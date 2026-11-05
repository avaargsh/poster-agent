"""Opt-in local reviewer credential gating and durable attribution evidence."""

import hashlib
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.main import app


REVIEWER_TOKEN = "reviewer-secret-" + "A" * 40
EXPORT_TOKEN = "exporter-secret-" + "B" * 40
AUDIT_TOKEN = "auditor-secret-" + "C" * 40
SECOND_TOKEN = "second-reviewer-" + "D" * 40


@pytest.fixture
def local_design_paths(tmp_path, monkeypatch):
    monkeypatch.setenv("POSTER_DATA_DIR", str(tmp_path / "posters"))
    monkeypatch.setenv("POSTER_EXECUTION_DB", str(tmp_path / "executions.sqlite3"))
    monkeypatch.setenv("POSTER_CHECKPOINT_DB", str(tmp_path / "checkpoints.sqlite3"))
    monkeypatch.setenv("POSTER_DESIGN_DB", str(tmp_path / "designs.sqlite3"))
    monkeypatch.setenv("POSTER_DESIGN_ASSET_DIR", str(tmp_path / "design-assets"))
    monkeypatch.delenv("POSTER_DESIGN_REVIEWERS_JSON", raising=False)
    return tmp_path / "designs.sqlite3"


def _policy(*, second=False):
    reviewers = {
        "reviewer-one": {
            "token_sha256": hashlib.sha256(REVIEWER_TOKEN.encode()).hexdigest(),
            "scopes": ["approve"],
        },
        "exporter-one": {
            "token_sha256": hashlib.sha256(EXPORT_TOKEN.encode()).hexdigest(),
            "scopes": ["export"],
        },
        "auditor-one": {
            "token_sha256": hashlib.sha256(AUDIT_TOKEN.encode()).hexdigest(),
            "scopes": ["audit.read"],
        },
    }
    if second:
        reviewers["reviewer-two"] = {
            "token_sha256": hashlib.sha256(SECOND_TOKEN.encode()).hexdigest(),
            "scopes": ["approve"],
        }
    return json.dumps(reviewers)


def _headers(token):
    return {"Authorization": f"Bearer {token}"}


def _create(client, product_bytes):
    response = client.post(
        "/api/designs",
        data={"title": "双十一牙刷", "price": "199.90"},
        files={"product": ("toothbrush.png", product_bytes, "image/png")},
    )
    assert response.status_code == 201, response.text
    return response.json()["document"]["document_id"]


def _approve_payload(client, document_id):
    target = client.get(f"/api/designs/{document_id}/approval-target")
    assert target.status_code == 200, target.text
    data = target.json()
    return {
        "approval_id": "reviewer-approval-key-0001",
        "expected_revision": data["revision"],
        "spec_digest": data["spec_digest"],
        "asset_manifest_digest": data["asset_manifest_digest"],
    }


def _error(response, status, code):
    assert response.status_code == status, response.text
    assert response.json()["error"]["code"] == code


def test_opt_in_reviewer_scopes_and_durable_attribution(
    local_design_paths, product_bytes, monkeypatch
):
    monkeypatch.setenv("POSTER_DESIGN_REVIEWERS_JSON", _policy(second=True))
    with TestClient(app) as client:
        document_id = _create(client, product_bytes)
        approvals = f"/api/designs/{document_id}/approvals"
        protected_svg = f"/api/designs/{document_id}/revisions/1/approved.svg"
        payload = _approve_payload(client, document_id)
        _error(client.post(approvals, json=payload), 401, "DESIGN_REVIEWER_UNAUTHENTICATED")
        _error(client.get(approvals), 401, "DESIGN_REVIEWER_UNAUTHENTICATED")
        _error(client.get(protected_svg), 401, "DESIGN_REVIEWER_UNAUTHENTICATED")
        _error(
            client.post(approvals, json=payload, headers=_headers(EXPORT_TOKEN)),
            403,
            "DESIGN_REVIEWER_FORBIDDEN",
        )
        first = client.post(approvals, json=payload, headers=_headers(REVIEWER_TOKEN))
        assert first.status_code == 201, first.text
        assert first.json()["principal_id"] == "reviewer-one"
        assert len(first.json()["policy_digest"]) == 64
        replay = client.post(approvals, json=payload, headers=_headers(REVIEWER_TOKEN))
        assert replay.status_code == 200, replay.text
        assert replay.json()["replayed"] is True
        _error(
            client.post(approvals, json=payload, headers=_headers(SECOND_TOKEN)),
            409,
            "IDEMPOTENCY_CONFLICT",
        )
        _error(
            client.get(protected_svg, headers=_headers(REVIEWER_TOKEN)),
            403,
            "DESIGN_REVIEWER_FORBIDDEN",
        )
        assert client.get(protected_svg, headers=_headers(EXPORT_TOKEN)).status_code == 200
        _error(
            client.get(approvals, headers=_headers(REVIEWER_TOKEN)),
            403,
            "DESIGN_REVIEWER_FORBIDDEN",
        )
        history = client.get(approvals, headers=_headers(AUDIT_TOKEN))
        assert history.status_code == 200, history.text
        entries = history.json()["approvals"]
        assert len(entries) == 1
        assert entries[0]["principal_id"] == "reviewer-one"
        assert entries[0]["policy_digest"] == first.json()["policy_digest"]

    with TestClient(app) as restarted:
        history = restarted.get(approvals, headers=_headers(AUDIT_TOKEN))
        assert history.json()["approvals"][0]["principal_id"] == "reviewer-one"
        assert restarted.get(protected_svg, headers=_headers(EXPORT_TOKEN)).status_code == 200
    with sqlite3.connect(local_design_paths) as connection:
        assert connection.execute("SELECT COUNT(*) FROM design_approval_attributions").fetchone()[0] == 1


def test_legacy_approval_cannot_be_claimed_as_authenticated_evidence(
    local_design_paths, product_bytes, monkeypatch
):
    with TestClient(app) as legacy:
        document_id = _create(legacy, product_bytes)
        approvals = f"/api/designs/{document_id}/approvals"
        protected_svg = f"/api/designs/{document_id}/revisions/1/approved.svg"
        payload = _approve_payload(legacy, document_id)
        assert legacy.post(approvals, json=payload).status_code == 201
        assert legacy.get(protected_svg).status_code == 200

    monkeypatch.setenv("POSTER_DESIGN_REVIEWERS_JSON", _policy())
    with TestClient(app) as protected:
        _error(
            protected.get(protected_svg, headers=_headers(EXPORT_TOKEN)),
            409,
            "APPROVAL_REQUIRED",
        )
        _error(
            protected.post(approvals, json=payload, headers=_headers(REVIEWER_TOKEN)),
            409,
            "IDEMPOTENCY_CONFLICT",
        )
        history = protected.get(approvals, headers=_headers(AUDIT_TOKEN)).json()["approvals"]
        assert history[0]["principal_id"] is None


def test_invalid_policy_and_digest_as_bearer_never_authorize(
    local_design_paths, product_bytes, monkeypatch
):
    monkeypatch.setenv("POSTER_DESIGN_REVIEWERS_JSON", _policy())
    with TestClient(app) as client:
        document_id = _create(client, product_bytes)
        url = f"/api/designs/{document_id}/approvals"
        payload = _approve_payload(client, document_id)
        digest = hashlib.sha256(REVIEWER_TOKEN.encode()).hexdigest()
        _error(
            client.post(url, json=payload, headers=_headers(digest)),
            401,
            "DESIGN_REVIEWER_UNAUTHENTICATED",
        )
        _error(
            client.post(url, json=payload, headers={"X-Actor": "reviewer-one"}),
            401,
            "DESIGN_REVIEWER_UNAUTHENTICATED",
        )
    monkeypatch.setenv("POSTER_DESIGN_REVIEWERS_JSON", "{}")
    with TestClient(app) as invalid:
        _error(
            invalid.get(url, headers=_headers(AUDIT_TOKEN)),
            503,
            "DESIGN_REVIEWER_CONFIG_INVALID",
        )


def test_policy_rotation_invalidates_prior_authenticated_export(
    local_design_paths, product_bytes, monkeypatch
):
    monkeypatch.setenv("POSTER_DESIGN_REVIEWERS_JSON", _policy())
    with TestClient(app) as client:
        document_id = _create(client, product_bytes)
        approvals = f"/api/designs/{document_id}/approvals"
        protected_svg = f"/api/designs/{document_id}/revisions/1/approved.svg"
        payload = _approve_payload(client, document_id)
        assert (
            client.post(approvals, json=payload, headers=_headers(REVIEWER_TOKEN)).status_code
            == 201
        )
    monkeypatch.setenv("POSTER_DESIGN_REVIEWERS_JSON", _policy(second=True))
    with TestClient(app) as client:
        _error(
            client.get(protected_svg, headers=_headers(EXPORT_TOKEN)),
            409,
            "APPROVAL_REQUIRED",
        )
        _error(
            client.post(approvals, json=payload, headers=_headers(REVIEWER_TOKEN)),
            409,
            "IDEMPOTENCY_CONFLICT",
        )
