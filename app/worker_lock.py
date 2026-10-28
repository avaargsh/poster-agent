"""Exclusive local-process ownership of poster files and SQLite state.

The documented application supports a single worker on a local POSIX filesystem.
This is an advisory file lock, not authentication or a distributed lease.
"""

from __future__ import annotations

import os
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Iterator

from . import storage


class WorkerLockUnavailable(RuntimeError):
    """A second worker or unsupported platform prevents safe startup."""


def _paths() -> list[Path]:
    ledger = Path(os.getenv("POSTER_EXECUTION_DB", "data/executions.sqlite3")).resolve()
    checkpoints = Path(os.getenv("POSTER_CHECKPOINT_DB", "data/checkpoints.sqlite3")).resolve()
    posters = storage.root()
    # Any shared execution, workflow checkpoint, or poster path requires
    # exclusive ownership, even when the other settings differ.
    return sorted(
        {
            ledger.with_name(f".{ledger.name}.poster-agent-worker.lock"),
            checkpoints.with_name(f".{checkpoints.name}.poster-agent-worker.lock"),
            posters.with_name(f".{posters.name}.poster-agent-worker.lock"),
        },
        key=str,
    )


@contextmanager
def single_worker() -> Iterator[None]:
    """Reject overlapping workers before startup recovery touches shared state.

    Keep lock files in place after shutdown: unlinking them could allow two
    processes to hold different inodes under the same path.
    """
    if os.name != "posix":
        raise WorkerLockUnavailable(
            "Poster Agent's local single-worker lock requires macOS or Linux"
        )

    import fcntl

    with ExitStack() as stack:
        for path in _paths():
            path.parent.mkdir(parents=True, exist_ok=True)
            stream = stack.enter_context(path.open("a+b"))
            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise WorkerLockUnavailable(
                    "A Poster Agent worker already owns the execution ledger, checkpoint DB, or "
                    "poster storage. Stop the other worker before restarting."
                ) from exc
            stack.callback(fcntl.flock, stream.fileno(), fcntl.LOCK_UN)
        yield
