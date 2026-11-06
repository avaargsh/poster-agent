"""Opt-in local DesignDocument OCR execution with durable, fail-closed journal.

The journal proves that this process recorded an attempt and observations. It
does NOT independently attest model identity, weights, human review or release.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
from threading import Lock
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from app.ocr_provider import OCRUnavailable, recognize

from .errors import (
    DesignError,
    IdempotencyConflictError,
    NotFoundError,
    RevisionConflictError,
    StorageError,
)
from .models import PAGE_MAX_SIZE, RENDERER_VERSION, validate_plain_text
from .rasterization import rasterize_svg
from .repository import DesignRepository, _now

_SHA256 = r"^[0-9a-f]{64}$"
_OPERATION_ID = r"^ocr_[0-9a-f]{32}$"

# PaddleOCR is cached process-wide by the adapter. Do not concurrently call
# predict() on the same cached engine unless thread-safety is independently proven.
_LOCAL_OCR_ENGINE_LOCK = Lock()


class OCRTargetIncomplete(DesignError):
    code = "OCR_TARGET_INCOMPLETE"

    @property
    def default_message(self) -> str:
        return "approved design is missing a required OCR text field"


class OCRSourceMismatch(DesignError):
    code = "OCR_SOURCE_MISMATCH"

    @property
    def default_message(self) -> str:
        return "OCR input does not match approved raster hashes"


class OCRRunRequest(BaseModel):
    """Caller must explicitly bind operation identity to already inspected pixels."""

    model_config = ConfigDict(extra="forbid")

    operation_id: Annotated[str, StringConstraints(pattern=_OPERATION_ID)]
    svg_sha256: Annotated[str, StringConstraints(pattern=_SHA256)]
    png_sha256: Annotated[str, StringConstraints(pattern=_SHA256)]


class _CapturedLine(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: Annotated[str, StringConstraints(min_length=1, max_length=200)]
    score: Annotated[float, Field(strict=True, ge=0, le=1, allow_inf_nan=False)]
    bbox: (
        Annotated[
            list[Annotated[int, Field(strict=True, ge=0, le=PAGE_MAX_SIZE)]],
            Field(min_length=4, max_length=4),
        ]
        | None
    ) = None

    @field_validator("text")
    @classmethod
    def valid_text(cls, value: str) -> str:
        return validate_plain_text(value)


def _canonical_json(value: object) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def _request_fingerprint(
    document_id: str, revision: int, payload: OCRRunRequest, principal: str, policy: str
) -> str:
    value = {
        "document_id": document_id,
        "revision": revision,
        "operation_id": payload.operation_id,
        "svg_sha256": payload.svg_sha256,
        "png_sha256": payload.png_sha256,
        "principal_id": principal,
        "policy_digest": policy,
    }
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _row_result(row: sqlite3.Row, *, replayed: bool) -> dict:
    """Never promote stored adapter observations to verification or approval."""
    return {
        "operation_id": row["operation_id"],
        "document_id": row["document_id"],
        "revision": row["revision"],
        "approval_id": row["approval_id"],
        "principal_id": row["principal_id"],
        "spec_digest": row["spec_digest"],
        "asset_manifest_digest": row["asset_manifest_digest"],
        "renderer_version": row["renderer_version"],
        "svg_sha256": row["svg_sha256"],
        "png_sha256": row["png_sha256"],
        "rasterizer": row["rasterizer"],
        "status": row["status"],
        "reason": row["reason"],
        "observations": (
            json.loads(row["observations_json"])
            if row["status"] == "EXECUTED" and row["observations_json"] is not None
            else []
        ),
        "runtime": json.loads(row["runtime_json"]) if row["runtime_json"] else None,
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
        "replayed": replayed,
        "runtime_attested": False,
        "quality_verified": False,
        "release_ready": False,
    }


class OCRRunJournal:
    """The DesignRepository remains the only authority and SQLite writer."""

    def __init__(self, repository: DesignRepository) -> None:
        self.repository = repository

    @staticmethod
    def _find(connection: sqlite3.Connection, operation_id: str) -> sqlite3.Row | None:
        return connection.execute(
            "SELECT * FROM design_ocr_runs WHERE operation_id = ?", (operation_id,)
        ).fetchone()

    @staticmethod
    def _authorize_row(
        row: sqlite3.Row,
        *,
        document_id: str,
        revision: int,
        principal: str,
        policy: str,
        fingerprint: str | None,
    ) -> None:
        if row["document_id"] != document_id or row["revision"] != revision:
            raise IdempotencyConflictError()
        if row["principal_id"] != principal or row["policy_digest"] != policy:
            raise NotFoundError()
        if fingerprint is not None and not hmac.compare_digest(
            row["request_fingerprint"], fingerprint
        ):
            raise IdempotencyConflictError()

    def get(
        self,
        document_id: str,
        revision: int,
        operation_id: str,
        *,
        principal: str,
        policy: str,
        fingerprint: str | None = None,
    ) -> dict | None:
        try:
            with self.repository._connect() as connection:
                row = self._find(connection, operation_id)
                if row is None:
                    return None
                self._authorize_row(
                    row,
                    document_id=document_id,
                    revision=revision,
                    principal=principal,
                    policy=policy,
                    fingerprint=fingerprint,
                )
                return _row_result(row, replayed=fingerprint is not None)
        except sqlite3.Error:
            raise StorageError() from None

    def begin(
        self,
        document_id: str,
        revision: int,
        payload: OCRRunRequest,
        *,
        principal: str,
        policy: str,
        fingerprint: str,
        rasterizer: str,
    ) -> tuple[dict, bool]:
        """Atomically claim an operation after rechecking current approval."""
        try:
            with self.repository._write_transaction() as connection:
                existing = self._find(connection, payload.operation_id)
                if existing is not None:
                    self._authorize_row(
                        existing,
                        document_id=document_id,
                        revision=revision,
                        principal=principal,
                        policy=policy,
                        fingerprint=fingerprint,
                    )
                    return _row_result(existing, replayed=True), False

                document, spec_digest, asset_digest, svg = self.repository._approved_snapshot(
                    connection,
                    document_id,
                    revision,
                    required_policy_digest=policy,
                )
                svg_hash = hashlib.sha256(svg.encode("utf-8")).hexdigest()
                if not hmac.compare_digest(svg_hash, payload.svg_sha256):
                    raise OCRSourceMismatch()
                approval = self.repository._approval_record(connection, document_id, revision)
                if approval is None:
                    raise RevisionConflictError()
                connection.execute(
                    """
                    INSERT INTO design_ocr_runs (
                        operation_id, document_id, revision, approval_id,
                        principal_id, policy_digest, request_fingerprint,
                        spec_digest, asset_manifest_digest, renderer_version,
                        svg_sha256, png_sha256, rasterizer, status, started_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'EXECUTING', ?)
                    """,
                    (
                        payload.operation_id,
                        document.document_id,
                        revision,
                        approval["approval_id"],
                        principal,
                        policy,
                        fingerprint,
                        spec_digest,
                        asset_digest,
                        RENDERER_VERSION,
                        payload.svg_sha256,
                        payload.png_sha256,
                        rasterizer,
                        _now(),
                    ),
                )
                row = self._find(connection, payload.operation_id)
                return _row_result(row, replayed=False), True
        except sqlite3.Error:
            raise StorageError() from None

    def finish(
        self,
        operation_id: str,
        *,
        status: str,
        observations: list[dict] | None = None,
        runtime: dict | None = None,
        reason: str | None = None,
    ) -> dict:
        """Terminal write rechecks current approval after the model returned."""
        try:
            with self.repository._write_transaction() as connection:
                row = self._find(connection, operation_id)
                if row is None:
                    raise NotFoundError()
                if row["status"] != "EXECUTING":
                    return _row_result(row, replayed=True)

                try:
                    document, spec, assets, svg = self.repository._approved_snapshot(
                        connection,
                        row["document_id"],
                        row["revision"],
                        required_policy_digest=row["policy_digest"],
                    )
                    svg_hash = hashlib.sha256(svg.encode("utf-8")).hexdigest()
                    if (
                        RENDERER_VERSION != row["renderer_version"]
                        or spec != row["spec_digest"]
                        or assets != row["asset_manifest_digest"]
                        or not hmac.compare_digest(svg_hash, row["svg_sha256"])
                    ):
                        raise OCRSourceMismatch()
                except DesignError:
                    status = "UNKNOWN"
                    reason = "SOURCE_CHANGED"
                    observations = None
                    runtime = None

                connection.execute(
                    """
                    UPDATE design_ocr_runs
                    SET status = ?, observations_json = ?, runtime_json = ?,
                        reason = ?, finished_at = ?
                    WHERE operation_id = ? AND status = 'EXECUTING'
                    """,
                    (
                        status,
                        _canonical_json(observations) if status == "EXECUTED" else None,
                        _canonical_json(runtime) if runtime is not None else None,
                        reason,
                        _now(),
                        operation_id,
                    ),
                )
                return _row_result(self._find(connection, operation_id), replayed=False)
        except sqlite3.Error:
            raise StorageError() from None


def _recognize_serialized(image: Path) -> list[dict]:
    """Keep local cached PaddleOCR inference single-flight within this worker."""
    with _LOCAL_OCR_ENGINE_LOCK:
        return recognize(image)


def _capture_model_output(rows: object) -> tuple[list[dict], dict]:
    if not isinstance(rows, list) or len(rows) > 200:
        raise ValueError("unexpected OCR line collection")
    observed = [_CapturedLine.model_validate(item).model_dump(mode="json") for item in rows]
    telemetry = getattr(rows, "runtime", None)
    telemetry = telemetry if isinstance(telemetry, dict) else {}

    def safe_text(key: str) -> str | None:
        value = telemetry.get(key)
        return value[:128] if isinstance(value, str) else None

    runtime = {
        "source": "local_paddleocr_adapter",
        "adapter_reported_source": safe_text("source"),
        "paddleocr_package_version": safe_text("paddleocr_package_version"),
        "paddlepaddle_package_version": safe_text("paddlepaddle_package_version"),
        "python_version": safe_text("python_version"),
        "machine": safe_text("machine"),
        "line_count": len(observed),
        "model_weights_sha256": None,
        "attested": False,
    }
    return observed, runtime


def submit_ocr_run(
    repository: DesignRepository,
    document_id: str,
    revision: int,
    payload: OCRRunRequest,
    *,
    principal: str,
    policy: str,
) -> tuple[dict, bool]:
    """Explicit synchronous operation; retries never re-invoke an admitted run."""
    journal = OCRRunJournal(repository)
    fingerprint = _request_fingerprint(document_id, revision, payload, principal, policy)
    existing = journal.get(
        document_id,
        revision,
        payload.operation_id,
        principal=principal,
        policy=policy,
        fingerprint=fingerprint,
    )
    if existing is not None:
        return existing, False

    target = repository.ocr_target(document_id, revision, required_policy_digest=policy)
    if target["missing_fields"]:
        raise OCRTargetIncomplete()

    svg = repository.approved_svg(document_id, revision, required_policy_digest=policy)
    raster = rasterize_svg(svg)
    if not hmac.compare_digest(payload.svg_sha256, raster.svg_sha256) or not hmac.compare_digest(
        payload.png_sha256, raster.png_sha256
    ):
        raise OCRSourceMismatch()

    admitted, new = journal.begin(
        document_id,
        revision,
        payload,
        principal=principal,
        policy=policy,
        fingerprint=fingerprint,
        rasterizer=raster.rasterizer,
    )
    if not new:
        return admitted, False

    try:
        # Private tempfile and explicit invocation only. Existing PaddleOCR
        # may require local model provisioning; default CI never invokes it.
        with TemporaryDirectory(prefix="poster-design-ocr-") as folder:
            image = Path(folder) / "approved.png"
            image.write_bytes(raster.png)
            rows = _recognize_serialized(image)
            observations, runtime = _capture_model_output(rows)
    except OCRUnavailable:
        result = journal.finish(
            payload.operation_id, status="UNAVAILABLE", reason="MODEL_UNAVAILABLE"
        )
    except Exception:
        # After durable admission, failures can be ambiguous (e.g. model ran
        # but its reply was lost). Do not call the provider again on replay.
        result = journal.finish(
            payload.operation_id, status="UNKNOWN", reason="OCR_EXECUTION_AMBIGUOUS"
        )
    else:
        result = journal.finish(
            payload.operation_id, status="EXECUTED", observations=observations, runtime=runtime
        )
    return result, True
