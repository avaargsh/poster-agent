"""Proposals stay drafts until an explicit version-checked application."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.designs.commands import CommandBatch
from app.designs.errors import IdempotencyConflictError, LockedNodeError, RevisionConflictError
from app.designs.proposals import EditProposalRequest
from app.designs.repository import DesignRepository


@pytest.fixture
def store(tmp_path, product_bytes):
    repo = DesignRepository(tmp_path / "designs.sqlite3", tmp_path / "assets")
    document = repo.create(title="原始标题", price=Decimal("99.90"), product_bytes=product_bytes)
    return repo, document


def request(revision=1, **changes):
    return EditProposalRequest.model_validate(
        {
            "request_id": str(uuid4()),
            "expected_revision": revision,
            "summary": "修改主标题",
            "operations": [{"op": "set_text", "node_id": "title", "text": "新的标题"}],
            **changes,
        }
    )


def test_proposal_is_persisted_without_changing_the_design(store):
    repo, document = store
    proposal = repo.propose_edit(document.document_id, request())
    assert proposal["status"] == "PENDING"
    assert proposal["candidate_document"]["revision"] == 1
    assert repo.get(document.document_id) == document
    reopened = DesignRepository(repo.db_path, repo.asset_dir)
    assert reopened.get_edit_proposal(document.document_id, proposal["proposal_id"]) == proposal


def test_proposal_request_replay_is_stable_and_changed_input_conflicts(store):
    repo, document = store
    req = request()
    first = repo.propose_edit(document.document_id, req)
    assert repo.propose_edit(document.document_id, req)["proposal_id"] == first["proposal_id"]
    changed = request(request_id=req.request_id, summary="不同意图")
    with pytest.raises(IdempotencyConflictError):
        repo.propose_edit(document.document_id, changed)


def test_apply_is_one_revision_replayable_and_undoable(store):
    repo, document = store
    proposal = repo.propose_edit(document.document_id, request())
    result = repo.apply_edit_proposal(document.document_id, proposal["proposal_id"])
    assert result.document.revision == 2 and not result.replayed
    replay = repo.apply_edit_proposal(document.document_id, proposal["proposal_id"])
    assert replay.document == result.document and replay.replayed
    assert (
        repo.get_edit_proposal(document.document_id, proposal["proposal_id"])["status"] == "APPLIED"
    )
    restored = repo.execute(
        document.document_id,
        CommandBatch.model_validate(
            {
                "command_id": str(uuid4()),
                "expected_revision": 2,
                "actor": "user",
                "operations": [{"op": "undo"}],
            }
        ),
    ).document
    assert restored.revision == 3
    assert restored.nodes == document.nodes
    assert (
        repo.apply_edit_proposal(document.document_id, proposal["proposal_id"]).document.revision
        == 2
    )


def test_unrelated_edit_makes_a_pending_proposal_stale(store):
    repo, document = store
    req = request()
    proposal = repo.propose_edit(document.document_id, req)
    repo.execute(
        document.document_id,
        CommandBatch.model_validate(
            {
                "command_id": str(uuid4()),
                "expected_revision": 1,
                "actor": "user",
                "operations": [{"op": "transform_node", "node_id": "product", "x": 600}],
            }
        ),
    )
    assert (
        repo.get_edit_proposal(document.document_id, proposal["proposal_id"])["status"] == "STALE"
    )
    assert repo.propose_edit(document.document_id, req)["proposal_id"] == proposal["proposal_id"]
    with pytest.raises(RevisionConflictError):
        repo.apply_edit_proposal(document.document_id, proposal["proposal_id"])
    assert repo.get(document.document_id).revision == 2


def test_locked_layer_and_forbidden_operations_are_rejected(store):
    repo, document = store
    repo.execute(
        document.document_id,
        CommandBatch.model_validate(
            {
                "command_id": str(uuid4()),
                "expected_revision": 1,
                "actor": "user",
                "operations": [{"op": "set_node_lock", "node_id": "title", "locked": True}],
            }
        ),
    )
    with pytest.raises(LockedNodeError):
        repo.propose_edit(document.document_id, request(2))
    for op in [{"op": "undo"}, {"op": "set_node_lock", "node_id": "title", "locked": False}]:
        with pytest.raises(ValidationError):
            request(2, operations=[op])


def test_simultaneous_application_only_commits_once(store):
    repo, document = store
    proposal = repo.propose_edit(document.document_id, request())
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda _: repo.apply_edit_proposal(document.document_id, proposal["proposal_id"]),
                range(2),
            )
        )
    assert {r.document.revision for r in results} == {2}
    assert sum(r.replayed for r in results) == 1


def test_base_snapshot_tampering_is_not_accepted(store):
    repo, document = store
    proposal = repo.propose_edit(document.document_id, request())
    with sqlite3.connect(repo.db_path) as db:
        db.execute(
            "UPDATE revisions SET document_json=replace(document_json, '原始标题', '篡改标题') WHERE document_id=?",
            (document.document_id,),
        )
    with pytest.raises(IdempotencyConflictError):
        repo.apply_edit_proposal(document.document_id, proposal["proposal_id"])
