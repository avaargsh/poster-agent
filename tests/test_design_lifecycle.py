"""Lifecycle and exclusive-storage tests for the local design API."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.designs import service as design_service
from app.designs.service import (
    DesignManager,
    DesignStorageBusy,
    DesignStorageUnavailable,
)
from app.main import app

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX design storage locking")

HOLDER_CODE = """
import sys
from app.designs.service import DesignManager

manager = DesignManager(sys.argv[1], sys.argv[2])
manager.repository()
print("READY", flush=True)
sys.stdin.read()
manager.close()
"""


def configure(monkeypatch, tmp_path):
    paths = {
        "posters": tmp_path / "posters",
        "executions": tmp_path / "ledger" / "executions.sqlite3",
        "checkpoints": tmp_path / "workflow" / "checkpoints.sqlite3",
        "design_db": tmp_path / "designs" / "designs.sqlite3",
        "design_assets": tmp_path / "design-assets",
    }
    monkeypatch.setenv("POSTER_DATA_DIR", str(paths["posters"]))
    monkeypatch.setenv("POSTER_EXECUTION_DB", str(paths["executions"]))
    monkeypatch.setenv("POSTER_CHECKPOINT_DB", str(paths["checkpoints"]))
    monkeypatch.setenv("POSTER_DESIGN_DB", str(paths["design_db"]))
    monkeypatch.setenv("POSTER_DESIGN_ASSET_DIR", str(paths["design_assets"]))
    return paths


def design_lock_files(tmp_path):
    return sorted(tmp_path.rglob("*poster-agent-design.lock"))


def start_holder(db_path, asset_dir):
    process = subprocess.Popen(
        [sys.executable, "-c", HOLDER_CODE, str(db_path), str(asset_dir)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    line = process.stdout.readline().strip()
    if line != "READY":
        error = process.stderr.read()
        process.kill()
        process.wait(timeout=10)
        raise AssertionError(f"lock holder failed to start: {error}")
    return process


def stop_holder(process):
    if process.poll() is None:
        process.stdin.close()
        process.wait(timeout=10)
    process.stdout.close()
    process.stderr.close()


def test_legacy_api_does_not_create_or_lock_design_storage(tmp_path, monkeypatch):
    paths = configure(monkeypatch, tmp_path)

    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/api/posters/not-a-valid-id").status_code == 404
        assert not paths["design_db"].exists()
        assert not paths["design_assets"].exists()
        assert design_lock_files(tmp_path) == []


def test_first_design_request_initializes_lazily_and_restart_uses_fresh_objects(
    tmp_path, monkeypatch, product_bytes
):
    paths = configure(monkeypatch, tmp_path)

    with TestClient(app) as client:
        first_manager = client.app.state.design_manager
        assert isinstance(first_manager, DesignManager)
        assert not paths["design_db"].exists()
        assert design_lock_files(tmp_path) == []

        created = client.post(
            "/api/designs",
            files={"product": ("product.png", product_bytes, "image/png")},
            data={"title": "生命周期", "price": "199.90"},
        )
        assert created.status_code == 201, created.text
        document_id = created.json()["document"]["document_id"]
        first_repository = first_manager.repository()
        assert paths["design_db"].exists()
        assert paths["design_assets"].is_dir()
        assert design_lock_files(tmp_path)

    assert client.app.state.design_manager is None

    with TestClient(app) as restarted:
        second_manager = restarted.app.state.design_manager
        assert isinstance(second_manager, DesignManager)
        assert second_manager is not first_manager
        second_repository = second_manager.repository()
        assert second_repository is not first_repository
        loaded = restarted.get(f"/api/designs/{document_id}")
        assert loaded.status_code == 200, loaded.text
        assert loaded.json()["document"]["revision"] == 1


def test_runtime_environment_changes_do_not_move_an_already_started_service(
    tmp_path, monkeypatch, product_bytes
):
    paths = configure(monkeypatch, tmp_path)
    moved_db = tmp_path / "moved" / "designs.sqlite3"
    moved_assets = tmp_path / "moved-assets"

    with TestClient(app) as client:
        monkeypatch.setenv("POSTER_DESIGN_DB", str(moved_db))
        monkeypatch.setenv("POSTER_DESIGN_ASSET_DIR", str(moved_assets))
        created = client.post(
            "/api/designs",
            files={"product": ("product.png", product_bytes, "image/png")},
            data={"title": "固定路径", "price": "99"},
        )
        assert created.status_code == 201, created.text

    assert paths["design_db"].exists()
    assert paths["design_assets"].exists()
    assert not moved_db.exists()
    assert not moved_assets.exists()


def test_missing_lifespan_returns_503_without_opening_storage(tmp_path, monkeypatch):
    paths = configure(monkeypatch, tmp_path)
    from app.designs.routes import router as design_router

    isolated = FastAPI()
    isolated.include_router(design_router)

    response = TestClient(isolated).get("/api/designs/does-not-exist")

    assert response.status_code == 503
    assert response.json() == {
        "error": {
            "code": "DESIGN_STORAGE_UNAVAILABLE",
            "message": "design service lifecycle is not running",
        }
    }
    assert not paths["design_db"].exists()
    assert not paths["design_assets"].exists()
    assert design_lock_files(tmp_path) == []


def test_same_process_managers_are_exclusive_for_each_storage_resource(tmp_path):
    owner = DesignManager(tmp_path / "a" / "designs.sqlite3", tmp_path / "a-assets")
    owner.repository()
    try:
        same_db = DesignManager(tmp_path / "a" / "designs.sqlite3", tmp_path / "b-assets")
        with pytest.raises(DesignStorageBusy):
            same_db.repository()

        same_assets = DesignManager(tmp_path / "b" / "designs.sqlite3", tmp_path / "a-assets")
        with pytest.raises(DesignStorageBusy):
            same_assets.repository()

        independent = DesignManager(tmp_path / "b" / "designs.sqlite3", tmp_path / "b-assets")
        assert independent.repository() is not None
        independent.close()
    finally:
        owner.close()

    reopened = DesignManager(tmp_path / "a" / "designs.sqlite3", tmp_path / "a-assets")
    assert reopened.repository() is not None
    reopened.close()


def test_real_cross_process_lock_rejects_shared_db_or_asset_directory(tmp_path):
    db_path = tmp_path / "cross" / "designs.sqlite3"
    asset_dir = tmp_path / "cross-assets"
    holder = start_holder(db_path, asset_dir)
    try:
        same_db = DesignManager(db_path, tmp_path / "other-assets")
        with pytest.raises(DesignStorageBusy):
            same_db.repository()

        same_assets = DesignManager(tmp_path / "other" / "designs.sqlite3", asset_dir)
        with pytest.raises(DesignStorageBusy):
            same_assets.repository()

        independent = DesignManager(
            tmp_path / "other" / "designs.sqlite3",
            tmp_path / "other-assets",
        )
        assert independent.repository() is not None
        independent.close()
    finally:
        stop_holder(holder)

    reopened = DesignManager(db_path, asset_dir)
    assert reopened.repository() is not None
    reopened.close()


def test_manager_close_is_repeatable_and_failed_initialization_can_retry(tmp_path):
    db_path = tmp_path / "retry" / "designs.sqlite3"
    blocked_asset_path = tmp_path / "blocked-assets"
    blocked_asset_path.write_text("not a directory", encoding="utf-8")

    manager = DesignManager(db_path, blocked_asset_path)
    with pytest.raises(DesignStorageUnavailable):
        manager.repository()

    # The failed attempt released both locks; another manager can own the same
    # database while the first manager is still open but uninitialized.
    other = DesignManager(db_path, tmp_path / "other-assets")
    assert other.repository() is not None
    other.close()

    blocked_asset_path.unlink()
    assert manager.repository() is not None
    manager.close()
    manager.close()

    with pytest.raises(DesignStorageUnavailable):
        manager.repository()


def test_concurrent_first_requests_initialize_one_repository(tmp_path, monkeypatch):
    calls = []
    real_repository = design_service.DesignRepository

    class CountingRepository(real_repository):
        def __init__(self, *args, **kwargs):
            calls.append(1)
            time.sleep(0.05)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(design_service, "DesignRepository", CountingRepository)
    manager = DesignManager(tmp_path / "concurrent" / "designs.sqlite3", tmp_path / "assets")
    barrier = threading.Barrier(8)

    def initialize(_index):
        barrier.wait(timeout=5)
        return manager.repository()

    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            repositories = list(pool.map(initialize, range(8)))
    finally:
        manager.close()

    assert len(repositories) == 8
    assert len({id(repository) for repository in repositories}) == 1
    assert len(calls) == 1
