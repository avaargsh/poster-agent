"""M11 square image-layer collision validation and deterministic safe repair.

No model calls, no background processing, and no quality attestation from OCR.
"""
import json

from fastapi.testclient import TestClient

from app.main import app
from app.schemas import PosterSpec
from app.compositor import compose, local_background, validate_square_layout
from app.layout_collision import inspect_square_layout, repair_square_layout
from app import storage

client = TestClient(app)

TITLE = "智能电动牙刷限时优惠购买新品即刻体验强劲清洁能力"


def spec_with_collision():
    return PosterSpec(
        title=TITLE, price="199", original_price="399",
        subtitle="高效清洁 · 品质生活", cta="查看商品",
        layout_id="hero", layer_layout={
            "schema_version": 1, "title_dy": 20, "price_dy": -8,
        },
    )


def test_safe_defaults_for_both_square_templates():
    for template in ("classic", "hero"):
        spec = PosterSpec(title="新品牙刷", price="199", layout_id=template)
        report = inspect_square_layout(spec)
        assert report["status"] == "PASS", report
        assert report["issues"] == []
        assert "heading" in report["rects"]
        assert "price" in report["rects"]


def test_two_line_hero_heading_conflicts_with_price_at_extreme_offsets():
    spec = spec_with_collision()
    report = inspect_square_layout(spec)
    assert report["status"] == "FAIL", report
    assert any({"heading", "price"} == set(item["layers"]) for item in report["issues"])
    assert any(item["code"] == "LAYER_COLLISION" for item in report["issues"])
    try:
        validate_square_layout(spec)
    except ValueError as exc:
        assert "碰撞" in str(exc)
    else:
        raise AssertionError("Unsafe layout passed validation")


def test_auto_repair_returns_bounded_mutation_only_to_layer_offsets():
    original = spec_with_collision()
    repaired = repair_square_layout(original)
    assert repaired["status"] == "REPAIRED", repaired
    assert repaired["after"]["status"] == "PASS"
    assert repaired["before"]["status"] == "FAIL"
    assert original.layer_layout.title_dy == 20
    assert original.layer_layout.price_dy == -8
    copy = original.model_copy(update={
        "layer_layout": original.layer_layout.model_copy(
            update=repaired["proposed_layer_layout"]
        )
    })
    assert inspect_square_layout(copy)["status"] == "PASS"
    assert copy.title == original.title and copy.price == original.price
    assert copy.layout_id == original.layout_id
    assert repaired["proposed_layer_layout"]["schema_version"] == 1


def test_invalid_hero_geometry_rejected_before_creating_assets(tmp_path, monkeypatch, product_bytes):
    monkeypatch.setenv("POSTER_DATA_DIR", str(tmp_path / "posters"))
    spec = spec_with_collision()
    fields = spec.model_dump(mode="json")
    result = client.post("/api/posters",
        files={"product": ("sku.png", product_bytes, "image/png")},
        data={k: v for k, v in fields.items() if k != "layer_layout"} |
             {"layer_layout": json.dumps(fields["layer_layout"])},
    )
    assert result.status_code == 422, result.text
    assert "碰撞" in result.text
    assert not storage.root().exists()


def test_revision_collision_rejected_without_overwriting_existing_version(tmp_path, monkeypatch, product_bytes):
    monkeypatch.setenv("POSTER_DATA_DIR", str(tmp_path / "posters"))
    created = client.post("/api/posters",
        files={"product": ("sku.png", product_bytes, "image/png")},
        data={"title": TITLE, "price": "199", "layout_id": "hero"},
    )
    assert created.status_code == 201, created.text
    v1 = created.json()
    original_png = client.get(v1["image_url"]).content
    rejected = client.post(
        f"/api/posters/{v1['poster_id']}/revisions",
        json={"expected_version": 1, "subtitle": "高效清洁 · 品质生活",
              "layer_layout": {"title_dy": 20, "price_dy": -8}},
    )
    assert rejected.status_code == 422, rejected.text
    assert storage.view(v1["poster_id"])["version"] == 1
    assert client.get(v1["image_url"]).content == original_png


def test_inspection_endpoint_is_pure_and_returns_safe_repair(tmp_path, monkeypatch):
    monkeypatch.setenv("POSTER_DATA_DIR", str(tmp_path / "posters"))
    spec = spec_with_collision()
    r = client.post("/api/layout/inspect", json=spec.model_dump(mode="json"))
    assert r.status_code == 200, r.text
    report = r.json()
    assert report["status"] == "FAIL"
    assert report["repair"]["status"] == "REPAIRED"
    assert report["repair"]["after"]["status"] == "PASS"
    assert not storage.root().exists()


def test_browser_surfaces_inspection_and_one_click_repair_before_submission():
    html = client.get("/").text
    for marker in ('id="checkCreateLayout"', 'id="fixCreateLayout"',
                   'id="checkEditLayout"', 'id="fixEditLayout"',
                   'id="createLayoutStatus"', 'id="editLayoutStatus"'):
        assert marker in html
    assert "/api/layout/inspect" in html
    assert "writeLayerLayout('create'," in html
    assert "writeLayerLayout('edit'," in html
