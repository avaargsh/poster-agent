"""M9: independently adjustable but bounded square template layers.

Synthetic sources and OCR; acceptance depends on real model and human review.
"""
from io import BytesIO
import json

from fastapi.testclient import TestClient
from PIL import Image

from app import storage
from app.compositor import compose, local_background
from app.main import app
from app.ocr_price_layout import PRICE_ZONES, assess_price_zones
from app.schemas import PosterSpec

client = TestClient(app)


def test_old_specs_render_identical_zero_offset_layers():
    source = {"title": "电动牙刷", "subtitle": "品牌新品", "price": "199",
              "original_price": "399", "cta": "马上购买"}
    sku = Image.new("RGBA", (250, 370), (85, 170, 245, 255))
    bg = local_background("蓝色科技风")
    first = PosterSpec(**source)
    second = PosterSpec(**source, layer_layout={"schema_version": 1})
    assert compose(first, sku, None, bg) == compose(second, sku, None, bg)
    assert first.layer_layout.model_dump()["price_dx"] == 0
    assert first.layer_layout.schema_version == 1


def test_independent_offsets_change_real_composited_pixels():
    sku = Image.new("RGBA", (300, 500), (85, 170, 245, 255))
    logo = Image.new("RGBA", (140, 90), (230, 205, 90, 255))
    bg = local_background("蓝色科技风")
    source = dict(title="电动牙刷", price="199", original_price="399")
    for template in ("classic", "hero"):
        normal = compose(PosterSpec(**source, layout_id=template), sku, logo, bg)
        changed = compose(PosterSpec(
            **source, layout_id=template,
            layer_layout={
                "schema_version": 1, "title_dx": 10, "title_dy": -12,
                "price_dx": 8, "price_dy": 6,
                "cta_dx": -12, "cta_dy": 9, "logo_dx": -10, "logo_dy": 10,
            },
        ), sku, logo, bg)
        assert normal != changed
        with Image.open(BytesIO(changed)) as image:
            assert image.size == (1024, 1024)


def test_create_edit_and_history_bind_layer_layout_to_saved_version(tmp_path, monkeypatch, product_bytes):
    monkeypatch.setenv("POSTER_DATA_DIR", str(tmp_path / "posters"))
    create_layout = {"schema_version": 1, "title_dx": 10, "logo_dx": -8}
    created = client.post(
        "/api/posters",
        files={"product": ("sku.png", product_bytes, "image/png")},
        data={"title": "电动牙刷", "price": "199", "layout_id": "hero",
              "layer_layout": json.dumps(create_layout)},
    )
    assert created.status_code == 201, created.text
    v1 = created.json()
    assert v1["spec"]["layer_layout"]["title_dx"] == 10
    old_png = client.get(v1["image_url"]).content
    revised = client.post(
        f"/api/posters/{v1['poster_id']}/revisions",
        json={"expected_version": 1, "layer_layout": {
            "schema_version": 1, "price_dx": 11, "price_dy": -6,
            "cta_dx": 8, "cta_dy": -9,
        }},
    )
    assert revised.status_code == 201, revised.text
    v2 = revised.json()
    assert v2["version"] == 2
    assert v2["spec"]["layer_layout"]["price_dx"] == 11
    assert v2["spec"]["layer_layout"]["title_dx"] == 0
    assert client.get(v2["image_url"]).content != old_png
    old = client.get(f"/api/posters/{v1['poster_id']}/versions/1").json()
    assert old["spec"]["layer_layout"]["title_dx"] == 10
    assert client.get(old["image_url"]).content == old_png
    assert client.post(
        f"/api/posters/{v1['poster_id']}/revisions",
        json={"expected_version": 1, "layer_layout": {"price_dx": 4}},
    ).status_code == 409


def test_reject_unsafe_offsets_unknown_keys_and_schema_version(tmp_path, monkeypatch, product_bytes):
    monkeypatch.setenv("POSTER_DATA_DIR", str(tmp_path / "posters"))
    files = {"product": ("sku.png", product_bytes, "image/png")}
    for candidate in (
        {"title_dx": 999}, {"price_dy": -50},
        {"logo_dx": 31}, {"cta_dy": 20},
        {"price_dx": True}, {"logo_dy": 1.2},
        {"schema_version": 2}, {"unexpected": 10},
    ):
        result = client.post("/api/posters", files=files, data={
            "title": "商品", "price": "199", "layer_layout": json.dumps(candidate),
        })
        assert result.status_code == 422, (candidate, result.text)
    assert not storage.root().exists()


def test_non_object_malformed_and_large_layer_json_fail_closed(tmp_path, monkeypatch, product_bytes):
    monkeypatch.setenv("POSTER_DATA_DIR", str(tmp_path / "posters"))
    for raw in ("[1,2]", "{", "null", '"unexpected"', "{" + " " * 5000 + "}"):
        result = client.post("/api/posters",
            files={"product": ("sku.png", product_bytes, "image/png")},
            data={"title": "商品", "price": "199", "layer_layout": raw})
        assert result.status_code == 422, (raw[:30], result.text)
    assert not storage.root().exists()


def test_ocr_price_zones_follow_saved_price_layer_offsets():
    spec = PosterSpec(title="商品", price="199", layout_id="classic",
                      layer_layout={"price_dx": 10, "price_dy": 6})
    lines = [{"text": "¥199", "bbox": [85, 680, 280, 752], "score": 0.99}]
    checks = {c["name"]: c for c in assess_price_zones(spec, "OK", lines)}
    assert checks["ocr_price_position"]["evidence"]["zone_xyxy"] == [
        55, 651, 522, 796
    ]
    assert checks["ocr_price_position"]["status"] == "PASS"
    assert PRICE_ZONES["price"] == (45, 645, 512, 790)


def test_ui_exposes_individual_layers_and_revision_reload():
    html = client.get("/").text
    for key in ("title_dx", "title_dy", "price_dx", "price_dy",
                "cta_dx", "cta_dy", "logo_dx", "logo_dy"):
        assert 'id="edit-layer-' + key + '"' in html
        assert 'id="create-layer-' + key + '"' in html
    assert "layer_layout:readLayerLayout('edit')" in html
    assert "layer_layout:readLayerLayout('create')" in html
    assert "writeLayerLayout('edit',data.spec.layer_layout)" in html


def test_approved_brief_must_include_exact_confirmed_layer_positions(tmp_path, monkeypatch, product_bytes):
    monkeypatch.setenv("POSTER_CHECKPOINT_DB", str(tmp_path / "checkpoint.sqlite3"))
    monkeypatch.setenv("POSTER_DATA_DIR", str(tmp_path / "posters"))
    new = client.post("/api/briefs/runs", json={
        "brief": "主标题：电动牙刷，活动价199元，原价399元",
        "compiler": "demo",
    })
    assert new.status_code == 201, new.text
    rid = new.json()["run_id"]
    approved = {
        "title": "电动牙刷", "subtitle": "促销活动",
        "price": "199", "original_price": "399",
        "style": "蓝色科技风", "cta": "立即抢购",
        "background_mode": "local", "layer_layout": {
            "schema_version": 1, "title_dx": 10, "price_dy": -6,
        },
    }
    decision = client.post(f"/api/briefs/runs/{rid}/decision", json={
        "approved": True, "spec": approved,
    })
    assert decision.status_code == 200, decision.text
    files = {"product": ("sku.png", product_bytes, "image/png")}
    base_data = {key: value for key, value in approved.items() if key != "layer_layout"}
    base_data["review_run_id"] = rid
    # A changed coordinate cannot reuse an approval for a different layout.
    tampered = client.post("/api/posters", files=files, data={
        **base_data, "layer_layout": json.dumps({"title_dx": 11, "price_dy": -6}),
    })
    assert tampered.status_code == 409, tampered.text
    assert not storage.root().exists()
    valid = client.post("/api/posters", files=files, data={
        **base_data, "layer_layout": json.dumps(approved["layer_layout"]),
    })
    assert valid.status_code == 201, valid.text
    assert valid.json()["spec"]["layer_layout"]["title_dx"] == 10
