from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.designs import (
    CommandBatch,
    DesignRepository,
    InvalidCommandError,
    NotFoundError,
    RevisionConflictError,
)
from app.designs.approvals import ApprovalRequest
from app.designs.errors import AssetInvalidError
from app.designs.proposals import EditProposalRequest
from app.main import app


@pytest.fixture
def repo(tmp_path):
    return DesignRepository(tmp_path / "designs.sqlite3", tmp_path / "assets")


def command(revision, *operations):
    return CommandBatch.model_validate(
        {
            "command_id": str(uuid4()),
            "expected_revision": revision,
            "actor": "user",
            "operations": list(operations),
        }
    )


def content(document):
    return document.model_dump(mode="json", exclude={"revision"})


def test_restore_creates_snapshot_with_undo_redo_replay_and_fresh_approval(repo, product_bytes):
    original = repo.create(title="历史标题", price=Decimal("99"), product_bytes=product_bytes)
    doc = original.document_id
    repo.execute(doc, command(1, {"op": "set_price", "price": "199", "source_text": "改为199元"}))
    previous = repo.execute(
        doc, command(2, {"op": "set_node_lock", "node_id": "title", "locked": True})
    ).document
    target = repo.approval_target(doc)
    repo.approve(
        doc,
        ApprovalRequest(
            approval_id="before-restore-001",
            expected_revision=3,
            spec_digest=target["spec_digest"],
            asset_manifest_digest=target["asset_manifest_digest"],
        ),
    )
    restore = command(3, {"op": "restore_revision", "revision": 1})
    restored = repo.execute(doc, restore)
    assert restored.document.revision == 4 and content(restored.document) == content(original)
    assert repo.get(doc, revision=1) == original
    assert repo.approval_target(doc)["approved"] is False
    undone = repo.execute(doc, command(4, {"op": "undo"})).document
    assert undone.revision == 5 and content(undone) == content(previous)
    redone = repo.execute(doc, command(5, {"op": "redo"})).document
    assert redone.revision == 6 and content(redone) == content(original)
    replayed = repo.execute(doc, restore)
    assert replayed.replayed and replayed.document.revision == 4
    assert repo.get(doc).revision == 6


def test_history_pages_survive_reopen_and_new_writes(repo, product_bytes):
    original = repo.create(title="版本一", price=Decimal("99"), product_bytes=product_bytes)
    doc = original.document_id
    for revision in [1, 2]:
        repo.execute(
            doc,
            command(
                revision, {"op": "set_text", "node_id": "title", "text": f"版本{revision + 1}"}
            ),
        )
    first = repo.list_revisions(doc, limit=2)
    assert [row["revision"] for row in first["revisions"]] == [3, 2]
    assert first["revisions"][0]["operations"] == ["set_text"]
    assert first["next_cursor"] == 2
    repo.execute(doc, command(3, {"op": "restore_revision", "revision": 1}))
    reopened = DesignRepository(repo.db_path, repo.asset_dir)
    page = reopened.list_revisions(doc, limit=2, cursor=first["next_cursor"])
    assert page["current_revision"] == 4
    assert [row["revision"] for row in page["revisions"]] == [1]
    assert page["revisions"][0]["operations"] == ["create"]
    assert page["revisions"][0]["parent_revision"] is None and page["next_cursor"] is None
    assert reopened.list_revisions(doc)["revisions"][0]["operations"] == ["restore_revision"]
    with pytest.raises(InvalidCommandError):
        reopened.list_revisions(doc, cursor=100)
    with pytest.raises(InvalidCommandError):
        reopened.list_revisions(doc, limit=True)
    with pytest.raises(NotFoundError):
        reopened.list_revisions("doc_missing")


def test_restore_rejects_invalid_targets_mixed_batches_and_proposals(repo, product_bytes):
    original = repo.create(title="版本一", price=Decimal("99"), product_bytes=product_bytes)
    for revision in [1, 100]:
        with pytest.raises(InvalidCommandError):
            repo.execute(
                original.document_id, command(1, {"op": "restore_revision", "revision": revision})
            )
    with pytest.raises(ValidationError):
        command(1, {"op": "restore_revision", "revision": 1}, {"op": "undo"})
    with pytest.raises(ValidationError):
        command(1, {"op": "restore_revision", "revision": True})
    with pytest.raises(ValidationError):
        EditProposalRequest.model_validate(
            {
                "request_id": "restore-proposal-001",
                "expected_revision": 1,
                "summary": "恢复",
                "operations": [{"op": "restore_revision", "revision": 1}],
            }
        )
    assert repo.get(original.document_id).revision == 1


def test_restore_validates_assets_and_concurrent_revision_gate(repo, product_bytes):
    original = repo.create(title="版本一", price=Decimal("99"), product_bytes=product_bytes)
    doc = original.document_id
    repo.execute(doc, command(1, {"op": "set_text", "node_id": "title", "text": "版本二"}))

    def restore():
        try:
            return repo.execute(
                doc, command(2, {"op": "restore_revision", "revision": 1})
            ).document.revision
        except RevisionConflictError:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: restore(), range(2)))
    assert results.count(3) == 1 and results.count("conflict") == 1
    for path in repo.asset_dir.glob("*.bin"):
        path.write_bytes(b"broken")
    with pytest.raises(AssetInvalidError):
        repo.execute(doc, command(3, {"op": "restore_revision", "revision": 1}))
    assert repo.get(doc).revision == 3


def test_revision_http_contract(tmp_path, monkeypatch, product_bytes):
    for key, name in [
        ("POSTER_DATA_DIR", "posters"),
        ("POSTER_EXECUTION_DB", "executions.sqlite3"),
        ("POSTER_CHECKPOINT_DB", "checkpoints.sqlite3"),
        ("POSTER_DESIGN_DB", "designs.sqlite3"),
        ("POSTER_DESIGN_ASSET_DIR", "assets"),
    ]:
        monkeypatch.setenv(key, str(tmp_path / name))
    with TestClient(app) as client:
        original = client.post(
            "/api/designs",
            data={"title": "历史海报", "price": "99"},
            files={"product": ("product.png", product_bytes, "image/png")},
        ).json()["document"]
        url = "/api/designs/" + original["document_id"]
        result = client.get(url + "/revisions?limit=1")
        assert result.status_code == 200 and result.headers["cache-control"] == "no-store"
        assert result.json()["revisions"][0]["revision"] == 1
        assert client.get(url + "/revisions?limit=51").status_code == 422
        assert client.get(url + "/revisions?cursor=2").status_code == 422
        assert (
            client.get(
                url + "/revisions", headers={"Origin": "https://example.invalid"}
            ).status_code
            == 403
        )
