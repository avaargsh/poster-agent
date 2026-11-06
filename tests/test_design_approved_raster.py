"""Protected current-approved SVG raster output is a content-bound input, never OCR evidence."""

import hashlib
import json
import uuid
from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from PIL import Image

import app.designs.rasterization as rasterization
from app.main import app


@pytest.fixture
def design_paths(tmp_path, monkeypatch):
    monkeypatch.setenv("POSTER_DATA_DIR", str(tmp_path / "posters"))
    monkeypatch.setenv("POSTER_EXECUTION_DB", str(tmp_path / "executions.sqlite3"))
    monkeypatch.setenv("POSTER_CHECKPOINT_DB", str(tmp_path / "checkpoints.sqlite3"))
    monkeypatch.setenv("POSTER_DESIGN_DB", str(tmp_path / "designs.sqlite3"))
    monkeypatch.setenv("POSTER_DESIGN_ASSET_DIR", str(tmp_path / "design-assets"))
    monkeypatch.delenv("POSTER_DESIGN_REVIEWERS_JSON", raising=False)


def _png(size=(1024, 1024)):
    with BytesIO() as buffer:
        Image.new("RGB", size, (20, 30, 40)).save(buffer, format="PNG")
        return buffer.getvalue()


def _create(client, product_bytes):
    response = client.post(
        "/api/designs",
        data={"title": "双十一牙刷", "price": "199.90", "cta": "立即抢购"},
        files={"product": ("product.png", product_bytes, "image/png")},
    )
    assert response.status_code == 201, response.text
    return response.json()["document"]["document_id"]


def _approve(client, doc, *, token=None):
    target = client.get(f"/api/designs/{doc}/approval-target").json()
    response = client.post(
        f"/api/designs/{doc}/approvals",
        json={
            "approval_id": "approved-raster-" + uuid.uuid4().hex,
            "expected_revision": target["revision"],
            "spec_digest": target["spec_digest"],
            "asset_manifest_digest": target["asset_manifest_digest"],
        },
        headers={"Authorization": f"Bearer {token}"} if token else {},
    )
    assert response.status_code == 201, response.text


def test_raster_source_requires_approval_and_returns_hash_bound_pixels(
    design_paths, product_bytes, monkeypatch
):
    calls = []
    raster_bytes = _png()

    def local_raster(svg_bytes, width, height):
        calls.append((svg_bytes, width, height))
        return raster_bytes, "test-raster/1"

    monkeypatch.setattr(rasterization, "_convert_svg_to_png", local_raster)
    with TestClient(app) as client:
        doc = _create(client, product_bytes)
        url = f"/api/designs/{doc}/revisions/1/approved.png"
        not_approved = client.get(url)
        assert not_approved.status_code == 409
        assert not_approved.json()["error"]["code"] == "APPROVAL_REQUIRED"
        assert calls == []

        _approve(client, doc)
        svg = client.get(f"/api/designs/{doc}/revisions/1/approved.svg")
        response = client.get(url)
        assert response.status_code == 200, response.text
        assert response.content == raster_bytes
        assert response.headers["content-type"].startswith("image/png")
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["x-approved-svg-sha256"] == hashlib.sha256(svg.content).hexdigest()
        assert response.headers["x-raster-png-sha256"] == hashlib.sha256(raster_bytes).hexdigest()
        assert response.headers["x-design-rasterizer"] == "test-raster/1"
        assert response.headers["x-design-ocr-status"] == "NOT_RUN"
        assert response.headers["x-design-release-ready"] == "false"
        assert calls[0][1:] == (1024, 1024)
        assert len(calls) == 1

        modified = client.post(
            f"/api/designs/{doc}/commands",
            json={
                "command_id": "approved-raster-rev-change-0001",
                "expected_revision": 1,
                "actor": "user",
                "operations": [{"op": "set_text", "node_id": "title", "text": "新标题"}],
            },
        )
        assert modified.status_code == 200, modified.text
        stale = client.get(url)
        assert stale.status_code == 409
        assert stale.json()["error"]["code"] == "REVISION_CONFLICT"
        assert len(calls) == 1


def test_revision_change_during_rasterization_is_not_returned(
    design_paths, product_bytes, monkeypatch
):
    with TestClient(app) as client:
        doc = _create(client, product_bytes)
        _approve(client, doc)
        url = f"/api/designs/{doc}/revisions/1/approved.png"

        def racing_raster(svg_bytes, width, height):
            changed = client.post(
                f"/api/designs/{doc}/commands",
                json={
                    "command_id": "approved-raster-inflight-0001",
                    "expected_revision": 1,
                    "actor": "user",
                    "operations": [{"op": "set_text", "node_id": "title", "text": "并发编辑"}],
                },
            )
            assert changed.status_code == 200, changed.text
            return _png(), "test-raster/1"

        monkeypatch.setattr(rasterization, "_convert_svg_to_png", racing_raster)
        response = client.get(url)
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "REVISION_CONFLICT"


def test_raster_fails_closed_on_invalid_output_and_unavailable_engine(
    design_paths, product_bytes, monkeypatch
):
    with TestClient(app) as client:
        doc = _create(client, product_bytes)
        _approve(client, doc)
        url = f"/api/designs/{doc}/revisions/1/approved.png"
        for invalid_bytes in (b"not a png", _png(size=(900, 900))):
            monkeypatch.setattr(
                rasterization,
                "_convert_svg_to_png",
                lambda svg, width, height, data=invalid_bytes: (data, "test-raster/1"),
            )
            response = client.get(url)
            assert response.status_code == 503
            assert response.json()["error"]["code"] == "DESIGN_RASTER_INVALID"

        def unavailable(svg, width, height):
            raise rasterization.RasterUnavailable("optional raster engine not installed")

        monkeypatch.setattr(rasterization, "_convert_svg_to_png", unavailable)
        response = client.get(url)
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "DESIGN_RASTER_UNAVAILABLE"


def test_raster_export_inherits_reviewer_scope(design_paths, product_bytes, monkeypatch):
    approver = "raster-approver-" + "A" * 40
    exporter = "raster-exporter-" + "B" * 40
    monkeypatch.setenv(
        "POSTER_DESIGN_REVIEWERS_JSON",
        json.dumps(
            {
                "approver-one": {
                    "token_sha256": hashlib.sha256(approver.encode()).hexdigest(),
                    "scopes": ["approve"],
                },
                "exporter-one": {
                    "token_sha256": hashlib.sha256(exporter.encode()).hexdigest(),
                    "scopes": ["export"],
                },
            }
        ),
    )
    monkeypatch.setattr(
        rasterization,
        "_convert_svg_to_png",
        lambda svg, width, height: (_png(), "test-raster/1"),
    )
    with TestClient(app) as client:
        doc = _create(client, product_bytes)
        _approve(client, doc, token=approver)
        url = f"/api/designs/{doc}/revisions/1/approved.png"
        assert client.get(url).status_code == 401
        assert client.get(url, headers={"Authorization": f"Bearer {approver}"}).status_code == 403
        assert client.get(url, headers={"Authorization": f"Bearer {exporter}"}).status_code == 200
