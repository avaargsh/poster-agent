"""A persisted request key cannot result in two potentially billable Brief extracts."""

from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

from app import brief_request_store as store


def test_claim_replay_conflict_and_completion(tmp_path, monkeypatch):
    monkeypatch.setenv("POSTER_CHECKPOINT_DB", str(tmp_path / "checkpoints.sqlite3"))
    key = "brief-request-0001"
    digest = store.fingerprint({"compiler": "qwen", "brief": "活动价199"})
    original_id = uuid4().hex

    first = store.claim(key, digest, original_id)
    assert first["claimed"] is True
    assert first["status"] == "RUNNING"
    assert first["run_id"] == original_id

    replay = store.claim(key, digest, uuid4().hex)
    assert replay["claimed"] is False
    assert replay["run_id"] == original_id

    with pytest.raises(store.ReviewRequestConflict):
        store.claim(key, store.fingerprint({"brief": "活动价299"}), uuid4().hex)
    store.complete(key, original_id)
    completed = store.claim(key, digest, uuid4().hex)
    assert completed["status"] == "COMPLETED"
    assert completed["run_id"] == original_id
    assert store.get(key)["status"] == "COMPLETED"


def test_unknown_is_never_reclaimed_or_demoted(tmp_path, monkeypatch):
    monkeypatch.setenv("POSTER_CHECKPOINT_DB", str(tmp_path / "checkpoints.sqlite3"))
    key = "brief-request-0002"
    run_id = uuid4().hex
    digest = store.fingerprint({"compiler": "qwen", "brief": "活动价199"})
    store.claim(key, digest, run_id)
    store.mark_unknown(key, run_id)
    replay = store.claim(key, digest, uuid4().hex)
    assert replay["claimed"] is False
    assert replay["status"] == "UNKNOWN"
    assert replay["run_id"] == run_id
    store.complete(key, run_id)  # Only after an independently verified checkpoint is found.
    store.mark_unknown(key, run_id)
    assert store.get(key)["status"] == "COMPLETED"


def test_parallel_same_key_has_one_owner(tmp_path, monkeypatch):
    monkeypatch.setenv("POSTER_CHECKPOINT_DB", str(tmp_path / "checkpoints.sqlite3"))
    digest = store.fingerprint({"compiler": "qwen", "brief": "活动价199"})
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(
            pool.map(lambda _: store.claim("brief-concurrent-01", digest, uuid4().hex), range(8))
        )
    assert sum(result["claimed"] for result in results) == 1
    assert len({result["run_id"] for result in results}) == 1


def test_invalid_key_rejected_before_disk_write(tmp_path, monkeypatch):
    path = tmp_path / "checkpoints.sqlite3"
    monkeypatch.setenv("POSTER_CHECKPOINT_DB", str(path))
    with pytest.raises(store.InvalidReviewRequestKey):
        store.claim("../unsafe", "hash", uuid4().hex)
    assert not path.exists()


def test_fingerprint_stable_across_dict_order():
    assert store.fingerprint({"brief": "文字", "compiler": "qwen"}) == store.fingerprint(
        {"compiler": "qwen", "brief": "文字"}
    )
