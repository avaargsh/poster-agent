"""Fail-closed OCR target snapshot for the currently approved DesignDocument."""

import hashlib
import json
import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def design_paths(tmp_path, monkeypatch):
    monkeypatch.setenv("POSTER_DATA_DIR", str(tmp_path / "posters"))
    monkeypatch.setenv("POSTER_EXECUTION_DB", str(tmp_path / "executions.sqlite3"))
    monkeypatch.setenv("POSTER_CHECKPOINT_DB", str(tmp_path / "checkpoints.sqlite3"))
    monkeypatch.setenv("POSTER_DESIGN_DB", str(tmp_path / "designs.sqlite3"))
    monkeypatch.setenv("POSTER_DESIGN_ASSET_DIR", str(tmp_path / "design-assets"))
    monkeypatch.delenv("POSTER_DESIGN_REVIEWERS_JSON", raising=False)
    return tmp_path


def create(client, product_bytes):
    response = client.post(
        "/api/designs",
        data={"title": "双十一牙刷", "price": "199.90"},
        files={"product": ("toothbrush.png", product_bytes, "image/png")},
    )
    assert response.status_code == 201, response.text
    return response.json()["document"]["document_id"]


def approve(client, doc):
    target = client.get(f"/api/designs/{doc}/approval-target")
    assert target.status_code == 200
    d = target.json()
    response = client.post(
        f"/api/designs/{doc}/approvals",
        json={
            "approval_id": "ocr-target-approval-" + uuid.uuid4().hex,
            "expected_revision": d["revision"],
            "spec_digest": d["spec_digest"],
            "asset_manifest_digest": d["asset_manifest_digest"],
        },
    )
    assert response.status_code == 201, response.text
    return d


def error(response, code):
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == code


def test_ocr_target_requires_current_approved_revision_and_frozen_svg(
    design_paths, product_bytes
):
    with TestClient(app) as client:
        doc = create(client, product_bytes)
        url = f"/api/designs/{doc}/revisions/1/ocr-target"
        error(client.get(url), "APPROVAL_REQUIRED")
        approved = approve(client, doc)
        response = client.get(url)
        assert response.status_code == 200, response.text
        payload = response.json()
        svg = client.get(f"/api/designs/{doc}/revisions/1/approved.svg")
        assert svg.status_code == 200, svg.text
        assert payload["document_id"] == doc
        assert payload["revision"] == 1
        assert payload["spec_digest"] == approved["spec_digest"]
        assert payload["asset_manifest_digest"] == approved["asset_manifest_digest"]
        assert payload["renderer_version"] == approved["renderer_version"]
        assert payload["svg_sha256"] == hashlib.sha256(svg.content).hexdigest()
        assert payload["expected_fields"] == {
            "title": "双十一牙刷",
            "price": "¥199.90",
            "cta": "立即抢购",
        }
        assert payload["ocr_status"] == "NOT_RUN"
        assert payload["runtime_attested"] is False
        assert payload["quality_verified"] is False
        assert payload["release_ready"] is False
        assert response.headers["cache-control"] == "no-store"

        changed = client.post(
            f"/api/designs/{doc}/commands",
            json={
                "command_id": "cmd-ocr-target-rev-0001",
                "expected_revision": 1,
                "actor": "user",
                "operations": [{"op": "set_text", "node_id": "title", "text": "改动后的标题"}],
            },
        )
        assert changed.status_code == 200, changed.text
        error(client.get(url), "REVISION_CONFLICT")
        error(client.get(f"/api/designs/{doc}/revisions/2/ocr-target"), "APPROVAL_REQUIRED")
        approved2 = approve(client, doc)
        fresh = client.get(f"/api/designs/{doc}/revisions/2/ocr-target")
        assert fresh.status_code == 200
        assert fresh.json()["spec_digest"] == approved2["spec_digest"]
        assert fresh.json()["svg_sha256"] != payload["svg_sha256"]
        assert fresh.json()["release_ready"] is False


def test_ocr_target_inherits_approval_export_auth_scope(
    design_paths, product_bytes, monkeypatch
):
    reviewer = "reviewer-key-" + "R" * 40
    exporter = "exporter-key-" + "E" * 40
    monkeypatch.setenv(
        "POSTER_DESIGN_REVIEWERS_JSON",
        json.dumps(
            {
                "reviewer-one": {
                    "token_sha256": hashlib.sha256(reviewer.encode()).hexdigest(),
                    "scopes": ["approve"],
                },
                "exporter-one": {
                    "token_sha256": hashlib.sha256(exporter.encode()).hexdigest(),
                    "scopes": ["export"],
                },
            }
        ),
    )
    with TestClient(app) as client:
        doc = create(client, product_bytes)
        target = client.get(f"/api/designs/{doc}/approval-target").json()
        created = client.post(
            f"/api/designs/{doc}/approvals",
            json={
                "approval_id": "ocr-target-secure-0001",
                "expected_revision": target["revision"],
                "spec_digest": target["spec_digest"],
                "asset_manifest_digest": target["asset_manifest_digest"],
            },
            headers={"Authorization": f"Bearer {reviewer}"},
        )
        assert created.status_code == 201, created.text
        url = f"/api/designs/{doc}/revisions/1/ocr-target"
        assert client.get(url).status_code == 401
        assert client.get(url, headers={"Authorization": f"Bearer {reviewer}"}).status_code == 403
        response = client.get(url, headers={"Authorization": f"Bearer {exporter}"})
        assert response.status_code == 200, response.text
        assert response.json()["release_ready"] is False
