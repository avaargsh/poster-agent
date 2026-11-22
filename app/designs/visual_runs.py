"""Durable, opt-in local VLM shadow execution for approved DesignDocuments.

Execution is single-flight in this process; SQLite records an operation before
any /api/chat call. Retry only reads that record and never repeats inference.
An UNKNOWN result is not evidence of failure and cannot be retried in place.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import sqlite3
from threading import Lock
from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints

from .errors import DesignError, IdempotencyConflictError, NotFoundError, StorageError
from .models import RENDERER_VERSION
from .rasterization import RasterInvalid, RasterUnavailable, rasterize_svg
from .repository import DesignRepository, _now
from .visual_critic import VisualSourceMismatch
from .vlm_local import (
    _finite_json,
    inspect_approved_png,
    load_vlm_profile,
    parse_findings,
    verify_ollama_model,
)

_VLM_LOCK = Lock()


class VLMExecutionBusy(DesignError):
    code = "VLM_EXECUTION_BUSY"

    @property
    def default_message(self) -> str:
        return "another local VLM shadow execution is in progress"


class VLMJournalCorrupt(DesignError):
    """Persisted local findings or runtime data cannot be safely replayed."""

    code = "VLM_JOURNAL_CORRUPT"

    @property
    def default_message(self) -> str:
        return "stored visual shadow operation has invalid evidence"


class VisualRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation_id: Annotated[str, StringConstraints(pattern=r"^vlm_[0-9a-f]{32}$")]
    svg_sha256: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
    png_sha256: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


def _json(value: object) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def _fingerprint(
    doc: str,
    revision: int,
    payload: VisualRunRequest,
    principal: str,
    policy: str,
    profile_fingerprint: str,
) -> str:
    canonical = {
        "document_id": doc,
        "revision": revision,
        "operation_id": payload.operation_id,
        "svg_sha256": payload.svg_sha256,
        "png_sha256": payload.png_sha256,
        "principal_id": principal,
        "policy_digest": policy,
        "model_profile_sha256": profile_fingerprint,
    }
    return hashlib.sha256(_json(canonical).encode("utf-8")).hexdigest()


def _result(row: sqlite3.Row, *, replayed: bool) -> dict:
    """Never turn malformed local data into empty positive shadow findings.

    A journal is operator-controlled storage, not trusted model attestation.
    Structural checks only prevent stale/corrupt rows from being presented as
    valid, without asserting visual quality or weight provenance.
    """
    try:
        status = row["status"]
        if status == "EXECUTED":
            raw_findings = row["findings_json"]
            raw_runtime = row["runtime_json"]
            if not isinstance(raw_findings, str) or not isinstance(raw_runtime, str):
                raise ValueError("completed visual run missing evidence")
            findings = parse_findings('{"findings":' + raw_findings + "}")
            if len(raw_runtime) > 4096:
                raise ValueError("visual runtime provenance exceeds limit")
            runtime = _finite_json(raw_runtime)
            if (
                not isinstance(runtime, dict)
                or set(runtime)
                != {
                    "source",
                    "local_model_digest_claim",
                    "execution_evidence",
                    "attested",
                }
                or runtime["source"] != "local_ollama"
                or runtime["execution_evidence"] != "OPERATOR_LOCAL_NOT_ATTESTED"
                or runtime["attested"] is not False
                or not isinstance(runtime["local_model_digest_claim"], str)
                or re.fullmatch(r"sha256:[0-9a-f]{64}", runtime["local_model_digest_claim"]) is None
            ):
                raise ValueError("invalid local-only runtime provenance")
        elif status in {"EXECUTING", "UNKNOWN"}:
            if row["findings_json"] is not None or row["runtime_json"] is not None:
                raise ValueError("unresolved visual operation contains evidence")
            findings, runtime = [], None
        else:
            raise ValueError("unknown visual journal state")
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise VLMJournalCorrupt() from None

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
        "model_name": row["model_name"],
        "model_profile_sha256": row["model_profile_sha256"],
        "status": row["status"],
        "reason": row["reason"],
        "findings": findings,
        "runtime": runtime,
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
        "replayed": replayed,
        "model_execution_attested": False,
        "quality_verified": False,
        "repair_authorized": False,
        "release_ready": False,
    }


class VisualRunJournal:
    """Use the existing DesignRepository transaction and approval authority."""

    def __init__(self, repository: DesignRepository) -> None:
        self.repository = repository

    @staticmethod
    def _find(db: sqlite3.Connection, operation_id: str) -> sqlite3.Row | None:
        return db.execute(
            "SELECT * FROM design_vlm_runs WHERE operation_id = ?", (operation_id,)
        ).fetchone()

    @staticmethod
    def _authorize(
        row: sqlite3.Row,
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
            with self.repository._connect() as db:
                row = self._find(db, operation_id)
                if row is None:
                    return None
                self._authorize(row, document_id, revision, principal, policy, fingerprint)
                return _result(row, replayed=fingerprint is not None)
        except sqlite3.Error:
            raise StorageError() from None

    def begin(
        self,
        document_id: str,
        revision: int,
        payload: VisualRunRequest,
        *,
        principal: str,
        policy: str,
        fingerprint: str,
        rasterizer: str,
        model_name: str,
        model_profile_sha256: str,
    ) -> tuple[dict, bool]:
        """Atomically fence one operation against the current approved revision."""
        try:
            with self.repository._write_transaction() as db:
                old = self._find(db, payload.operation_id)
                if old is not None:
                    self._authorize(old, document_id, revision, principal, policy, fingerprint)
                    return _result(old, replayed=True), False
                document, spec, assets, svg = self.repository._approved_snapshot(
                    db, document_id, revision, required_policy_digest=policy
                )
                if not hmac.compare_digest(
                    hashlib.sha256(svg.encode("utf-8")).hexdigest(), payload.svg_sha256
                ):
                    raise VisualSourceMismatch()
                approval = self.repository._approval_record(db, document_id, revision)
                if approval is None:
                    raise VisualSourceMismatch()
                db.execute(
                    """
                    INSERT INTO design_vlm_runs (
                        operation_id, document_id, revision, approval_id,
                        principal_id, policy_digest, request_fingerprint,
                        spec_digest, asset_manifest_digest, renderer_version,
                        svg_sha256, png_sha256, rasterizer,
                        model_name, model_profile_sha256, status, started_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'EXECUTING', ?)
                    """,
                    (
                        payload.operation_id,
                        document.document_id,
                        revision,
                        approval["approval_id"],
                        principal,
                        policy,
                        fingerprint,
                        spec,
                        assets,
                        RENDERER_VERSION,
                        payload.svg_sha256,
                        payload.png_sha256,
                        rasterizer,
                        model_name,
                        model_profile_sha256,
                        _now(),
                    ),
                )
                return _result(self._find(db, payload.operation_id), replayed=False), True
        except sqlite3.Error:
            raise StorageError() from None

    def finish(
        self,
        operation_id: str,
        *,
        status: str,
        findings: list[dict] | None = None,
        runtime: dict | None = None,
        reason: str | None = None,
    ) -> dict:
        if status not in {"EXECUTED", "UNKNOWN"}:
            raise ValueError("invalid local shadow run terminal status")
        try:
            with self.repository._write_transaction() as db:
                row = self._find(db, operation_id)
                if row is None:
                    raise NotFoundError()
                if row["status"] != "EXECUTING":
                    return _result(row, replayed=True)
                try:
                    _, spec, assets, svg = self.repository._approved_snapshot(
                        db,
                        row["document_id"],
                        row["revision"],
                        required_policy_digest=row["policy_digest"],
                    )
                    if (
                        RENDERER_VERSION != row["renderer_version"]
                        or spec != row["spec_digest"]
                        or assets != row["asset_manifest_digest"]
                        or not hmac.compare_digest(
                            hashlib.sha256(svg.encode("utf-8")).hexdigest(), row["svg_sha256"]
                        )
                    ):
                        raise VisualSourceMismatch()
                    # Re-render *inside* the approval transaction so postflight
                    # cannot accept a changed PNG or renderer with an unchanged SVG.
                    # Renderer absence after inference is ambiguous, never PASS.
                    raster = rasterize_svg(svg)
                    if (
                        not hmac.compare_digest(raster.svg_sha256, row["svg_sha256"])
                        or not hmac.compare_digest(raster.png_sha256, row["png_sha256"])
                        or raster.rasterizer != row["rasterizer"]
                    ):
                        raise VisualSourceMismatch()
                except (DesignError, RasterInvalid, RasterUnavailable):
                    status, reason, findings, runtime = "UNKNOWN", "SOURCE_CHANGED", None, None
                db.execute(
                    """
                    UPDATE design_vlm_runs
                    SET status = ?, findings_json = ?, runtime_json = ?,
                        reason = ?, finished_at = ?
                    WHERE operation_id = ? AND status = 'EXECUTING'
                    """,
                    (
                        status,
                        _json(findings) if status == "EXECUTED" else None,
                        _json(runtime) if runtime is not None else None,
                        reason,
                        _now(),
                        operation_id,
                    ),
                )
                return _result(self._find(db, operation_id), replayed=False)
        except sqlite3.Error:
            raise StorageError() from None


def submit_visual_run(
    repository: DesignRepository,
    document_id: str,
    revision: int,
    payload: VisualRunRequest,
    *,
    principal: str,
    policy: str,
    model_config_raw: str | None,
) -> tuple[dict, bool]:
    """Explicit synchronous local inference; never repeat a journaled attempt."""
    profile = load_vlm_profile(model_config_raw)
    journal = VisualRunJournal(repository)
    fingerprint = _fingerprint(
        document_id, revision, payload, principal, policy, profile.fingerprint
    )
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

    if not _VLM_LOCK.acquire(blocking=False):
        raise VLMExecutionBusy()
    try:
        # A second check after acquiring the lock prevents a concurrent caller
        # from running the same admitted operation twice.
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

        # Entire preflight is before durable admission and before inference.
        # No model invocation when approval, raster or tag digest mismatches.
        target = repository.ocr_target(document_id, revision, required_policy_digest=policy)
        svg = repository.approved_svg(document_id, revision, required_policy_digest=policy)
        raster = rasterize_svg(svg)
        if not all(
            hmac.compare_digest(a, b)
            for a, b in (
                (target["svg_sha256"], raster.svg_sha256),
                (payload.svg_sha256, raster.svg_sha256),
                (payload.png_sha256, raster.png_sha256),
            )
        ):
            raise VisualSourceMismatch()
        verify_ollama_model(profile)

        admitted, new = journal.begin(
            document_id,
            revision,
            payload,
            principal=principal,
            policy=policy,
            fingerprint=fingerprint,
            rasterizer=raster.rasterizer,
            model_name=profile.model,
            model_profile_sha256=profile.fingerprint,
        )
        if not new:
            return admitted, False

        try:
            findings = inspect_approved_png(raster.png, profile)
            verify_ollama_model(profile)
        except Exception:
            # A provider may have performed side effects even if the response
            # disappeared. Never downgrade uncertainty to a retryable failure.
            result = journal.finish(
                payload.operation_id, status="UNKNOWN", reason="VLM_EXECUTION_AMBIGUOUS"
            )
        else:
            result = journal.finish(
                payload.operation_id,
                status="EXECUTED",
                findings=findings,
                runtime={
                    "source": "local_ollama",
                    "local_model_digest_claim": profile.digest,
                    "execution_evidence": "OPERATOR_LOCAL_NOT_ATTESTED",
                    "attested": False,
                },
            )
        return result, True
    finally:
        _VLM_LOCK.release()
