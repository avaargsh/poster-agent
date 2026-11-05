"""Durable admission records for Brief extraction, stored alongside Graph checkpoints.

A stable key is claimed before a potentially billable Qwen request. If the
process dies, a retry can read the original run's checkpoint, but cannot claim
another model call. This is intentionally single-worker / local SQLite only.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

_VALID_KEY = re.compile(r"^[A-Za-z0-9_-]{8,128}$")


class InvalidReviewRequestKey(ValueError):
    """Invalid request idempotency key."""


class ReviewRequestConflict(RuntimeError):
    """The caller cannot reuse a key for other input."""


class ReviewRequestNotFound(LookupError):
    """The request key has not been claimed."""


def validate_key(key: str) -> None:
    if not _VALID_KEY.fullmatch(key):
        raise InvalidReviewRequestKey(
            "Idempotency-Key 必须是 8–128 位 ASCII 字母、数字、下划线或短横线"
        )


def fingerprint(request: dict) -> str:
    canonical = json.dumps(request, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    # Reuse the existing checkpoint database so the single-worker filesystem
    # lock already protects this request ledger as well.
    path = Path(os.getenv("POSTER_CHECKPOINT_DB", "data/checkpoints.sqlite3")).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=10000")
    try:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS brief_request_claims (
                idempotency_key TEXT PRIMARY KEY,
                request_hash TEXT NOT NULL,
                run_id TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL CHECK(status IN ('RUNNING','COMPLETED','UNKNOWN')),
                started_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )"""
        )
        with conn:
            yield conn
    finally:
        conn.close()


def claim(key: str, request_hash: str, proposed_run_id: str) -> dict:
    """Return claimed=True only for the first request, in one SQLite transaction."""
    validate_key(key)
    if not re.fullmatch(r"[a-f0-9]{32}", proposed_run_id):
        raise ValueError("invalid review run ID")
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        current = conn.execute(
            "SELECT * FROM brief_request_claims WHERE idempotency_key=?", (key,)
        ).fetchone()
        if current is not None:
            if current["request_hash"] != request_hash:
                raise ReviewRequestConflict("相同 Idempotency-Key 不允许用于不同 Brief")
            return {
                "claimed": False,
                "run_id": current["run_id"],
                "status": current["status"],
            }
        timestamp = _now()
        conn.execute(
            "INSERT INTO brief_request_claims VALUES(?, ?, ?, 'RUNNING', ?, ?)",
            (key, request_hash, proposed_run_id, timestamp, timestamp),
        )
        return {"claimed": True, "run_id": proposed_run_id, "status": "RUNNING"}


def complete(key: str, run_id: str) -> None:
    """Mark a request complete only after its real Graph checkpoint is readable."""
    validate_key(key)
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        updated = conn.execute(
            "UPDATE brief_request_claims SET status='COMPLETED', updated_at=? "
            "WHERE idempotency_key=? AND run_id=? AND status IN ('RUNNING','UNKNOWN')",
            (_now(), key, run_id),
        )
        if not updated.rowcount:
            existing = conn.execute(
                "SELECT run_id,status FROM brief_request_claims WHERE idempotency_key=?",
                (key,),
            ).fetchone()
            if (
                existing is None
                or existing["run_id"] != run_id
                or existing["status"] != "COMPLETED"
            ):
                raise ReviewRequestConflict("Brief 请求完成状态冲突")


def mark_unknown(key: str, run_id: str) -> None:
    """Keep uncertain calls non-retryable, never demoting COMPLETED."""
    validate_key(key)
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "UPDATE brief_request_claims SET status='UNKNOWN', updated_at=? "
            "WHERE idempotency_key=? AND run_id=? AND status='RUNNING'",
            (_now(), key, run_id),
        )


def get(key: str) -> dict:
    validate_key(key)
    with _connect() as conn:
        row = conn.execute(
            "SELECT run_id,status,started_at,updated_at FROM brief_request_claims "
            "WHERE idempotency_key=?",
            (key,),
        ).fetchone()
    if row is None:
        raise ReviewRequestNotFound("未找到该 Brief 请求")
    return dict(row)


def recover_abandoned() -> int:
    """Under the existing exclusive worker lock, mark interrupted claims UNKNOWN."""
    path = Path(os.getenv("POSTER_CHECKPOINT_DB", "data/checkpoints.sqlite3")).resolve()
    if not path.is_file():
        return 0
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        cursor = conn.execute(
            "UPDATE brief_request_claims SET status='UNKNOWN', updated_at=? WHERE status='RUNNING'",
            (_now(),),
        )
        return cursor.rowcount
