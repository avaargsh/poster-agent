"""Persistence boundary for M2b: SQLite checkpoints and per-run serialization.

This service is deliberately single-process: the worker's exclusive filesystem
lock owns its SQLite files. Multi-worker production would require a transactional
DB, distributed run ownership, and authorization.
"""

from __future__ import annotations

import os
import re
import threading
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from app import brief_request_store
from app.brief_compiler import BriefRequest
from app.schemas import PosterSpec
from app.workflows.review_graph import make_review_graph

_RUN_ID = re.compile(r"^[a-f0-9]{32}$")
# The registry guard protects only lock bookkeeping, never network-backed work.
_LOCAL_LOCK_GUARD = threading.Lock()
_LOCAL_LOCKS: dict[str, tuple[threading.RLock, int]] = {}


class ReviewNotFound(LookupError):
    pass


class ReviewConflict(RuntimeError):
    pass


def database_path() -> Path:
    db = Path(os.getenv("POSTER_CHECKPOINT_DB", "data/checkpoints.sqlite3")).resolve()
    db.parent.mkdir(parents=True, exist_ok=True)
    return db


@contextmanager
def open_graph():
    # Opening a new connection on each request also proves checkpoints can be
    # rehydrated across connections/restarts. No in-memory saver is used.
    with SqliteSaver.from_conn_string(str(database_path())) as checkpointer:
        yield make_review_graph(checkpointer)


def config(run_id: str) -> dict:
    if not _RUN_ID.fullmatch(run_id):
        raise ReviewNotFound("未找到该审核任务")
    return {"configurable": {"thread_id": run_id}}


@contextmanager
def _local_lock(token: str):
    """Serialize one run or request key, reclaiming its lock after all waiters."""
    with _LOCAL_LOCK_GUARD:
        current = _LOCAL_LOCKS.get(token)
        lock, refs = current if current is not None else (threading.RLock(), 0)
        _LOCAL_LOCKS[token] = (lock, refs + 1)
    try:
        with lock:
            yield
    finally:
        with _LOCAL_LOCK_GUARD:
            lock, refs = _LOCAL_LOCKS[token]
            if refs == 1:
                del _LOCAL_LOCKS[token]
            else:
                _LOCAL_LOCKS[token] = (lock, refs - 1)


@contextmanager
def _run_lock(run_id: str):
    """Serialize only operations on the same canonical LangGraph run."""
    config(run_id)  # Reject invalid IDs before allocating local lock entries.
    with _local_lock(f"run:{run_id}"):
        yield



def _view(run_id: str, graph, *, expected_request_hash: str | None = None) -> dict:
    snapshot = graph.get_state(config(run_id))
    values = snapshot.values or {}
    if not values.get("draft"):
        raise ReviewNotFound("未找到该审核任务")
    if expected_request_hash is not None:
        stored_request = values.get("request")
        if (
            not isinstance(stored_request, dict)
            or brief_request_store.fingerprint(stored_request) != expected_request_hash
        ):
            raise ReviewConflict("原 Brief Checkpoint 的请求摘要不匹配，拒绝重放")
    status = values.get("status", "WAITING_CONFIRMATION")
    return {
        "run_id": run_id,
        "status": status,
        "draft": values["draft"],
        "confirmed_spec": values.get("confirmed_spec"),
        "pending_approval": "approval" in snapshot.next,
        "background_generation_started": False,
    }


def create_run(request: BriefRequest, *, idempotency_key: str | None = None) -> dict:
    """Admit a stable Brief request before its Qwen network side effect."""
    if request.compiler == "qwen" and idempotency_key is None:
        raise brief_request_store.InvalidReviewRequestKey("Qwen Brief 编译必须提供 Idempotency-Key")
    if idempotency_key is None:
        return _create_run_after_admission(request, None)

    # This same-key lock covers the claim-to-run-lock handoff. Without it, a
    # concurrent replay could find an empty checkpoint before the original
    # owner acquired its run lock and incorrectly mark the claim UNKNOWN.
    brief_request_store.validate_key(idempotency_key)
    with _local_lock(f"request:{idempotency_key}"):
        return _create_run_after_admission(request, idempotency_key)


def _create_run_after_admission(request: BriefRequest, idempotency_key: str | None) -> dict:
    run_id = uuid4().hex
    claim = None
    request_hash = None
    if idempotency_key is not None:
        request_hash = brief_request_store.fingerprint(request.model_dump(mode="json"))
        # The SQLite transaction claims ownership without holding a process-wide
        # lock while a different run waits on its LLM provider.
        claim = brief_request_store.claim(idempotency_key, request_hash, run_id)
        run_id = claim["run_id"]

    with _run_lock(run_id):
        if claim is not None and not claim["claimed"]:
            # The original call on THIS run has finished (or failed) because
            # the lock serializes same-run callers, including concurrent replays.
            with open_graph() as graph:
                try:
                    result = _view(run_id, graph, expected_request_hash=request_hash)
                except (ReviewNotFound, ReviewConflict) as exc:
                    brief_request_store.mark_unknown(idempotency_key, run_id)
                    if isinstance(exc, ReviewConflict):
                        raise
                    raise ReviewConflict(
                        f"原 Brief 请求结果未确认（run_id={run_id}），不会自动重新调用模型"
                    ) from exc
            brief_request_store.complete(idempotency_key, run_id)
            return result

        try:
            with open_graph() as graph:
                graph.invoke({"request": request.model_dump(mode="json")}, config(run_id))
                result = _view(run_id, graph)
                if not result["pending_approval"]:
                    raise RuntimeError("编译流程未正确进入人工审核节点")
        except BaseException:
            # Even a transport failure can follow accepted/provider-billed work.
            if idempotency_key is not None:
                brief_request_store.mark_unknown(idempotency_key, run_id)
            raise
        if idempotency_key is not None:
            brief_request_store.complete(idempotency_key, run_id)
        return result


def get_run(run_id: str) -> dict:
    with _run_lock(run_id):
        with open_graph() as graph:
            return _view(run_id, graph)


def decide_run(run_id: str, approved: bool, spec: PosterSpec | None) -> dict:
    if approved and spec is None:
        raise ValueError("审核通过前必须提供完整 PosterSpec")
    with _run_lock(run_id):
        with open_graph() as graph:
            before = _view(run_id, graph)
            if not before["pending_approval"]:
                if before["status"] == "APPROVED" and approved and spec is not None:
                    # Older checkpoints may omit default layout controls.
                    # Normalize both sides while preserving exact field values.
                    prior = PosterSpec.model_validate(before["confirmed_spec"])
                    if prior.model_dump(mode="json") == spec.model_dump(mode="json"):
                        return before  # exact duplicate is idempotent
                if before["status"] == "REJECTED" and not approved:
                    return before
                raise ReviewConflict("该审核任务已经结束，不能覆盖原有确认结果")
            graph.invoke(
                Command(
                    resume={
                        "approved": approved,
                        "spec": spec.model_dump(mode="json") if spec else None,
                    }
                ),
                config(run_id),
            )
            result = _view(run_id, graph)
            if result["pending_approval"]:
                raise RuntimeError("审核恢复后仍处于中断状态")
            return result
