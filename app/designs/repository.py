"""SQLite-backed design repository with content-addressed assets.

The repository is the only writer of design state.  Every mutation runs in an
explicit ``BEGIN IMMEDIATE`` transaction with a revision check; assets are
published atomically before a document can reference them.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import re
import sqlite3
import stat
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from typing import Iterator

from PIL import Image, UnidentifiedImageError
from pydantic import ValidationError

from .approvals import ApprovalRequest, snapshot_digests
from .commands import (
    CommandBatch,
    CommandResult,
    Redo,
    ReorderNode,
    SetNodeLock,
    SetPrice,
    SetStyle,
    SetText,
    TransformNode,
    Undo,
)
from .errors import (
    ApprovalConflictError,
    ApprovalDigestConflictError,
    ApprovalRequiredError,
    AssetInvalidError,
    IdempotencyConflictError,
    InvalidCommandError,
    LockedNodeError,
    NotFoundError,
    NothingToRedoError,
    NothingToUndoError,
    RevisionConflictError,
    StorageError,
)
from .models import (
    MAX_ASSET_BYTES,
    RENDERER_VERSION,
    AssetRef,
    BusinessFacts,
    DesignDocument,
    ImageNode,
    RectNode,
    TextLine,
    TextNode,
    format_price_text,
)
from .rendering import ALLOWED_FORMATS, DesignRenderError, render_svg
from .templates import create_template
from .text_layout import split_text_chunks

_DOCUMENT_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_ASSET_FILE_SUFFIX = ".bin"
_SCHEMA = """
CREATE TABLE IF NOT EXISTS designs (
    document_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS revisions (
    document_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    parent_revision INTEGER,
    document_json TEXT NOT NULL,
    command_id TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (document_id, revision),
    FOREIGN KEY (document_id) REFERENCES designs(document_id)
);

CREATE TABLE IF NOT EXISTS heads (
    document_id TEXT PRIMARY KEY,
    revision INTEGER NOT NULL,
    undo_stack TEXT NOT NULL,
    redo_stack TEXT NOT NULL,
    FOREIGN KEY (document_id) REFERENCES designs(document_id)
);

CREATE TABLE IF NOT EXISTS commands (
    document_id TEXT NOT NULL,
    command_id TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    result_revision INTEGER NOT NULL,
    actor TEXT NOT NULL,
    request_json TEXT NOT NULL,
    source_texts_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (document_id, command_id),
    FOREIGN KEY (document_id) REFERENCES designs(document_id)
);

CREATE TABLE IF NOT EXISTS assets (
    document_id TEXT NOT NULL,
    asset_id TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    media_type TEXT NOT NULL,
    byte_size INTEGER NOT NULL,
    PRIMARY KEY (document_id, asset_id),
    FOREIGN KEY (document_id) REFERENCES designs(document_id)
);

CREATE TABLE IF NOT EXISTS design_approvals (
    document_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    approval_id TEXT NOT NULL,
    spec_digest TEXT NOT NULL,
    asset_manifest_digest TEXT NOT NULL,
    renderer_version TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (document_id, approval_id),
    UNIQUE (document_id, revision, renderer_version),
    FOREIGN KEY (document_id, revision) REFERENCES revisions(document_id, revision)
);
"""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


def _valid_document_id(document_id: object) -> bool:
    return isinstance(document_id, str) and _DOCUMENT_ID_PATTERN.fullmatch(document_id) is not None


def _canonical_json(payload: object) -> str:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def _request_payload(batch: CommandBatch) -> dict:
    dumped = batch.model_dump(mode="json")
    return {
        "actor": dumped["actor"],
        "expected_revision": dumped["expected_revision"],
        "operations": dumped["operations"],
    }


def _fingerprint(batch: CommandBatch) -> str:
    return hashlib.sha256(_canonical_json(_request_payload(batch)).encode("utf-8")).hexdigest()


def _source_texts(batch: CommandBatch) -> list[str]:
    return [
        operation.source_text for operation in batch.operations if isinstance(operation, SetPrice)
    ]


class DesignRepository:
    """A local, single-process design store with explicit paths."""

    def __init__(self, db_path: Path, asset_dir: Path) -> None:
        self.db_path = Path(db_path)
        self.asset_dir = Path(asset_dir)
        self._prepare_paths()
        self._initialize()

    # ------------------------------------------------------------------ paths

    def _prepare_paths(self) -> None:
        try:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            raise StorageError() from None

        if self.asset_dir.is_symlink():
            raise AssetInvalidError()
        if self.asset_dir.exists() and not self.asset_dir.is_dir():
            raise AssetInvalidError()
        try:
            self.asset_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            raise AssetInvalidError() from None
        if self.asset_dir.is_symlink() or not self.asset_dir.is_dir():
            raise AssetInvalidError()

    # ------------------------------------------------------------- connections

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.db_path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA busy_timeout = 10000")
            connection.execute("PRAGMA foreign_keys = ON")
            yield connection
        finally:
            connection.close()

    @contextmanager
    def _write_transaction(self) -> Iterator[sqlite3.Connection]:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
            except BaseException:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise
            else:
                connection.execute("COMMIT")

    def _initialize(self) -> None:
        try:
            with self._connect() as connection:
                connection.executescript(_SCHEMA)
        except sqlite3.Error:
            raise StorageError() from None

    # ------------------------------------------------------------------ assets

    def _asset_path(self, sha256: str) -> Path:
        return self.asset_dir / f"{sha256}{_ASSET_FILE_SUFFIX}"

    def _verify_regular_file(self, path: Path, *, expected_size: int | None = None) -> None:
        try:
            metadata = os.lstat(path)
        except OSError:
            raise AssetInvalidError() from None
        if not stat.S_ISREG(metadata.st_mode):
            raise AssetInvalidError()
        if expected_size is not None and metadata.st_size != expected_size:
            raise AssetInvalidError()

    def _contained_asset_path(self, path: Path) -> None:
        try:
            resolved_root = self.asset_dir.resolve(strict=True)
            resolved = path.resolve(strict=True)
        except OSError:
            raise AssetInvalidError() from None
        if resolved.parent != resolved_root:
            raise AssetInvalidError()

    def _read_published_asset(self, *, sha256: str, byte_size: int) -> bytes:
        path = self._asset_path(sha256)
        self._verify_regular_file(path, expected_size=byte_size)
        self._contained_asset_path(path)

        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(path, flags)
        except OSError:
            raise AssetInvalidError() from None
        try:
            with os.fdopen(descriptor, "rb") as handle:
                raw = handle.read(MAX_ASSET_BYTES + 1)
        except OSError:
            raise AssetInvalidError() from None

        if len(raw) > MAX_ASSET_BYTES or len(raw) != byte_size:
            raise AssetInvalidError()
        digest = hashlib.sha256(raw).hexdigest()
        if not hmac.compare_digest(digest, sha256):
            raise AssetInvalidError()
        return raw

    def _publish_asset(self, *, sha256: str, raw: bytes) -> None:
        final_path = self._asset_path(sha256)
        try:
            metadata = os.lstat(final_path)
        except FileNotFoundError:
            pass
        else:
            if not stat.S_ISREG(metadata.st_mode):
                raise AssetInvalidError()
            self._read_published_asset(sha256=sha256, byte_size=len(raw))
            return

        temporary = self.asset_dir / f".tmp-{uuid.uuid4().hex}"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(temporary, flags, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())

            try:
                metadata = os.lstat(final_path)
            except FileNotFoundError:
                os.replace(temporary, final_path)
            else:
                if not stat.S_ISREG(metadata.st_mode):
                    raise AssetInvalidError()
                existing = self._read_published_asset(sha256=sha256, byte_size=len(raw))
                if not hmac.compare_digest(hashlib.sha256(existing).hexdigest(), sha256):
                    raise AssetInvalidError()
                temporary.unlink(missing_ok=True)
        except OSError:
            raise AssetInvalidError() from None
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    @staticmethod
    def _detect_media_type(raw: bytes) -> str:
        try:
            with Image.open(BytesIO(raw)) as image:
                image_format = image.format
        except (UnidentifiedImageError, OSError, SyntaxError, ValueError):
            raise AssetInvalidError() from None
        media_type = ALLOWED_FORMATS.get(image_format or "")
        if media_type is None:
            raise AssetInvalidError()
        return media_type

    # ------------------------------------------------------------------ create

    def create(
        self,
        *,
        title: str,
        price: Decimal,
        product_bytes: bytes,
    ) -> DesignDocument:
        """Validate the product and persist a new unique design at revision 1."""

        if isinstance(product_bytes, (bytearray, memoryview)):
            raw = bytes(product_bytes)
        elif isinstance(product_bytes, bytes):
            raw = product_bytes
        else:
            raise AssetInvalidError()

        try:
            template = create_template(
                title=title,
                price=price,
                product_asset_id="product",
            )
        except (ValidationError, TypeError, ValueError):
            raise InvalidCommandError() from None

        try:
            render_svg(template, assets={"product": raw})
        except DesignRenderError:
            raise AssetInvalidError() from None

        digest = hashlib.sha256(raw).hexdigest()
        media_type = self._detect_media_type(raw)
        asset_ref = AssetRef(
            asset_id="product",
            sha256=digest,
            media_type=media_type,
            byte_size=len(raw),
        )

        payload = template.model_dump(mode="json")
        payload["document_id"] = f"doc_{uuid.uuid4().hex}"
        payload["asset_refs"] = [asset_ref.model_dump(mode="json")]
        try:
            document = DesignDocument.model_validate(payload)
        except ValidationError:
            raise InvalidCommandError() from None

        try:
            render_svg(document, assets={"product": raw})
        except DesignRenderError:
            raise AssetInvalidError() from None

        self._publish_asset(sha256=digest, raw=raw)

        created_at = _now()
        try:
            with self._write_transaction() as connection:
                connection.execute(
                    "INSERT INTO designs (document_id, created_at) VALUES (?, ?)",
                    (document.document_id, created_at),
                )
                connection.execute(
                    """
                    INSERT INTO revisions
                        (document_id, revision, parent_revision, document_json, command_id, created_at)
                    VALUES (?, ?, NULL, ?, NULL, ?)
                    """,
                    (
                        document.document_id,
                        document.revision,
                        document.model_dump_json(),
                        created_at,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO heads (document_id, revision, undo_stack, redo_stack)
                    VALUES (?, ?, ?, ?)
                    """,
                    (document.document_id, document.revision, "[]", "[]"),
                )
                connection.execute(
                    """
                    INSERT INTO assets
                        (document_id, asset_id, sha256, media_type, byte_size)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        document.document_id,
                        asset_ref.asset_id,
                        asset_ref.sha256,
                        asset_ref.media_type,
                        asset_ref.byte_size,
                    ),
                )
        except sqlite3.Error:
            raise StorageError() from None

        return document

    # --------------------------------------------------------------------- get

    def get(self, document_id: str, revision: int | None = None) -> DesignDocument:
        """Read the latest or one exact historical revision; never fall back."""

        if not _valid_document_id(document_id):
            raise NotFoundError()
        if revision is not None and (type(revision) is not int or revision < 1):
            raise NotFoundError()

        if revision is None:
            query = """
                SELECT revisions.document_json
                FROM heads
                JOIN revisions
                  ON revisions.document_id = heads.document_id
                 AND revisions.revision = heads.revision
                WHERE heads.document_id = ?
            """
            parameters: tuple[object, ...] = (document_id,)
        else:
            query = """
                SELECT document_json
                FROM revisions
                WHERE document_id = ? AND revision = ?
            """
            parameters = (document_id, revision)

        try:
            with self._connect() as connection:
                row = connection.execute(query, parameters).fetchone()
        except sqlite3.Error:
            raise StorageError() from None
        if row is None:
            raise NotFoundError()
        try:
            return DesignDocument.model_validate_json(row["document_json"])
        except ValidationError:
            raise StorageError() from None

    # ------------------------------------------------------------------- reads

    def read_assets(self, document: DesignDocument) -> dict[str, bytes]:
        """Read only indexed assets and verify size, full hash and real format."""

        if not _valid_document_id(document.document_id):
            raise NotFoundError()

        try:
            with self._connect() as connection:
                rows = connection.execute(
                    """
                    SELECT asset_id, sha256, media_type, byte_size
                    FROM assets
                    WHERE document_id = ?
                    """,
                    (document.document_id,),
                ).fetchall()
        except sqlite3.Error:
            raise StorageError() from None

        index = {row["asset_id"]: row for row in rows}
        assets: dict[str, bytes] = {}
        for ref in document.asset_refs:
            if ref.sha256 is None or ref.media_type is None or ref.byte_size is None:
                raise AssetInvalidError()
            row = index.get(ref.asset_id)
            if row is None:
                raise AssetInvalidError()
            if not hmac.compare_digest(row["sha256"], ref.sha256):
                raise AssetInvalidError()
            if row["media_type"] != ref.media_type or row["byte_size"] != ref.byte_size:
                raise AssetInvalidError()
            assets[ref.asset_id] = self._read_published_asset(
                sha256=ref.sha256,
                byte_size=ref.byte_size,
            )

        try:
            render_svg(document, assets=assets)
        except DesignRenderError:
            raise AssetInvalidError() from None
        return assets

    # -------------------------------------------------------- local approvals

    def _approval_snapshot(
        self, connection: sqlite3.Connection, document_id: str
    ) -> tuple[DesignDocument, str, str]:
        if not _valid_document_id(document_id):
            raise NotFoundError()
        row = connection.execute(
            "SELECT revision FROM heads WHERE document_id = ?", (document_id,)
        ).fetchone()
        if row is None:
            raise NotFoundError()
        document = self._load_revision(connection, document_id, row["revision"])
        spec_digest, asset_digest = snapshot_digests(document)
        return document, spec_digest, asset_digest

    @staticmethod
    def _approval_record(
        connection: sqlite3.Connection, document_id: str, revision: int
    ) -> sqlite3.Row | None:
        return connection.execute(
            """
            SELECT approval_id, spec_digest, asset_manifest_digest,
                   renderer_version, created_at
            FROM design_approvals
            WHERE document_id = ? AND revision = ? AND renderer_version = ?
            """,
            (document_id, revision, RENDERER_VERSION),
        ).fetchone()

    @staticmethod
    def _approval_matches(record: sqlite3.Row, spec_digest: str, asset_digest: str) -> bool:
        return (
            hmac.compare_digest(record["spec_digest"], spec_digest)
            and hmac.compare_digest(record["asset_manifest_digest"], asset_digest)
            and hmac.compare_digest(record["renderer_version"], RENDERER_VERSION)
        )

    def approval_target(self, document_id: str) -> dict:
        """Expose current immutable digests; historical approvals never carry forward."""
        try:
            with self._write_transaction() as connection:
                document, spec_digest, asset_digest = self._approval_snapshot(
                    connection, document_id
                )
                record = self._approval_record(connection, document_id, document.revision)
                approved = record is not None and self._approval_matches(
                    record, spec_digest, asset_digest
                )
                if approved:
                    # Do not advertise export-readiness if indexed bytes are corrupt.
                    self.read_assets(document)
                return {
                    "document_id": document.document_id,
                    "revision": document.revision,
                    "spec_digest": spec_digest,
                    "asset_manifest_digest": asset_digest,
                    "renderer_version": RENDERER_VERSION,
                    "approved": approved,
                    "approval_id": record["approval_id"] if approved else None,
                }
        except sqlite3.Error:
            raise StorageError() from None

    def approval_history(self, document_id: str) -> dict:
        """Read persisted local acknowledgements, including superseded revisions.

        This is a database record history, not tamper-proof audit or reviewer
        authentication. No old record grants export rights to a new head.
        """
        try:
            with self._connect() as connection:
                # Keep the head, digests and approval rows on one SQLite
                # read snapshot even if a concurrent command advances the head.
                connection.execute("BEGIN")
                document, spec_digest, asset_digest = self._approval_snapshot(
                    connection, document_id
                )
                rows = connection.execute(
                    """
                    SELECT revision, approval_id, spec_digest,
                           asset_manifest_digest, renderer_version, created_at
                    FROM design_approvals
                    WHERE document_id = ?
                    ORDER BY revision ASC, created_at ASC, approval_id ASC
                    """,
                    (document_id,),
                ).fetchall()
                entries = []
                for record in rows:
                    is_current = record["revision"] == document.revision
                    matches_current = is_current and self._approval_matches(
                        record, spec_digest, asset_digest
                    )
                    entries.append(
                        {
                            "revision": record["revision"],
                            "approval_id": record["approval_id"],
                            "spec_digest": record["spec_digest"],
                            "asset_manifest_digest": record["asset_manifest_digest"],
                            "renderer_version": record["renderer_version"],
                            "created_at": record["created_at"],
                            "is_current_head": is_current,
                            "matches_current_snapshot": matches_current,
                        }
                    )
                return {
                    "document_id": document.document_id,
                    "current_revision": document.revision,
                    "current_renderer_version": RENDERER_VERSION,
                    "approvals": entries,
                }
        except sqlite3.Error:
            raise StorageError() from None

    def approve(self, document_id: str, request: ApprovalRequest) -> dict:
        """Atomically bind one local operator acknowledgement to the exact head."""
        try:
            with self._write_transaction() as connection:
                document, spec_digest, asset_digest = self._approval_snapshot(
                    connection, document_id
                )
                if request.expected_revision != document.revision:
                    raise RevisionConflictError()

                existing_by_key = connection.execute(
                    """
                    SELECT revision, spec_digest, asset_manifest_digest, renderer_version
                    FROM design_approvals
                    WHERE document_id = ? AND approval_id = ?
                    """,
                    (document_id, request.approval_id),
                ).fetchone()
                if existing_by_key is not None and (
                    existing_by_key["revision"] != request.expected_revision
                    or not hmac.compare_digest(
                        existing_by_key["renderer_version"], RENDERER_VERSION
                    )
                    or not hmac.compare_digest(existing_by_key["spec_digest"], request.spec_digest)
                    or not hmac.compare_digest(
                        existing_by_key["asset_manifest_digest"], request.asset_manifest_digest
                    )
                ):
                    raise IdempotencyConflictError()

                if not (
                    hmac.compare_digest(request.spec_digest, spec_digest)
                    and hmac.compare_digest(request.asset_manifest_digest, asset_digest)
                ):
                    raise ApprovalDigestConflictError()

                existing = self._approval_record(connection, document_id, document.revision)
                if existing is not None and existing["approval_id"] != request.approval_id:
                    raise ApprovalConflictError()
                if existing is not None and not self._approval_matches(
                    existing, spec_digest, asset_digest
                ):
                    raise ApprovalDigestConflictError()

                # Verify complete asset bytes against their immutable refs before
                # recording approval, even if an idempotent replay already exists.
                self._assert_bound_assets(document)
                self._assert_assets_indexed(connection, document)
                self.read_assets(document)

                replayed = existing is not None
                if not replayed:
                    connection.execute(
                        """
                        INSERT INTO design_approvals
                            (document_id, revision, approval_id, spec_digest,
                             asset_manifest_digest, renderer_version, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            document_id,
                            document.revision,
                            request.approval_id,
                            spec_digest,
                            asset_digest,
                            RENDERER_VERSION,
                            _now(),
                        ),
                    )
                return {
                    "document_id": document_id,
                    "revision": document.revision,
                    "approval_id": request.approval_id,
                    "spec_digest": spec_digest,
                    "asset_manifest_digest": asset_digest,
                    "renderer_version": RENDERER_VERSION,
                    "replayed": replayed,
                }
        except sqlite3.Error:
            raise StorageError() from None

    def approved_svg(self, document_id: str, revision: int) -> str:
        """Render only the currently approved head, not stale history."""
        if type(revision) is not int or revision < 1:
            raise NotFoundError()
        try:
            with self._write_transaction() as connection:
                document, spec_digest, asset_digest = self._approval_snapshot(
                    connection, document_id
                )
                if revision != document.revision:
                    raise RevisionConflictError()
                record = self._approval_record(connection, document_id, revision)
                if record is None or not self._approval_matches(record, spec_digest, asset_digest):
                    raise ApprovalRequiredError()
                self._assert_bound_assets(document)
                self._assert_assets_indexed(connection, document)
                assets = self.read_assets(document)
                return render_svg(document, assets=assets)
        except sqlite3.Error:
            raise StorageError() from None

    # ----------------------------------------------------------------- execute

    def execute(self, document_id: str, command: CommandBatch) -> CommandResult:
        """Apply one command batch atomically, with revision and idempotency checks."""

        if not _valid_document_id(document_id):
            raise NotFoundError()
        batch = command
        fingerprint = _fingerprint(batch)
        request_json = _canonical_json(batch.model_dump(mode="json"))
        source_texts_json = _canonical_json(_source_texts(batch))

        try:
            with self._write_transaction() as connection:
                design = connection.execute(
                    "SELECT document_id FROM designs WHERE document_id = ?",
                    (document_id,),
                ).fetchone()
                if design is None:
                    raise NotFoundError()

                existing = connection.execute(
                    """
                    SELECT fingerprint, result_revision
                    FROM commands
                    WHERE document_id = ? AND command_id = ?
                    """,
                    (document_id, batch.command_id),
                ).fetchone()
                if existing is not None:
                    if not hmac.compare_digest(existing["fingerprint"], fingerprint):
                        raise IdempotencyConflictError()
                    replayed = self._load_revision(
                        connection,
                        document_id,
                        existing["result_revision"],
                    )
                    return CommandResult(
                        document=replayed,
                        command_id=batch.command_id,
                        replayed=True,
                    )

                head = connection.execute(
                    """
                    SELECT revision, undo_stack, redo_stack
                    FROM heads
                    WHERE document_id = ?
                    """,
                    (document_id,),
                ).fetchone()
                if head is None:
                    raise NotFoundError()
                if batch.expected_revision != head["revision"]:
                    raise RevisionConflictError()

                head_revision = head["revision"]
                undo_stack = list(json.loads(head["undo_stack"]))
                redo_stack = list(json.loads(head["redo_stack"]))
                new_revision = head_revision + 1

                if len(batch.operations) == 1 and isinstance(batch.operations[0], Undo):
                    if not undo_stack:
                        raise NothingToUndoError()
                    target_revision = undo_stack.pop()
                    redo_stack.append(head_revision)
                    candidate = self._load_revision(connection, document_id, target_revision)
                    candidate = candidate.model_copy(update={"revision": new_revision})
                elif len(batch.operations) == 1 and isinstance(batch.operations[0], Redo):
                    if not redo_stack:
                        raise NothingToRedoError()
                    target_revision = redo_stack.pop()
                    undo_stack.append(head_revision)
                    candidate = self._load_revision(connection, document_id, target_revision)
                    candidate = candidate.model_copy(update={"revision": new_revision})
                else:
                    if any(isinstance(op, (Undo, Redo)) for op in batch.operations):
                        raise InvalidCommandError()
                    candidate = self._load_revision(connection, document_id, head_revision)
                    try:
                        for operation in batch.operations:
                            candidate = self._apply_operation(candidate, operation)
                    except ValidationError:
                        raise InvalidCommandError() from None
                    candidate = candidate.model_copy(update={"revision": new_revision})
                    undo_stack.append(head_revision)
                    redo_stack = []

                try:
                    candidate = DesignDocument.model_validate(candidate.model_dump(mode="json"))
                except ValidationError:
                    raise InvalidCommandError() from None

                self._assert_bound_assets(candidate)
                self._assert_assets_indexed(connection, candidate)

                connection.execute(
                    """
                    INSERT INTO revisions
                        (document_id, revision, parent_revision, document_json, command_id, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        document_id,
                        new_revision,
                        head_revision,
                        candidate.model_dump_json(),
                        batch.command_id,
                        _now(),
                    ),
                )
                connection.execute(
                    """
                    UPDATE heads
                    SET revision = ?, undo_stack = ?, redo_stack = ?
                    WHERE document_id = ?
                    """,
                    (
                        new_revision,
                        _canonical_json(undo_stack),
                        _canonical_json(redo_stack),
                        document_id,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO commands
                        (document_id, command_id, fingerprint, result_revision, actor,
                         request_json, source_texts_json, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        document_id,
                        batch.command_id,
                        fingerprint,
                        new_revision,
                        batch.actor,
                        request_json,
                        source_texts_json,
                        _now(),
                    ),
                )
                return CommandResult(
                    document=candidate,
                    command_id=batch.command_id,
                    replayed=False,
                )
        except sqlite3.Error:
            raise StorageError() from None

    # --------------------------------------------------------------- internals

    def _load_revision(
        self,
        connection: sqlite3.Connection,
        document_id: str,
        revision: int,
    ) -> DesignDocument:
        row = connection.execute(
            """
            SELECT document_json
            FROM revisions
            WHERE document_id = ? AND revision = ?
            """,
            (document_id, revision),
        ).fetchone()
        if row is None:
            raise StorageError()
        try:
            return DesignDocument.model_validate_json(row["document_json"])
        except ValidationError:
            raise StorageError() from None

    @staticmethod
    def _assert_bound_assets(document: DesignDocument) -> None:
        for ref in document.asset_refs:
            if ref.sha256 is None or ref.media_type is None or ref.byte_size is None:
                raise InvalidCommandError()

    @staticmethod
    def _assert_assets_indexed(
        connection: sqlite3.Connection,
        document: DesignDocument,
    ) -> None:
        for ref in document.asset_refs:
            row = connection.execute(
                """
                SELECT sha256, media_type, byte_size
                FROM assets
                WHERE document_id = ? AND asset_id = ?
                """,
                (document.document_id, ref.asset_id),
            ).fetchone()
            if row is None:
                raise AssetInvalidError()
            if not hmac.compare_digest(row["sha256"], ref.sha256 or ""):
                raise AssetInvalidError()
            if row["media_type"] != ref.media_type or row["byte_size"] != ref.byte_size:
                raise AssetInvalidError()

    @staticmethod
    def _find_node(document: DesignDocument, node_id: str):
        for node in document.nodes:
            if node.node_id == node_id:
                return node
        raise InvalidCommandError()

    @staticmethod
    def _replace_node(document: DesignDocument, node) -> DesignDocument:
        nodes = [node if item.node_id == node.node_id else item for item in document.nodes]
        return document.model_copy(update={"nodes": nodes})

    def _apply_operation(self, document: DesignDocument, operation) -> DesignDocument:
        if isinstance(operation, SetPrice):
            return self._apply_set_price(document, operation)

        node = self._find_node(document, operation.node_id)
        if node.locked and not isinstance(operation, SetNodeLock):
            raise LockedNodeError()

        if isinstance(operation, SetText):
            return self._apply_set_text(document, node, operation)
        if isinstance(operation, SetStyle):
            return self._apply_set_style(document, node, operation)
        if isinstance(operation, TransformNode):
            return self._apply_transform(document, node, operation)
        if isinstance(operation, SetNodeLock):
            return self._apply_set_lock(document, node, operation)
        if isinstance(operation, ReorderNode):
            return self._apply_reorder(document, node, operation)
        raise InvalidCommandError()

    @staticmethod
    def _apply_set_text(document: DesignDocument, node, operation: SetText) -> DesignDocument:
        if not isinstance(node, TextNode) or node.role not in {"title", "subtitle", "cta"}:
            raise InvalidCommandError()
        lines = DesignRepository._wrap_text(operation.text, node)
        return DesignRepository._replace_node(document, node.model_copy(update={"lines": lines}))

    @staticmethod
    def _wrap_text(text: str, node: TextNode) -> list[TextLine]:
        first_line = node.lines[0]
        if len(node.lines) > 1:
            spacing = node.lines[1].y - node.lines[0].y
            line_height = spacing if spacing > 0 else max(1.0, node.font_size * 1.2)
        else:
            line_height = max(1.0, node.font_size * 1.2)

        base_width = max(1, int(node.width // max(node.font_size, 1.0)))
        characters_per_line = max(base_width, math.ceil(len(text) / 12))
        chunks = split_text_chunks(text, characters_per_line)
        return [
            TextLine(
                text=chunk,
                x=first_line.x,
                y=first_line.y + index * line_height,
            )
            for index, chunk in enumerate(chunks)
        ]

    @staticmethod
    def _apply_set_price(document: DesignDocument, operation: SetPrice) -> DesignDocument:
        price_nodes = [node for node in document.nodes if node.role == "price"]
        if len(price_nodes) != 1 or not isinstance(price_nodes[0], TextNode):
            raise InvalidCommandError()
        price_node = price_nodes[0]
        if price_node.locked:
            raise LockedNodeError()

        facts = BusinessFacts(price=operation.price)
        first_line = price_node.lines[0]
        line = TextLine(
            text=format_price_text(facts.price, facts.currency),
            x=first_line.x,
            y=first_line.y,
        )
        updated_node = price_node.model_copy(update={"lines": [line]})
        return DesignRepository._replace_node(
            document.model_copy(update={"facts": facts}),
            updated_node,
        )

    @staticmethod
    def _apply_set_style(document: DesignDocument, node, operation: SetStyle) -> DesignDocument:
        changes: dict[str, object] = {}
        if operation.opacity is not None:
            changes["opacity"] = operation.opacity

        if isinstance(node, TextNode):
            if operation.color is not None:
                changes["color"] = operation.color
            if operation.font_size is not None:
                changes["font_size"] = operation.font_size
        elif isinstance(node, RectNode):
            if operation.font_size is not None:
                raise InvalidCommandError()
            if operation.color is not None:
                changes["fill"] = operation.color
        elif isinstance(node, ImageNode):
            if operation.color is not None or operation.font_size is not None:
                raise InvalidCommandError()
        else:
            raise InvalidCommandError()

        return DesignRepository._replace_node(document, node.model_copy(update=changes))

    @staticmethod
    def _apply_transform(
        document: DesignDocument,
        node,
        operation: TransformNode,
    ) -> DesignDocument:
        changes: dict[str, object] = {}
        if operation.x is not None:
            changes["x"] = operation.x
        if operation.y is not None:
            changes["y"] = operation.y
        if operation.width is not None:
            changes["width"] = operation.width
        if operation.height is not None:
            changes["height"] = operation.height
        if operation.rotation is not None:
            changes["rotation"] = operation.rotation

        if isinstance(node, TextNode):
            delta_x = operation.x - node.x if operation.x is not None else 0.0
            delta_y = operation.y - node.y if operation.y is not None else 0.0
            if delta_x or delta_y:
                changes["lines"] = [
                    line.model_copy(update={"x": line.x + delta_x, "y": line.y + delta_y})
                    for line in node.lines
                ]

        return DesignRepository._replace_node(document, node.model_copy(update=changes))

    @staticmethod
    def _apply_set_lock(document: DesignDocument, node, operation: SetNodeLock) -> DesignDocument:
        return DesignRepository._replace_node(
            document,
            node.model_copy(update={"locked": operation.locked}),
        )

    @staticmethod
    def _apply_reorder(document: DesignDocument, node, operation: ReorderNode) -> DesignDocument:
        nodes = list(document.nodes)
        if operation.index >= len(nodes):
            raise InvalidCommandError()
        current_index = nodes.index(node)
        nodes.pop(current_index)
        nodes.insert(operation.index, node)
        return document.model_copy(update={"nodes": nodes})
