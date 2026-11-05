"""Revision-bound local design approval: fail-closed export and immutable evidence."""

import hashlib
import sqlite3
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

import app.designs.approvals as approvals_module
import app.designs.repository as repository_module
from app.main import app


@pytest.fixture
def design_paths(tmp_path, monkeypatch):
    paths = {
        "database": tmp_path / "designs.sqlite3",
        "assets": tmp_path / "design-assets",
    }
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


def target(client, document_id):
    response = client.get(f"/api/designs/{document_id}/approval-target")
    assert response.status_code == 200, response.text
    return response.json()


def approve(client, document_id, snapshot, *, approval_id=None):
    return client.post(
        f"/api/designs/{document_id}/approvals",
        json={
            "approval_id": approval_id or "approval-" + uuid.uuid4().hex,
            "expected_revision": snapshot["revision"],
            "spec_digest": snapshot["spec_digest"],
            "asset_manifest_digest": snapshot["asset_manifest_digest"],
        },
    )


def export(client, document_id, revision):
    return client.get(f"/api/designs/{document_id}/revisions/{revision}/approved.svg")


def edit(client, document_id, revision, *, text="已修改"):
    return client.post(
        f"/api/designs/{document_id}/commands",
        json={
            "command_id": "cmd-" + uuid.uuid4().hex,
            "expected_revision": revision,
            "actor": "user",
            "operations": [{"op": "set_text", "node_id": "title", "text": text}],
        },
    )


def expect_error(response, status, code):
    assert response.status_code == status, response.text
    assert response.json()["error"]["code"] == code


def test_current_revision_approval_is_idempotent_and_survives_restart(design_paths, product_bytes):
    with TestClient(app) as client:
        document = create(client, product_bytes)
        doc = document["document_id"]
        initial = target(client, doc)
        assert initial["revision"] == 1
        assert initial["approved"] is False
        assert len(initial["spec_digest"]) == 64
        assert len(initial["asset_manifest_digest"]) == 64
        expect_error(export(client, doc, 1), 409, "APPROVAL_REQUIRED")
        assert client.get(f"/api/designs/{doc}/revisions/1/preview.svg").status_code == 200

        approval_id = "approval-fixed-durable-0001"
        first = approve(client, doc, initial, approval_id=approval_id)
        assert first.status_code == 201, first.text
        assert first.json()["replayed"] is False
        assert first.json()["revision"] == 1
        assert first.json()["approval_id"] == approval_id
        replay = approve(client, doc, initial, approval_id=approval_id)
        assert replay.status_code == 200, replay.text
        assert replay.json()["replayed"] is True

        current = target(client, doc)
        assert current["approved"] is True
        assert current["approval_id"] == approval_id
        rendered = export(client, doc, 1)
        assert rendered.status_code == 200, rendered.text
        assert rendered.headers["cache-control"] == "no-store"
        assert b"<svg" in rendered.content
        digest = hashlib.sha256(rendered.content).hexdigest()

    with TestClient(app) as restarted:
        assert target(restarted, doc)["approved"] is True
        exported = export(restarted, doc, 1)
        assert exported.status_code == 200
        assert hashlib.sha256(exported.content).hexdigest() == digest

    with sqlite3.connect(design_paths["database"]) as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM design_approvals WHERE document_id=?", (doc,)
            ).fetchone()[0]
            == 1
        )


def test_approval_revoked_for_new_head_even_after_undo(design_paths, product_bytes):
    with TestClient(app) as client:
        doc = create(client, product_bytes)["document_id"]
        initial = target(client, doc)
        assert approve(client, doc, initial).status_code == 201
        updated = edit(client, doc, 1)
        assert updated.status_code == 200, updated.text
        assert updated.json()["document"]["revision"] == 2
        assert target(client, doc)["approved"] is False
        expect_error(export(client, doc, 1), 409, "REVISION_CONFLICT")
        expect_error(export(client, doc, 2), 409, "APPROVAL_REQUIRED")
        expect_error(approve(client, doc, initial), 409, "REVISION_CONFLICT")
        undone = client.post(
            f"/api/designs/{doc}/commands",
            json={
                "command_id": "cmd-" + uuid.uuid4().hex,
                "expected_revision": 2,
                "actor": "user",
                "operations": [{"op": "undo"}],
            },
        )
        assert undone.status_code == 200, undone.text
        assert undone.json()["document"]["revision"] == 3
        assert target(client, doc)["approved"] is False
        expect_error(export(client, doc, 3), 409, "APPROVAL_REQUIRED")
        latest = target(client, doc)
        assert approve(client, doc, latest).status_code == 201
        assert export(client, doc, 3).status_code == 200


def test_wrong_digest_and_reused_id_reject_without_new_record(design_paths, product_bytes):
    with TestClient(app) as client:
        doc = create(client, product_bytes)["document_id"]
        current = target(client, doc)
        wrong_spec = {**current, "spec_digest": "0" * 64}
        expect_error(approve(client, doc, wrong_spec), 409, "APPROVAL_DIGEST_CONFLICT")
        wrong_assets = {**current, "asset_manifest_digest": "f" * 64}
        expect_error(approve(client, doc, wrong_assets), 409, "APPROVAL_DIGEST_CONFLICT")
        fixed_id = "approval-digest-check-0001"
        assert approve(client, doc, current, approval_id=fixed_id).status_code == 201
        expect_error(
            approve(client, doc, wrong_spec, approval_id=fixed_id),
            409,
            "IDEMPOTENCY_CONFLICT",
        )
        expect_error(approve(client, doc, current), 409, "APPROVAL_CONFLICT")
        assert target(client, doc)["approval_id"] == fixed_id


def test_corrupt_asset_blocks_new_approval_and_approved_export(design_paths, product_bytes):
    with TestClient(app) as client:
        doc = create(client, product_bytes)["document_id"]
        stamp = target(client, doc)
        files = list(design_paths["assets"].glob("*.bin"))
        assert len(files) == 1
        original_bytes = files[0].read_bytes()
        files[0].write_bytes(b"corrupt")
        expect_error(approve(client, doc, stamp), 409, "ASSET_INVALID")
        files[0].write_bytes(original_bytes)
        assert approve(client, doc, stamp).status_code == 201
        assert export(client, doc, 1).status_code == 200
        files[0].write_bytes(b"corrupt")
        expect_error(export(client, doc, 1), 409, "ASSET_INVALID")


def test_concurrent_edit_and_approval_never_authorizes_new_revision(design_paths, product_bytes):
    with TestClient(app) as client:
        doc = create(client, product_bytes)["document_id"]
        initial = target(client, doc)
        with ThreadPoolExecutor(max_workers=2) as pool:
            approval = pool.submit(approve, client, doc, initial)
            command = pool.submit(edit, client, doc, 1)
            approval_response = approval.result(timeout=10)
            command_response = command.result(timeout=10)
        assert command_response.status_code == 200, command_response.text
        assert approval_response.status_code in (201, 409), approval_response.text
        assert target(client, doc)["revision"] == 2
        assert target(client, doc)["approved"] is False
        expect_error(export(client, doc, 2), 409, "APPROVAL_REQUIRED")


def test_invalid_approval_and_document_ids_fail_closed(design_paths, product_bytes):
    with TestClient(app) as client:
        doc = create(client, product_bytes)["document_id"]
        snapshot = target(client, doc)
        invalid = client.post(
            f"/api/designs/{doc}/approvals",
            json={
                "approval_id": "bad",
                "expected_revision": True,
                "spec_digest": snapshot["spec_digest"],
                "asset_manifest_digest": snapshot["asset_manifest_digest"],
            },
        )
        assert invalid.status_code == 422
        expect_error(client.get("/api/designs/non-existent/approval-target"), 404, "NOT_FOUND")
        expect_error(export(client, doc, 2), 409, "REVISION_CONFLICT")


def test_existing_v1_database_reopens_without_approval_table(design_paths, product_bytes):
    with TestClient(app) as client:
        document = create(client, product_bytes)
        doc = document["document_id"]
        original_svg = client.get(f"/api/designs/{doc}/revisions/1/preview.svg").content

    # Simulate a database persisted by the pre-approval repository version.
    with sqlite3.connect(design_paths["database"]) as connection:
        connection.execute("DROP TABLE design_approvals")
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM revisions WHERE document_id=?", (doc,)
            ).fetchone()[0]
            == 1
        )

    with TestClient(app) as restarted:
        assert restarted.get(f"/api/designs/{doc}").json()["document"] == document
        assert restarted.get(f"/api/designs/{doc}/revisions/1/preview.svg").content == original_svg
        current = target(restarted, doc)
        assert current["approved"] is False
        assert approve(restarted, doc, current).status_code == 201
        assert export(restarted, doc, 1).status_code == 200


def test_simultaneous_same_approval_id_writes_once(design_paths, product_bytes):
    with TestClient(app) as client:
        doc = create(client, product_bytes)["document_id"]
        current = target(client, doc)
        approval_id = "approval-parallel-retry-0001"
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(approve, client, doc, current, approval_id=approval_id)
            second = pool.submit(approve, client, doc, current, approval_id=approval_id)
            outcomes = [first.result(timeout=10), second.result(timeout=10)]
        assert sorted(item.status_code for item in outcomes) == [200, 201]
        assert sorted(item.json()["replayed"] for item in outcomes) == [False, True]
        assert target(client, doc)["approved"] is True


def test_renderer_upgrade_invalidates_then_allows_fresh_approval(
    design_paths, product_bytes, monkeypatch
):
    with TestClient(app) as client:
        doc = create(client, product_bytes)["document_id"]
        original = target(client, doc)
        first_id = "approval-renderer-old-0001"
        assert approve(client, doc, original, approval_id=first_id).status_code == 201
        assert export(client, doc, 1).status_code == 200

        # A renderer change alters the reviewed visual contract even without
        # document edits. Old evidence remains immutable, not export-authorizing.
        monkeypatch.setattr(approvals_module, "RENDERER_VERSION", "design-svg@2")
        monkeypatch.setattr(repository_module, "RENDERER_VERSION", "design-svg@2")
        changed = target(client, doc)
        assert changed["revision"] == 1
        assert changed["approved"] is False
        assert changed["spec_digest"] != original["spec_digest"]
        expect_error(export(client, doc, 1), 409, "APPROVAL_REQUIRED")
        expect_error(
            approve(client, doc, changed, approval_id=first_id),
            409,
            "IDEMPOTENCY_CONFLICT",
        )

        second_id = "approval-renderer-new-0001"
        fresh = approve(client, doc, changed, approval_id=second_id)
        assert fresh.status_code == 201, fresh.text
        assert target(client, doc)["approval_id"] == second_id
        assert export(client, doc, 1).status_code == 200

    with sqlite3.connect(design_paths["database"]) as connection:
        rows = connection.execute(
            "SELECT approval_id, renderer_version FROM design_approvals WHERE document_id=?",
            (doc,),
        ).fetchall()
        assert len(rows) == 2
        assert set(rows) == {
            (first_id, "design-svg@1"),
            (second_id, "design-svg@2"),
        }


def test_approval_history_tracks_superseded_revisions_and_restart(design_paths, product_bytes):
    with TestClient(app) as client:
        doc = create(client, product_bytes)["document_id"]
        url = f"/api/designs/{doc}/approvals"
        empty = client.get(url)
        assert empty.status_code == 200
        assert empty.json()["approvals"] == []
        first = target(client, doc)
        first_id = "approval-history-first-0001"
        assert approve(client, doc, first, approval_id=first_id).status_code == 201
        assert approve(client, doc, first, approval_id=first_id).status_code == 200

        rows = client.get(url).json()["approvals"]
        assert len(rows) == 1
        assert rows[0]["is_current_head"] is True
        assert rows[0]["matches_current_snapshot"] is True
        assert rows[0]["spec_digest"] == first["spec_digest"]
        assert rows[0]["asset_manifest_digest"] == first["asset_manifest_digest"]

        updated = edit(client, doc, 1)
        assert updated.status_code == 200, updated.text
        next_target = target(client, doc)
        assert next_target["revision"] == 2
        rows = client.get(url).json()["approvals"]
        assert len(rows) == 1
        assert rows[0]["is_current_head"] is False
        assert rows[0]["matches_current_snapshot"] is False
        expect_error(export(client, doc, 2), 409, "APPROVAL_REQUIRED")

        second_id = "approval-history-second-0001"
        assert approve(client, doc, next_target, approval_id=second_id).status_code == 201
        rows = client.get(url).json()["approvals"]
        assert [row["approval_id"] for row in rows] == [first_id, second_id]
        assert [row["is_current_head"] for row in rows] == [False, True]
        assert [row["matches_current_snapshot"] for row in rows] == [False, True]
        assert all(row["created_at"] for row in rows)

    with TestClient(app) as restarted:
        assert restarted.get(url).json()["approvals"] == rows
        expect_error(restarted.get("/api/designs/does-not-exist/approvals"), 404, "NOT_FOUND")
        expect_error(restarted.get("/api/designs/INVALID/approvals"), 404, "NOT_FOUND")


def test_approval_history_renderer_change_does_not_resurrect_old_evidence(
    design_paths, product_bytes, monkeypatch
):
    with TestClient(app) as client:
        doc = create(client, product_bytes)["document_id"]
        current = target(client, doc)
        assert approve(
            client, doc, current, approval_id="approval-history-renderer-old"
        ).status_code == 201
        monkeypatch.setattr(approvals_module, "RENDERER_VERSION", "design-svg@2")
        monkeypatch.setattr(repository_module, "RENDERER_VERSION", "design-svg@2")
        url = f"/api/designs/{doc}/approvals"
        rows = client.get(url).json()["approvals"]
        assert len(rows) == 1
        assert rows[0]["is_current_head"] is True
        assert rows[0]["matches_current_snapshot"] is False
        expect_error(export(client, doc, 1), 409, "APPROVAL_REQUIRED")
        upgraded = target(client, doc)
        assert approve(
            client, doc, upgraded, approval_id="approval-history-renderer-new"
        ).status_code == 201
        rows = client.get(url).json()["approvals"]
        assert len(rows) == 2
        assert [row["matches_current_snapshot"] for row in rows] == [False, True]
