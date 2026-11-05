"""Brief-to-Design provenance contract: local token and fail-closed linking."""

import hashlib
import sqlite3

import pytest
from fastapi.testclient import TestClient

import app.designs.routes as design_routes
from app.main import app
from app.schemas import PosterSpec


@pytest.fixture
def design_paths(tmp_path, monkeypatch):
    paths = {"database": tmp_path / "designs.sqlite3", "assets": tmp_path / "design-assets"}
    monkeypatch.setenv("POSTER_DATA_DIR", str(tmp_path / "posters"))
    monkeypatch.setenv("POSTER_EXECUTION_DB", str(tmp_path / "executions.sqlite3"))
    monkeypatch.setenv("POSTER_CHECKPOINT_DB", str(tmp_path / "checkpoints.sqlite3"))
    monkeypatch.setenv("POSTER_DESIGN_DB", str(paths["database"]))
    monkeypatch.setenv("POSTER_DESIGN_ASSET_DIR", str(paths["assets"]))
    return paths


def create(client, product_bytes):
    response = client.post(
        "/api/designs",
        data={"title": "双十一牙刷", "price": "199.90"},
        files={"product": ("toothbrush.png", product_bytes, "image/png")},
    )
    assert response.status_code == 201, response.text
    return response.json()["document"]


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
        assert (
            first.json()["confirmed_spec_digest"]
            == hashlib.sha256(spec.model_dump_json().encode("utf-8")).hexdigest()
        )
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
        assert (
            db.execute(
                "SELECT COUNT(*) FROM design_brief_links WHERE document_id=?", (doc,)
            ).fetchone()[0]
            == 1
        )


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


def test_brief_link_rejects_mismatched_business_facts(
    design_paths, product_bytes, monkeypatch
):
    monkeypatch.setenv("POSTER_BRIEF_LINK_TOKEN", "x" * 48)
    headers = {"Authorization": "Bearer " + "x" * 48}
    state = {
        "confirmed_spec": PosterSpec(title="其他商品", price="199.90").model_dump(mode="json")
    }
    monkeypatch.setattr(
        design_routes,
        "get_run",
        lambda rid: {"status": "APPROVED", "pending_approval": False, **state},
    )
    with TestClient(app) as client:
        doc = create(client, product_bytes)["document_id"]
        url = f"/api/designs/{doc}/brief-link"
        payload = {"brief_run_id": "brief-approved-run-001"}
        result = client.post(url, json=payload, headers=headers)
        assert result.status_code == 422
        assert result.json()["error"]["code"] == "INVALID_COMMAND"
        state["confirmed_spec"] = PosterSpec(
            title="双十一牙刷", price="200.00"
        ).model_dump(mode="json")
        result = client.post(url, json=payload, headers=headers)
        assert result.status_code == 422
        assert client.get(url, headers=headers).json()["link"] is None
