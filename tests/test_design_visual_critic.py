"""Visual critic shadow preview: untrusted observations cannot grant repair or release."""

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


def _fake_png():
    with BytesIO() as output:
        Image.new("RGB", (1024, 1024), (20, 30, 40)).save(output, format="PNG")
        return output.getvalue()


def _use_fake_raster(monkeypatch):
    raster_bytes = _fake_png()
    monkeypatch.setattr(
        rasterization,
        "_convert_svg_to_png",
        lambda svg, width, height: (raster_bytes, "test-raster/1"),
    )


def _create(client, product_bytes):
    created = client.post(
        "/api/designs",
        data={"title": "双十一牙刷", "price": "199.90", "cta": "立即抢购"},
        files={"product": ("product.png", product_bytes, "image/png")},
    )
    assert created.status_code == 201, created.text
    return created.json()["document"]["document_id"]


def _approve(client, doc, token=None):
    target = client.get(f"/api/designs/{doc}/approval-target").json()
    response = client.post(
        f"/api/designs/{doc}/approvals",
        json={
            "approval_id": "visual-critic-" + uuid.uuid4().hex,
            "expected_revision": target["revision"],
            "spec_digest": target["spec_digest"],
            "asset_manifest_digest": target["asset_manifest_digest"],
        },
        headers={"Authorization": f"Bearer {token}"} if token else {},
    )
    assert response.status_code == 201, response.text


def _payload(client, doc, *, headers=None):
    raster = client.get(f"/api/designs/{doc}/revisions/1/approved.png", headers=headers or {})
    assert raster.status_code == 200, raster.text
    return {
        "schema_version": 1,
        "svg_sha256": raster.headers["x-approved-svg-sha256"],
        "png_sha256": raster.headers["x-raster-png-sha256"],
        "findings": [
            {
                "finding_id": "contrast-1",
                "category": "contrast",
                "severity": "minor",
                "confidence": 0.72,
                "explanation": "标题与背景对比度不足",
                "region": {"x": 0.1, "y": 0.1, "width": 0.3, "height": 0.2},
                "suggested_action": "REVIEW_STYLE",
            }
        ],
    }


def test_preview_is_read_only_and_all_findings_unattested(design_paths, product_bytes, monkeypatch):
    _use_fake_raster(monkeypatch)
    with TestClient(app) as client:
        doc = _create(client, product_bytes)
        _approve(client, doc)
        payload = _payload(client, doc)
        url = f"/api/designs/{doc}/revisions/1/visual-critic-preview"
        first = client.post(url, json=payload)
        assert first.status_code == 200, first.text
        result = first.json()
        assert result["document_id"] == doc
        assert result["revision"] == 1
        assert result["png_sha256"] == payload["png_sha256"]
        assert result["svg_sha256"] == payload["svg_sha256"]
        assert result["critic_status"] == "DECLARED_FINDINGS_UNATTESTED"
        assert result["findings"] == payload["findings"]
        assert result["model_execution_attested"] is False
        assert result["quality_verified"] is False
        assert result["repair_authorized"] is False
        assert result["release_ready"] is False
        assert first.headers["cache-control"] == "no-store"
        assert first.headers["x-content-type-options"] == "nosniff"
        assert client.post(url, json=payload).json() == result

        # Different untrusted findings have different content hashes but no authority.
        payload["findings"][0]["confidence"] = 0.9
        changed = client.post(url, json=payload)
        assert changed.status_code == 200
        assert changed.json()["critic_fingerprint_sha256"] != result["critic_fingerprint_sha256"]
        assert changed.json()["repair_authorized"] is False
        assert client.get(f"/api/designs/{doc}").json()["document"]["revision"] == 1


def test_unapproved_stale_or_wrong_raster_source_is_blocked(
    design_paths, product_bytes, monkeypatch
):
    _use_fake_raster(monkeypatch)
    with TestClient(app) as client:
        doc = _create(client, product_bytes)
        url = f"/api/designs/{doc}/revisions/1/visual-critic-preview"
        minimal = {"svg_sha256": "a" * 64, "png_sha256": "b" * 64, "findings": []}
        not_approved = client.post(url, json=minimal)
        assert not_approved.status_code == 409
        assert not_approved.json()["error"]["code"] == "APPROVAL_REQUIRED"

        _approve(client, doc)
        payload = _payload(client, doc)
        for altered_key in ("png_sha256", "svg_sha256"):
            wrong = {**payload, altered_key: "f" * 64}
            response = client.post(url, json=wrong)
            assert response.status_code == 409
            assert response.json()["error"]["code"] == "VISUAL_SOURCE_MISMATCH"

        updated = client.post(
            f"/api/designs/{doc}/commands",
            json={
                "command_id": "visual-critic-stale-0001",
                "expected_revision": 1,
                "actor": "user",
                "operations": [{"op": "set_text", "node_id": "title", "text": "修改后"}],
            },
        )
        assert updated.status_code == 200, updated.text
        stale = client.post(url, json=payload)
        assert stale.status_code == 409
        assert stale.json()["error"]["code"] == "REVISION_CONFLICT"


def test_preview_rejects_forged_claims_and_invalid_regions(
    design_paths, product_bytes, monkeypatch
):
    _use_fake_raster(monkeypatch)
    with TestClient(app) as client:
        doc = _create(client, product_bytes)
        _approve(client, doc)
        payload = _payload(client, doc)
        url = f"/api/designs/{doc}/revisions/1/visual-critic-preview"
        invalid = [
            {**payload, "release_ready": True},
            {**payload, "repair_authorized": True},
            {**payload, "findings": [*payload["findings"], payload["findings"][0]]},
            {**payload, "findings": [{**payload["findings"][0], "category": "price"}]},
            {**payload, "findings": [{**payload["findings"][0], "confidence": "0.9"}]},
            {**payload, "findings": [{**payload["findings"][0], "explanation": "bad\ntext"}]},
            {
                **payload,
                "findings": [
                    {
                        **payload["findings"][0],
                        "region": {"x": 0.9, "y": 0.1, "width": 0.2, "height": 0.2},
                    }
                ],
            },
            {
                **payload,
                "findings": [
                    {
                        **payload["findings"][0],
                        "region": {"x": False, "y": 0.1, "width": 0.2, "height": 0.2},
                    }
                ],
            },
            {
                **payload,
                "findings": [
                    {
                        **payload["findings"][0],
                        "region": {"x": 0.1, "y": 0.1, "width": True, "height": 0.2},
                    }
                ],
            },
            {
                **payload,
                "findings": [
                    {
                        **payload["findings"][0],
                        "region": {"x": "0.1", "y": 0.1, "width": 0.2, "height": 0.2},
                    }
                ],
            },
            {**payload, "findings": payload["findings"] * 21},
        ]
        for submission in invalid:
            response = client.post(url, json=submission)
            assert response.status_code == 422, (submission, response.text)

        # Even an empty finding set is not a positive quality verdict.
        empty = client.post(url, json={**payload, "findings": []})
        assert empty.status_code == 200
        assert empty.json()["quality_verified"] is False
        assert empty.json()["release_ready"] is False


def test_preview_rechecks_revision_after_rasterization(design_paths, product_bytes, monkeypatch):
    _use_fake_raster(monkeypatch)
    with TestClient(app) as client:
        doc = _create(client, product_bytes)
        _approve(client, doc)
        payload = _payload(client, doc)

        def racing_raster(svg_bytes, width, height):
            changed = client.post(
                f"/api/designs/{doc}/commands",
                json={
                    "command_id": "visual-critic-inflight-0001",
                    "expected_revision": 1,
                    "actor": "user",
                    "operations": [{"op": "set_text", "node_id": "title", "text": "并发修改"}],
                },
            )
            assert changed.status_code == 200, changed.text
            return _fake_png(), "test-raster/1"

        monkeypatch.setattr(rasterization, "_convert_svg_to_png", racing_raster)
        response = client.post(
            f"/api/designs/{doc}/revisions/1/visual-critic-preview", json=payload
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "REVISION_CONFLICT"


def test_preview_obeys_export_scope(design_paths, product_bytes, monkeypatch):
    approver = "critic-approver-" + "A" * 40
    exporter = "critic-exporter-" + "B" * 40
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
    _use_fake_raster(monkeypatch)
    with TestClient(app) as client:
        doc = _create(client, product_bytes)
        _approve(client, doc, token=approver)
        url = f"/api/designs/{doc}/revisions/1/visual-critic-preview"
        payload = _payload(client, doc, headers={"Authorization": f"Bearer {exporter}"})
        assert client.post(url, json=payload).status_code == 401
        assert (
            client.post(
                url, json=payload, headers={"Authorization": f"Bearer {approver}"}
            ).status_code
            == 403
        )
        allowed = client.post(url, json=payload, headers={"Authorization": f"Bearer {exporter}"})
        assert allowed.status_code == 200, allowed.text
        assert allowed.json()["repair_authorized"] is False
