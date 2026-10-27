"""M8 square layout variants: selectable, versioned, deterministic and isolated.

This tests the product-facing create/edit workflow, not real SKU acceptance.
"""
from io import BytesIO

from fastapi.testclient import TestClient
from PIL import Image

from app import storage
from app.compositor import compose, local_background, validate_square_layout
from app.main import app
from app.schemas import PosterSpec

client = TestClient(app)


def test_classic_default_matches_explicit_settings():
    product = Image.new("RGBA", (180, 320), (50, 160, 240, 255))
    bg = local_background("蓝色科技风")
    legacy = PosterSpec(title="日常好物", price="129")
    explicit = PosterSpec(title="日常好物", price="129",
                          layout_id="classic", product_align="center", product_scale="full")
    assert compose(legacy, product, None, bg) == compose(explicit, product, None, bg)


def test_hero_variant_and_product_geometry_produce_distinct_full_size_images():
    product = Image.new("RGBA", (180, 320), (140, 210, 80, 255))
    bg = local_background("蓝色科技风")
    base = dict(title="电动牙刷", price="199", subtitle="高效清洁",
                original_price="399", cta="查看详情")
    layouts = [
        PosterSpec(**base, layout_id="classic"),
        PosterSpec(**base, layout_id="hero"),
        PosterSpec(**base, layout_id="hero", product_align="top", product_scale="compact"),
        PosterSpec(**base, layout_id="hero", product_align="bottom", product_scale="full"),
        PosterSpec(**base, layout_id="classic", product_align="top", product_scale="full"),
        PosterSpec(**base, layout_id="classic", product_align="bottom", product_scale="full"),
    ]
    results = [compose(spec, product, None, bg) for spec in layouts]
    assert len(set(results)) == len(results)
    for blob in results:
        with Image.open(BytesIO(blob)) as image:
            assert image.format == "PNG"
            assert image.size == (1024, 1024)


def test_create_and_edit_preserve_assets_and_versioned_layout(tmp_path, monkeypatch, product_bytes):
    monkeypatch.setenv("POSTER_DATA_DIR", str(tmp_path / "posters"))
    monkeypatch.setattr("app.main.generate_ai_background",
                        lambda *_args, **_kwargs: (_ for _ in ()).throw(
                            AssertionError("layout edit must never call AI")))
    created = client.post(
        "/api/posters", files={"product": ("sku.png", product_bytes, "image/png")},
        data={"title": "电动牙刷", "price": "199", "layout_id": "hero",
              "product_align": "top", "product_scale": "compact",
              "background_mode": "local"},
    )
    assert created.status_code == 201, created.text
    first = created.json()
    pid = first["poster_id"]
    first_png = client.get(first["image_url"]).content
    assert first["spec"]["layout_id"] == "hero"
    assert first["spec"]["product_align"] == "top"
    assert first["spec"]["product_scale"] == "compact"
    assets = {
        key: (storage.poster_dir(pid) / "assets" / key).read_bytes()
        for key in ("product.png", "background.png")
    }

    revised = client.post(
        f"/api/posters/{pid}/revisions",
        json={"expected_version": 1, "layout_id": "classic",
              "product_align": "bottom", "product_scale": "full"},
    )
    assert revised.status_code == 201, revised.text
    updated = revised.json()
    assert updated["version"] == 2
    assert updated["spec"]["layout_id"] == "classic"
    assert updated["spec"]["product_align"] == "bottom"
    assert client.get(updated["image_url"]).content != first_png
    assert client.get(first["image_url"]).content == first_png
    old = client.get(f"/api/posters/{pid}/versions/1").json()
    assert old["spec"]["layout_id"] == "hero"
    assert all((storage.poster_dir(pid) / "assets" / key).read_bytes() == old_data
               for key, old_data in assets.items())
    stale = client.post(
        f"/api/posters/{pid}/revisions",
        json={"expected_version": 1, "layout_id": "hero"},
    )
    assert stale.status_code == 409


def test_invalid_layout_input_is_rejected_before_asset_creation(tmp_path, monkeypatch, product_bytes):
    monkeypatch.setenv("POSTER_DATA_DIR", str(tmp_path / "posters"))
    for field, bad in (("layout_id", "freeform"), ("product_scale", "huge"),
                       ("product_align", "../../outside")):
        created = client.post(
            "/api/posters",
            files={"product": ("sku.png", product_bytes, "image/png")},
            data={"title": "电动牙刷", "price": "199", field: bad},
        )
        assert created.status_code == 422, created.text
    assert not storage.root().exists()


def test_layout_edit_can_only_change_known_enumerations(tmp_path, monkeypatch, product_bytes):
    monkeypatch.setenv("POSTER_DATA_DIR", str(tmp_path / "posters"))
    created = client.post(
        "/api/posters",
        files={"product": ("sku.png", product_bytes, "image/png")},
        data={"title": "电动牙刷", "price": "199"},
    )
    pid = created.json()["poster_id"]
    for invalid in ("layout_id", "product_align", "product_scale"):
        response = client.post(
            f"/api/posters/{pid}/revisions",
            json={"expected_version": 1, invalid: "unknown"},
        )
        assert response.status_code == 422, response.text
    assert storage.view(pid)["version"] == 1


def test_existing_version_specs_without_layout_fields_load_as_classic():
    spec = PosterSpec.model_validate({"title": "老海报", "price": "99"})
    assert spec.layout_id == "classic"
    assert spec.product_align == "center"
    assert spec.product_scale == "full"
    validate_square_layout(spec)


def test_web_editor_exposes_layout_and_product_position_as_versioned_fields():
    html = client.get("/").text
    for marker in (
        'id="create-layout_id"', 'name="layout_id"',
        'id="create-product_align"', 'name="product_align"',
        'id="create-product_scale"', 'name="product_scale"',
        'id="editLayout"', 'id="editProductAlign"', 'id="editProductScale"',
    ):
        assert marker in html
    assert "layout_id:$('editLayout').value" in html
    assert "product_align:$('editProductAlign').value" in html
    assert "product_scale:$('editProductScale').value" in html
    assert "data.spec.layout_id ?? 'classic'" in html


def test_approved_brief_disallows_unapproved_layout_change(tmp_path, monkeypatch, product_bytes):
    monkeypatch.setenv("POSTER_CHECKPOINT_DB", str(tmp_path / "checkpoints.sqlite3"))
    monkeypatch.setenv("POSTER_DATA_DIR", str(tmp_path / "posters"))
    run = client.post(
        "/api/briefs/runs",
        json={"brief": "主标题：电动牙刷，活动价199元，原价399元", "compiler": "demo"},
    )
    assert run.status_code == 201, run.text
    run_id = run.json()["run_id"]
    confirmed_spec = {
        "title": "电动牙刷", "subtitle": "品质生活 · 限时优惠",
        "price": "199", "original_price": "399", "style": "蓝色科技风",
        "cta": "立即抢购", "background_mode": "local",
    }
    decision = client.post(
        f"/api/briefs/runs/{run_id}/decision",
        json={"approved": True, "spec": confirmed_spec},
    )
    assert decision.status_code == 200, decision.text
    data = {**confirmed_spec, "review_run_id": run_id}
    files = {"product": ("sku.png", product_bytes, "image/png")}
    changed = client.post("/api/posters", files=files, data={
        **data, "layout_id": "hero",
    })
    assert changed.status_code == 409, changed.text
    assert not storage.root().exists()
    legacy = client.post("/api/posters", files=files, data=data)
    assert legacy.status_code == 201, legacy.text
    assert legacy.json()["spec"]["layout_id"] == "classic"
