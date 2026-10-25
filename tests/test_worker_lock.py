"""Regression tests for exclusive ownership of local execution and poster data."""

from __future__ import annotations

import os
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient

from app import execution_store, worker_lock
from app.main import app

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX local worker locking")


def _configure(monkeypatch, tmp_path):
    monkeypatch.setenv("POSTER_EXECUTION_DB", str(tmp_path / "ledger" / "executions.sqlite3"))
    monkeypatch.setenv("POSTER_CHECKPOINT_DB", str(tmp_path / "workflow" / "checkpoints.sqlite3"))
    monkeypatch.setenv("POSTER_DATA_DIR", str(tmp_path / "posters"))


def test_second_worker_rejected_and_lock_released_after_exit(tmp_path, monkeypatch):
    _configure(monkeypatch, tmp_path)

    with worker_lock.single_worker():
        with pytest.raises(worker_lock.WorkerLockUnavailable):
            with worker_lock.single_worker():
                pytest.fail("second worker acquired the same resources")

    with worker_lock.single_worker():
        pass


def test_shared_storage_rejected_even_with_different_execution_db(tmp_path, monkeypatch):
    _configure(monkeypatch, tmp_path)

    with worker_lock.single_worker():
        monkeypatch.setenv("POSTER_EXECUTION_DB", str(tmp_path / "other" / "executions.sqlite3"))
        with pytest.raises(worker_lock.WorkerLockUnavailable):
            with worker_lock.single_worker():
                pytest.fail("shared poster storage must remain exclusive")


def test_shared_execution_db_rejected_even_with_different_storage(tmp_path, monkeypatch):
    _configure(monkeypatch, tmp_path)

    with worker_lock.single_worker():
        monkeypatch.setenv("POSTER_DATA_DIR", str(tmp_path / "other-posters"))
        with pytest.raises(worker_lock.WorkerLockUnavailable):
            with worker_lock.single_worker():
                pytest.fail("shared execution ledger must remain exclusive")



def test_shared_checkpoint_rejected_when_other_storage_paths_differ(tmp_path, monkeypatch):
    _configure(monkeypatch, tmp_path)

    with worker_lock.single_worker():
        monkeypatch.setenv("POSTER_EXECUTION_DB", str(tmp_path / "other" / "executions.sqlite3"))
        monkeypatch.setenv("POSTER_DATA_DIR", str(tmp_path / "other-posters"))
        with pytest.raises(worker_lock.WorkerLockUnavailable):
            with worker_lock.single_worker():
                pytest.fail("shared LangGraph checkpoints must remain exclusive")


def test_independent_python_process_cannot_acquire_live_worker_lock(tmp_path, monkeypatch):
    _configure(monkeypatch, tmp_path)
    code = (
        "from app.worker_lock import single_worker\n"
        "with single_worker():\n"
        "    print('acquired')\n"
    )

    def run_competitor():
        return subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=8,
            env=os.environ.copy(),
        )

    with worker_lock.single_worker():
        blocked = run_competitor()
        assert blocked.returncode != 0
        assert "WorkerLockUnavailable" in blocked.stderr

    released = run_competitor()
    assert released.returncode == 0, released.stderr
    assert "acquired" in released.stdout


def test_failed_second_app_startup_never_recovers_active_worker(tmp_path, monkeypatch):
    _configure(monkeypatch, tmp_path)
    key = "m7-live-worker-claim"

    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        assert execution_store.begin(key, "live-request-hash", paid=True) is None
        with pytest.raises(worker_lock.WorkerLockUnavailable):
            with TestClient(app):
                pytest.fail("second app must not begin serving")
        assert execution_store.get(key)["status"] == "RUNNING"

    # Only a later startup without a live worker may reconcile abandoned work.
    with TestClient(app) as restarted:
        assert restarted.get("/health").status_code == 200
    assert execution_store.get(key)["status"] == "UNKNOWN"


def test_startup_recovery_failure_releases_worker_lock(tmp_path, monkeypatch):
    _configure(monkeypatch, tmp_path)

    with monkeypatch.context() as patch:
        patch.setattr(execution_store, "recover_incomplete", lambda: (_ for _ in ()).throw(
            RuntimeError("simulated startup recovery failure")
        ))
        with pytest.raises(RuntimeError, match="simulated startup recovery failure"):
            with TestClient(app):
                pass

    with worker_lock.single_worker():
        pass
