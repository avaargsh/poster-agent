"""Process-local lifecycle ownership for the persistent design store.

The web application constructs one :class:`DesignManager` per lifespan.  The
manager is deliberately lazy: constructing it does not create directories,
open SQLite or claim a lock.  The first design request acquires exclusive
POSIX locks for both the database and the asset directory, then constructs the
repository.  Legacy poster requests therefore never touch design storage.

The lock files stay on disk after release.  Removing them could let two
processes lock different inodes for the same logical path.
"""

from __future__ import annotations

import os
import stat
import threading
from pathlib import Path
from typing import BinaryIO

from .errors import DesignError
from .repository import DesignRepository

DEFAULT_DESIGN_DB = "data/designs.sqlite3"
DEFAULT_DESIGN_ASSET_DIR = "data/design-assets"
_LOCK_SUFFIX = ".poster-agent-design.lock"


class DesignStorageBusy(DesignError):
    """Another process already owns the same database or asset directory."""

    code = "DESIGN_STORAGE_BUSY"

    @property
    def default_message(self) -> str:
        return "design storage is already owned by another local service"


class DesignStorageUnavailable(DesignError):
    """Design storage cannot be initialized or is no longer available."""

    code = "DESIGN_STORAGE_UNAVAILABLE"

    @property
    def default_message(self) -> str:
        return "design storage is unavailable"


def _absolute_path(value: str | os.PathLike[str]) -> Path:
    """Normalize a configured path without resolving symlinks.

    Resolving symlinks here would hide an unsafe asset directory from the
    repository's explicit symlink rejection.
    """

    return Path(os.path.abspath(os.path.expanduser(os.fspath(value))))


def _lock_path(path: Path) -> Path:
    return path.with_name(f".{path.name}{_LOCK_SUFFIX}")


def create_design_manager() -> "DesignManager":
    """Construct a lazy manager from process configuration.

    Paths are captured at application startup, so later requests cannot change
    the storage location.
    """

    db_value = os.getenv("POSTER_DESIGN_DB", "").strip() or DEFAULT_DESIGN_DB
    asset_value = os.getenv("POSTER_DESIGN_ASSET_DIR", "").strip() or DEFAULT_DESIGN_ASSET_DIR
    return DesignManager(db_value, asset_value)


class DesignManager:
    """Own one design repository and its exclusive locks for one lifespan."""

    def __init__(
        self,
        db_path: str | os.PathLike[str],
        asset_dir: str | os.PathLike[str],
    ) -> None:
        self.db_path = _absolute_path(db_path)
        self.asset_dir = _absolute_path(asset_dir)
        self._mutex = threading.Lock()
        self._repository: DesignRepository | None = None
        self._lock_streams: list[BinaryIO] = []
        self._closed = False

    def repository(self) -> DesignRepository:
        """Return the repository, initializing storage exactly once.

        A failed initialization releases every lock already acquired, so a
        later request can retry with fixed configuration.  Concurrent first
        requests share the same mutex and therefore one repository instance.
        """

        with self._mutex:
            if self._closed:
                raise DesignStorageUnavailable("design service has been closed")
            if self._repository is not None:
                return self._repository

            self._acquire_locks()
            try:
                self._validate_database_file()
                repository = DesignRepository(self.db_path, self.asset_dir)
            except DesignStorageBusy:
                self._release_locks()
                raise
            except DesignError:
                self._release_locks()
                raise DesignStorageUnavailable() from None
            except Exception:
                self._release_locks()
                raise DesignStorageUnavailable() from None
            except BaseException:
                self._release_locks()
                raise

            self._repository = repository
            return repository

    def close(self) -> None:
        """Release locks as many times as the caller needs; safe to repeat."""

        with self._mutex:
            self._closed = True
            self._repository = None
            self._release_locks()

    def _acquire_locks(self) -> None:
        if os.name != "posix":
            raise DesignStorageUnavailable("design storage locking requires macOS or Linux")

        import fcntl

        self._validate_database_file()
        lock_paths = sorted(
            {_lock_path(self.db_path), _lock_path(self.asset_dir)},
            key=str,
        )
        for path in lock_paths:
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                stream = path.open("a+b")
            except OSError:
                self._release_locks()
                raise DesignStorageUnavailable() from None

            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                stream.close()
                self._release_locks()
                raise DesignStorageBusy() from None
            except OSError:
                stream.close()
                self._release_locks()
                raise DesignStorageUnavailable() from None
            self._lock_streams.append(stream)

    def _validate_database_file(self) -> None:
        # Filename-based locks cannot protect aliases of the same SQLite file.
        # Check before locking and again before opening the repository. Keep
        # this lazy so legacy requests never inspect or create design storage.
        try:
            metadata = self.db_path.lstat()
        except FileNotFoundError:
            return
        except OSError:
            raise DesignStorageUnavailable() from None
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise DesignStorageUnavailable()

    def _release_locks(self) -> None:
        streams, self._lock_streams = self._lock_streams, []
        for stream in streams:
            try:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
            except (ImportError, OSError):
                pass
            try:
                stream.close()
            except OSError:
                pass
