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
    # Native 4:5 exports must use each immutable version's selected source.
    v1_native = client.get(f"/api/posters/{pid}/versions/1/exports/feed_4x5_native")
    v2_native = client.get(f"/api/posters/{pid}/versions/2/exports/feed_4x5_native")
    assert v1_native.status_code == 200 and v2_native.status_code == 200
    assert v1_native.content != v2_native.content
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
    monkeypatch.setenv("POSTER_CUTOUT_MODEL_SHA256", hashlib.sha256(
        model.read_bytes()).hexdigest())
    monkeypatch.setattr(cutout_service.shutil, "which", lambda name: "/usr/bin/rembg")
    runs = []
    def fake_run(argv, *, env, timeout, capture_output, check):
        assert argv[0] == "/usr/bin/rembg"
        assert argv[1:4] == ["i", "-m", "u2netp"]
        assert env["REMBG_HOME"] == str(home)
        assert env["MODEL_CHECKSUM_DISABLED"] == "1"
        assert env["HTTPS_PROXY"].endswith(":9")
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



def test_batch_accepts_only_explicit_cutout_and_reuses_published_poster(tmp_path, monkeypatch):
    import csv
    from scripts import real_sku_batch

    monkeypatch.setenv("POSTER_DATA_DIR", str(tmp_path / "posters"))
    monkeypatch.setenv("POSTER_EXECUTION_DB", str(tmp_path / "executions.sqlite3"))
    folder = tmp_path / "input"
    folder.mkdir()
    (folder / "source.png").write_bytes(png())
    (folder / "cutout.png").write_bytes(png(alpha=True))
    path = folder / "products.csv"
    with path.open("w", encoding="utf-8", newline="") as out:
        rows = csv.DictWriter(out, fieldnames=[
            "sku_id", "product_image", "product_cutout_image", "title", "price",
        ])
        rows.writeheader()
        rows.writerow({
            "sku_id": "sku-01", "product_image": "source.png",
            "product_cutout_image": "cutout.png",
            "title": "智能好物", "price": "99",
        })
    sha, prepared = real_sku_batch.preflight(path)
    assert len(sha) == 64
    assert prepared[0]["data"]["product_source_mode"] == "cutout"
    assert prepared[0]["cutout_sha256"] == hashlib.sha256(png(alpha=True)).hexdigest()
    output = tmp_path / "result"
    created = real_sku_batch.run_batch(path, output, apply=True)
    assert created["created_or_reused"] == 1
    ident = created["poster_ids"]["sku-01"]
    assert storage.view(ident)["spec"]["product_source_mode"] == "cutout"
    assert (storage.poster_dir(ident) / "assets" / "product_cutout.png").is_file()
    second = real_sku_batch.run_batch(path, output, apply=True)
    assert second["poster_ids"] == created["poster_ids"]
    assert len(list(storage.root().iterdir())) == 1
    # The saved cutout is part of the batch integrity attestation.
    from app.batch_artifact_integrity import (
        BatchArtifactIntegrityError, verify_batch_artifact,
    )
    ledger = json.loads((output / "batch_import_results.json").read_text())
    verify_batch_artifact(ident, ledger["rows"]["sku-01"])
    asset = storage.poster_dir(ident) / "assets" / "product_cutout.png"
    original_cutout_bytes = asset.read_bytes()
    asset.write_bytes(b"corrupted-png")
    try:
        verify_batch_artifact(ident, ledger["rows"]["sku-01"])
    except BatchArtifactIntegrityError:
        pass
    else:
        raise AssertionError("Tampered cutout passed batch receipt verification")
    asset.write_bytes(original_cutout_bytes)
    verify_batch_artifact(ident, ledger["rows"]["sku-01"])


def test_batch_rejects_opaque_cutout_before_creating_any_poster(tmp_path, monkeypatch):
    import csv
    from scripts import real_sku_batch

    monkeypatch.setenv("POSTER_DATA_DIR", str(tmp_path / "posters"))
    folder = tmp_path / "input"
    folder.mkdir()
    (folder / "source.png").write_bytes(png())
    (folder / "cutout.png").write_bytes(png())
    path = folder / "products.csv"
    with path.open("w", encoding="utf-8", newline="") as out:
        writer = csv.DictWriter(out, fieldnames=[
            "sku_id", "product_image", "product_cutout_image", "title", "price",
        ])
        writer.writeheader()
        writer.writerow({
            "sku_id": "sku-01", "product_image": "source.png",
            "product_cutout_image": "cutout.png", "title": "好物", "price": "99",
        })
    try:
        real_sku_batch.preflight(path)
    except real_sku_batch.BatchError:
        pass
    else:
        raise AssertionError("Opaque PNG was incorrectly accepted as a cutout")
    assert not storage.root().exists()



def test_invalid_local_model_digest_never_invokes_rembg(tmp_path, monkeypatch):
    from app import cutout_service
    home = tmp_path / "rembg"
    model = home / "models" / "u2netp" / "u2netp.onnx"
    model.parent.mkdir(parents=True)
    model.write_bytes(b"pretend-model")
    monkeypatch.setenv("POSTER_CUTOUT_REMBG_HOME", str(home))
    monkeypatch.setenv("POSTER_CUTOUT_MODEL_SHA256", "0" * 64)
    monkeypatch.setattr(cutout_service.shutil, "which", lambda _: "/usr/bin/rembg")
    monkeypatch.setattr(
        cutout_service.subprocess, "run",
        lambda *a, **kw: (_ for _ in ()).throw(
            AssertionError("Unpinned model was executed")),
    )
    result = client.post("/api/product-cutouts/preview",
        files={"product": ("source.png", png(), "image/png")})
    assert result.status_code == 503
    assert "SHA-256" in result.text



def test_original_mode_idempotency_fingerprint_matches_pre_cutout_contract():
    from app.main import _fingerprint
    from app.schemas import PosterSpec
    spec = PosterSpec(title="旧海报", price="99")
    product = Image.new("RGBA", (120, 150), (60, 100, 180, 255))
    legacy_spec = spec.model_dump(mode="json")
    legacy_spec.pop("product_source_mode")
    digest = hashlib.sha256()
    digest.update(json.dumps(legacy_spec, sort_keys=True).encode("utf-8"))
    digest.update(f"{product.mode}:{product.width}x{product.height}".encode())
    digest.update(product.tobytes())
    digest.update(b"none")
    assert _fingerprint(spec, product, None) == digest.hexdigest()
    with_candidate = spec.model_copy(update={"product_source_mode": "cutout"})
    candidate = Image.new("RGBA", (120, 150), (0, 0, 0, 0))
    assert _fingerprint(with_candidate, product, None, candidate) != digest.hexdigest()
