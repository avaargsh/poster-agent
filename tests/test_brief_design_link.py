"""Brief-to-Design provenance contract: local token and fail-closed linking."""

import hashlib
import sqlite3

from fastapi.testclient import TestClient

import app.designs.routes as design_routes
from app.main import app
from app.schemas import PosterSpec
from tests.test_design_approval_binding import create, design_paths  # noqa: F401


def test_brief_link_auth_replay_conflict_and_restart(design_paths, product_bytes, monkeypatch):
    token = "x" * 48
    monkeypatch.setenv("POSTER_BRIEF_LINK_TOKEN", token)
    spec = PosterSpec(title="双十一牙刷", price="199.90")
    run_id = "brief-approved-run-001"
    monkeypatch.setattr(
        design_routes,
        "get_run",
        lambda rid: {
            "status": "APPROVED",
            "pending_approval": False,
            "confirmed_spec": spec.model_dump(mode="json"),
        },
    )
    with TestClient(app) as client:
        doc = create(client, product_bytes)["document_id"]
        url = f"/api/designs/{doc}/brief-link"
        payload = {"brief_run_id": run_id}
        assert client.post(url, json=payload).status_code == 401
        assert client.get(url).status_code == 401
        headers = {"Authorization": f"Bearer {token}"}
        assert client.get(url, headers=headers).json()["link"] is None
        first = client.post(url, json=payload, headers=headers)
        assert first.status_code == 201, first.text
        assert first.json()["linked_revision"] == 1
        assert first.json()["replayed"] is False
        assert first.json()["confirmed_spec_digest"] == hashlib.sha256(
            spec.model_dump_json().encode("utf-8")
        ).hexdigest()
        assert client.post(url, json=payload, headers=headers).status_code == 200
        conflict = client.post(
            url, json={"brief_run_id": "another-approved-run-002"}, headers=headers
        )
        assert conflict.status_code == 409
        assert conflict.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
        assert client.get(url, headers=headers).json()["link"]["brief_run_id"] == run_id
    with TestClient(app) as client:
        assert client.get(url, headers=headers).json()["link"]["brief_run_id"] == run_id
    with sqlite3.connect(design_paths["database"]) as db:
        assert db.execute(
            "SELECT COUNT(*) FROM design_brief_links WHERE document_id=?", (doc,)
        ).fetchone()[0] == 1


def test_brief_link_rejects_unapproved_or_changed_revision(
    design_paths, product_bytes, monkeypatch
):
    monkeypatch.setenv("POSTER_BRIEF_LINK_TOKEN", "x" * 48)
    headers = {"Authorization": "Bearer " + "x" * 48}
    spec = PosterSpec(title="双十一牙刷", price="199.90")
    state = {"status": "WAITING_CONFIRMATION", "pending_approval": True}
    monkeypatch.setattr(
        design_routes,
        "get_run",
        lambda rid: {**state, "confirmed_spec": spec.model_dump(mode="json")},
    )
    with TestClient(app) as client:
        doc = create(client, product_bytes)["document_id"]
        url = f"/api/designs/{doc}/brief-link"
        payload = {"brief_run_id": "brief-approved-run-001"}
        result = client.post(url, json=payload, headers=headers)
        assert result.status_code == 409
        assert result.json()["error"]["code"] == "BRIEF_NOT_APPROVED"
        state.update(status="APPROVED", pending_approval=False)
        updated = client.post(
            f"/api/designs/{doc}/commands",
            json={
                "command_id": "cmd-brief-link-edit-0001",
                "expected_revision": 1,
                "actor": "user",
                "operations": [{"op": "set_text", "node_id": "title", "text": "更新标题"}],
            },
        )
        assert updated.status_code == 200, updated.text
        result = client.post(url, json=payload, headers=headers)
        assert result.status_code == 409
        assert result.json()["error"]["code"] == "REVISION_CONFLICT"
        assert client.get(url, headers=headers).json()["link"] is None
