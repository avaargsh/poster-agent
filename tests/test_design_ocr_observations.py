"""Unattested OCR observation comparison cannot turn into release authorization."""

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


def create_approved(client, product_bytes, *, token=None):
    created = client.post(
        "/api/designs",
        data={"title": "双十一牙刷", "price": "199.90"},
        files={"product": ("product.png", product_bytes, "image/png")},
    )
    assert created.status_code == 201, created.text
    doc = created.json()["document"]["document_id"]
    target = client.get(f"/api/designs/{doc}/approval-target").json()
    approved = client.post(
        f"/api/designs/{doc}/approvals",
        json={
            "approval_id": "ocr-observations-" + uuid.uuid4().hex,
            "expected_revision": target["revision"],
            "spec_digest": target["spec_digest"],
            "asset_manifest_digest": target["asset_manifest_digest"],
        },
        headers={"Authorization": f"Bearer {token}"} if token else {},
    )
    assert approved.status_code == 201, approved.text
    return doc


def test_ocr_preview_match_and_false_price_prefix_fail_closed(design_paths, product_bytes):
    with TestClient(app) as client:
        doc = create_approved(client, product_bytes)
        url = f"/api/designs/{doc}/revisions/1/ocr-evaluation-preview"
        observed = {
            "lines": [
                {"text": "双十一牙刷", "score": 0.98},
                {"text": "¥1990.00", "score": 0.99},
                {"text": "立即抢购", "score": 0.97},
            ]
        }
        response = client.post(url, json=observed)
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["ocr_status"] == "OBSERVATIONS_UNATTESTED"
        assert result["runtime_attested"] is False
        assert result["quality_verified"] is False
        assert result["release_ready"] is False
        assert result["declared_consistency"] == "BLOCKED"
        by_field = {check["field"]: check for check in result["field_checks"]}
        assert by_field["title"]["status"] == "MATCHED"
        assert by_field["price"]["status"] == "MISMATCH"
        assert by_field["cta"]["status"] == "MISSING_IN_DESIGN"
        assert result["missing_fields"] == ["cta"]
        assert len(result["svg_sha256"]) == 64
        assert response.headers["cache-control"] == "no-store"

        observed["lines"][1]["text"] = "¥199.90"
        response = client.post(url, json=observed)
        assert response.status_code == 200
        by_field = {check["field"]: check for check in response.json()["field_checks"]}
        assert by_field["price"]["status"] == "MATCHED"
        assert response.json()["declared_consistency"] == "BLOCKED"
        assert response.json()["release_ready"] is False

        observed["lines"][1]["text"] = "¥199.90.5"
        response = client.post(url, json=observed)
        assert response.status_code == 200
        by_field = {check["field"]: check for check in response.json()["field_checks"]}
        assert by_field["price"]["status"] == "MISMATCH"


def test_ocr_preview_missing_or_low_confidence_lines_remain_unverified(
    design_paths, product_bytes
):
    with TestClient(app) as client:
        doc = create_approved(client, product_bytes)
        url = f"/api/designs/{doc}/revisions/1/ocr-evaluation-preview"
        empty = client.post(url, json={"lines": []})
        assert empty.status_code == 200
        by_field = {check["field"]: check for check in empty.json()["field_checks"]}
        assert by_field["title"]["status"] == "NO_OBSERVATION"
        assert by_field["price"]["status"] == "NO_OBSERVATION"
        assert by_field["cta"]["status"] == "MISSING_IN_DESIGN"

        low = client.post(
            url,
            json={
                "lines": [
                    {"text": "双十一牙刷", "score": 0.1},
                    {"text": "¥199.90", "score": 0.1},
                ]
            },
        )
        assert low.status_code == 200
        by_field = {check["field"]: check for check in low.json()["field_checks"]}
        assert by_field["title"]["status"] == "NO_OBSERVATION"
        assert by_field["price"]["status"] == "NO_OBSERVATION"
        assert low.json()["runtime_attested"] is False

        malformed = client.post(
            url,
            json={"lines": [{"text": "¥199.90", "score": 1.1}]},
        )
        assert malformed.status_code == 422


def test_ocr_preview_rejects_stale_head_and_obeys_export_scope(
    design_paths, product_bytes, monkeypatch
):
    reviewer = "reviewer-token-" + "A" * 40
    exporter = "exporter-token-" + "B" * 40
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
        doc = create_approved(client, product_bytes, token=reviewer)
        url = f"/api/designs/{doc}/revisions/1/ocr-evaluation-preview"
        assert client.post(url, json={"lines": []}).status_code == 401
        assert (
            client.post(
                url,
                json={"lines": []},
                headers={"Authorization": f"Bearer {reviewer}"},
            ).status_code
            == 403
        )
        exporter_headers = {"Authorization": f"Bearer {exporter}"}
        response = client.post(url, json={"lines": []}, headers=exporter_headers)
        assert response.status_code == 200, response.text

        updated = client.post(
            f"/api/designs/{doc}/commands",
            json={
                "command_id": "cmd-ocr-observed-stale-0001",
                "expected_revision": 1,
                "actor": "user",
                "operations": [{"op": "set_text", "node_id": "title", "text": "修改后的标题"}],
            },
        )
        assert updated.status_code == 200, updated.text
        stale = client.post(url, json={"lines": []}, headers=exporter_headers)
        assert stale.status_code == 409
        assert stale.json()["error"]["code"] == "REVISION_CONFLICT"
