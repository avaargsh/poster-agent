"""Optional local product cutout: explicit preview, source preservation and audit.

The rembg CLI is stubbed at the external process boundary in CI; this does not
attest real ONNX segmentation accuracy. Product rendering itself is real Pillow.
"""
from __future__ import annotations
from io import BytesIO
import hashlib
import json
from pathlib import Path
from subprocess import CompletedProcess

from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from app import storage
from app.main import app

client = TestClient(app)


def png(size=(180, 200), *, alpha=False):
    img = Image.new("RGBA", size, (250, 250, 250, 255))
    if alpha:
        ImageDraw.Draw(img).rectangle((35, 30, 140, 165), fill=(60, 125, 225, 255))
        for y in range(size[1]):
            for x in range(size[0]):
                if x < 35 or x > 140 or y < 30 or y > 165:
                    img.putpixel((x, y), (0, 0, 0, 0))
    out = BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


def test_default_create_still_uses_original_and_no_cutout_asset(tmp_path, monkeypatch):
    monkeypatch.setenv("POSTER_DATA_DIR", str(tmp_path / "posters"))
    original = png()
    response = client.post("/api/posters",
        data={"title": "测试商品", "price": "100"},
        files={"product": ("product.png", original, "image/png")})
    assert response.status_code == 201, response.text
    record = response.json()
    assert record["spec"]["product_source_mode"] == "original"
    assert record["product_cutout_available"] is False
    assets = storage.poster_dir(record["poster_id"]) / "assets"
    assert (assets / "product.png").is_file()
    assert not (assets / "product_cutout.png").exists()


def test_selected_cutout_preserves_original_and_revision_can_switch(tmp_path, monkeypatch):
    monkeypatch.setenv("POSTER_DATA_DIR", str(tmp_path / "posters"))
    original, cutout = png(), png(alpha=True)
    response = client.post("/api/posters",
        data={"title": "测试商品", "price": "100", "product_source_mode": "cutout"},
        files={
            "product": ("source.png", original, "image/png"),
            "product_cutout": ("removed.png", cutout, "image/png"),
        })
    assert response.status_code == 201, response.text
    record = response.json()
    pid = record["poster_id"]
    assert record["product_cutout_available"] is True
    assert record["spec"]["product_source_mode"] == "cutout"
    assets = storage.poster_dir(pid) / "assets"
    assert (assets / "product.png").read_bytes() != (assets / "product_cutout.png").read_bytes()
    with Image.open(assets / "product.png") as saved:
        assert saved.getpixel((0, 0))[3] == 255
    with Image.open(assets / "product_cutout.png") as saved:
        assert saved.getpixel((0, 0))[3] == 0
    old_png = client.get(record["image_url"]).content
    switched = client.post(f"/api/posters/{pid}/revisions",
        json={"expected_version": 1, "product_source_mode": "original"})
    assert switched.status_code == 201, switched.text
    v2 = switched.json()
    assert v2["version"] == 2
    assert v2["spec"]["product_source_mode"] == "original"
    assert client.get(v2["image_url"]).content != old_png
    assert client.get(record["image_url"]).content == old_png
    assert client.post(f"/api/posters/{pid}/revisions",
        json={"expected_version": 1, "product_source_mode": "cutout"}).status_code == 409


def test_invalid_cutout_rejected_before_any_persistence(tmp_path, monkeypatch):
    monkeypatch.setenv("POSTER_DATA_DIR", str(tmp_path / "posters"))
    original = png()
    attempts = [
        ({"product_source_mode": "cutout"}, {}),
        ({"product_source_mode": "original"}, {
            "product_cutout": ("cutout.png", png(alpha=True), "image/png")}),
        ({"product_source_mode": "cutout"}, {
            "product_cutout": ("cutout.png", png(), "image/png")}),
        ({"product_source_mode": "cutout"}, {
            "product_cutout": ("cutout.png", png(size=(100, 100), alpha=True), "image/png")}),
    ]
    for data, other in attempts:
        r = client.post("/api/posters",
            data={"title": "测试商品", "price": "100", **data},
            files={"product": ("source.png", original, "image/png"), **other})
        assert r.status_code == 422, r.text
    assert not storage.root().exists()


def test_preview_fails_closed_without_explicit_local_model(tmp_path, monkeypatch):
    monkeypatch.delenv("POSTER_CUTOUT_REMBG_HOME", raising=False)
    monkeypatch.setenv("POSTER_DATA_DIR", str(tmp_path / "posters"))
    r = client.post("/api/product-cutouts/preview", files={
        "product": ("test.png", png(), "image/png")
    })
    assert r.status_code == 503
    assert not storage.root().exists()


def test_preview_uses_local_cli_with_preloaded_weight_and_validates_png(tmp_path, monkeypatch):
    from app import cutout_service
    home = tmp_path / "rembg"
    model = home / "models" / "u2netp" / "u2netp.onnx"
    model.parent.mkdir(parents=True)
    model.write_bytes(b"pretend-model-for-offline-stub")
    monkeypatch.setenv("POSTER_CUTOUT_REMBG_HOME", str(home))
    monkeypatch.setattr(cutout_service.shutil, "which", lambda name: "/usr/bin/rembg")
    runs = []
    def fake_run(argv, *, env, timeout, capture_output, check):
        assert argv[0] == "/usr/bin/rembg"
        assert argv[1:4] == ["i", "-m", "u2netp"]
        assert env["REMBG_HOME"] == str(home)
        assert "withoutbg" not in argv
        assert timeout <= 60
        Path(argv[-1]).write_bytes(png(alpha=True))
        runs.append(argv)
        return CompletedProcess(argv, 0, b"", b"")
    monkeypatch.setattr(cutout_service.subprocess, "run", fake_run)
    response = client.post("/api/product-cutouts/preview", files={
        "product": ("product.png", png(), "image/png")})
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "image/png"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-cutout-model"] == "u2netp"
    assert response.headers["x-cutout-sha256"] == hashlib.sha256(response.content).hexdigest()
    assert len(runs) == 1
    with Image.open(BytesIO(response.content)) as img:
        assert img.mode == "RGBA"
        assert img.getpixel((0, 0))[3] == 0


def test_ui_contains_explicit_preview_comparison_and_mode_selection():
    html = client.get("/").text
    for marker in (
        'id="cutoutPreviewButton"', 'id="cutoutOriginalPreview"',
        'id="cutoutResultPreview"', 'id="acceptCutout"',
        'id="editProductSourceMode"',
    ):
        assert marker in html
    assert "/api/product-cutouts/preview" in html
    assert "body.append('product_cutout'" in html
