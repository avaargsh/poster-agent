"""Read-only comparison of a journaled local OCR run against the current design.

This rechecks local provenance and detects staleness, but cannot independently
attest OCR execution or authorize publishing.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re

from pydantic import ValidationError

from .approvals import snapshot_digests
from .errors import DesignError, NotFoundError
from .ocr_layout_hints import assess_design_node_hints
from .ocr_model_policy import load_model_profile
from .ocr_observations import OCRObservationRequest, assess_unattested
from .ocr_runs import OCRRunJournal, OCRSourceMismatch, _CapturedLine
from .ocr_spatial_evidence import assess_spatial_evidence
from .rasterization import rasterize_svg
from .repository import DesignRepository


class OCRRunNotExecuted(DesignError):
    code = "OCR_RUN_NOT_EXECUTED"

    @property
    def default_message(self) -> str:
        return "OCR run has no completed observations to compare"


def _approval_evidence(
    repository: DesignRepository, target: dict, *, exporter_principal: str
) -> dict:
    """Return the exact current, attributed approval for a portable evidence bundle."""
    history = repository.approval_history(target["document_id"])
    if (
        history.get("document_id") != target["document_id"]
        or history.get("current_revision") != target["revision"]
        or history.get("current_renderer_version") != target["renderer_version"]
    ):
        raise OCRSourceMismatch()

    matches = [
        item
        for item in history.get("approvals", [])
        if isinstance(item, dict) and item.get("approval_id") == target["approval_id"]
    ]
    if len(matches) != 1:
        raise OCRSourceMismatch()
    record = matches[0]
    evidence = {
        "document_id": history["document_id"],
        "revision": record.get("revision"),
        "approval_id": record.get("approval_id"),
        "spec_digest": record.get("spec_digest"),
        "asset_manifest_digest": record.get("asset_manifest_digest"),
        "renderer_version": record.get("renderer_version"),
        "principal_id": record.get("principal_id"),
        "policy_digest": record.get("policy_digest"),
        "created_at": record.get("created_at"),
        "attributed_at": record.get("attributed_at"),
        "is_current_head": record.get("is_current_head"),
        "matches_current_snapshot": record.get("matches_current_snapshot"),
    }
    if (
        type(evidence["revision"]) is not int
        or evidence["revision"] != target["revision"]
        or evidence["approval_id"] != target["approval_id"]
        or evidence["spec_digest"] != target["spec_digest"]
        or evidence["asset_manifest_digest"] != target["asset_manifest_digest"]
        or evidence["renderer_version"] != target["renderer_version"]
        or not isinstance(evidence["principal_id"], str)
        or re.fullmatch(r"[a-z][a-z0-9_-]{2,63}", evidence["principal_id"]) is None
        or evidence["principal_id"] == exporter_principal
        or not isinstance(evidence["policy_digest"], str)
        or re.fullmatch(r"[0-9a-f]{64}", evidence["policy_digest"]) is None
        or any(
            not isinstance(evidence[key], str) or not 0 < len(evidence[key]) <= 256
            for key in ("created_at", "attributed_at")
        )
        or evidence["is_current_head"] is not True
        or evidence["matches_current_snapshot"] is not True
    ):
        raise OCRSourceMismatch()
    return evidence


def compare_journaled_ocr_run(
    repository: DesignRepository,
    document_id: str,
    revision: int,
    operation_id: str,
    *,
    principal: str,
    policy: str,
    model_config_raw: str | None,
    include_evidence_bundle: bool = False,
) -> dict:
    """Replay stored local observations only against a newly verified source.

    Unknown, unavailable, stale, altered and cross-policy records never become
    positive comparisons. No model inference, journal mutation or release.
    """
    row = OCRRunJournal(repository).get(
        document_id,
        revision,
        operation_id,
        principal=principal,
        policy=policy,
    )
    if row is None:
        raise NotFoundError()
    if row["status"] != "EXECUTED":
        raise OCRRunNotExecuted()

    # Rehash pinned local model files. This detects changed local files but
    # does NOT authenticate model weights or prove the model actually ran.
    model_profile = load_model_profile(model_config_raw)
    recorded_model = row.get("model_profile_sha256")
    if not isinstance(recorded_model, str) or not hmac.compare_digest(
        recorded_model, model_profile.fingerprint
    ):
        raise OCRSourceMismatch()

    target = repository.ocr_target(document_id, revision, required_policy_digest=policy)
    approval_evidence = (
        _approval_evidence(repository, target, exporter_principal=principal)
        if include_evidence_bundle
        else None
    )
    for key in (
        "document_id",
        "revision",
        "approval_id",
        "spec_digest",
        "asset_manifest_digest",
        "renderer_version",
        "svg_sha256",
    ):
        if row.get(key) != target.get(key):
            raise OCRSourceMismatch()

    # Confirm raster bytes, not just the SVG or client-claimed PNG digest.
    svg = repository.approved_svg(document_id, revision, required_policy_digest=policy)
    raster = rasterize_svg(svg)
    for observed, expected in (
        (row.get("svg_sha256"), raster.svg_sha256),
        (row.get("png_sha256"), raster.png_sha256),
        (row.get("rasterizer"), raster.rasterizer),
    ):
        if not isinstance(observed, str) or not hmac.compare_digest(observed, expected):
            raise OCRSourceMismatch()
    # Re-read the authoritative head after rendering to reject concurrent
    # edits. This endpoint is still only a read-time consistency check.
    if repository.ocr_target(document_id, revision, required_policy_digest=policy) != target:
        raise OCRSourceMismatch()

    try:
        # Validate the *entire* persisted record before hashing geometry:
        # SQLite content is not independently trusted and may be corrupted.
        captured_lines = [
            _CapturedLine.model_validate(item).model_dump(mode="json")
            for item in row["observations"]
        ]
        observations = OCRObservationRequest.model_validate(
            {"lines": [{"text": item["text"], "score": item["score"]} for item in captured_lines]}
        )
    except (ValidationError, KeyError, TypeError, ValueError):
        raise OCRSourceMismatch() from None

    compared = assess_unattested(target, observations)
    spatial = assess_spatial_evidence(
        captured_lines,
        compared["field_checks"],
        width=raster.width,
        height=raster.height,
        source_binding={
            "document_id": target["document_id"],
            "revision": target["revision"],
            "approval_id": target["approval_id"],
            "spec_digest": target["spec_digest"],
            "asset_manifest_digest": target["asset_manifest_digest"],
            "renderer_version": target["renderer_version"],
            "svg_sha256": raster.svg_sha256,
            "png_sha256": raster.png_sha256,
            "rasterizer": raster.rasterizer,
            "model_profile_sha256": model_profile.fingerprint,
            "operation_id": operation_id,
            "principal_id": principal,
            "policy_digest": policy,
        },
    )
    # The design-node envelopes must belong to the exact approved spec, not
    # a historical snapshot or a concurrent edit. They remain uncalibrated
    # layout hints and cannot authorize publication.
    approved_document = repository.get(document_id, revision)
    spec_digest, asset_digest = snapshot_digests(approved_document)
    if (
        spec_digest != target["spec_digest"]
        or asset_digest != target["asset_manifest_digest"]
        or approved_document.page.width != raster.width
        or approved_document.page.height != raster.height
    ):
        raise OCRSourceMismatch()
    layout_hints = assess_design_node_hints(
        approved_document,
        spatial["spatial_field_checks"],
        width=raster.width,
        height=raster.height,
        spatial_source_sha256=spatial["spatial_source_sha256"],
    )
    # The expensive raster/layout work must not turn an earlier model or
    # journal snapshot into a claim about the current source. Recheck both
    # before the last authoritative design read; never rerun the OCR engine.
    if load_model_profile(model_config_raw) != model_profile:
        raise OCRSourceMismatch()
    current_row = OCRRunJournal(repository).get(
        document_id,
        revision,
        operation_id,
        principal=principal,
        policy=policy,
    )
    if current_row != row:
        raise OCRSourceMismatch()
    if repository.ocr_target(document_id, revision, required_policy_digest=policy) != target:
        raise OCRSourceMismatch()
    if include_evidence_bundle and (
        _approval_evidence(repository, target, exporter_principal=principal) != approval_evidence
    ):
        raise OCRSourceMismatch()

    report = {
        **compared,
        **spatial,
        **layout_hints,
        "operation_id": operation_id,
        "journal_status": "EXECUTED",
        "source_state": "CURRENT_EXECUTED_UNATTESTED",
        "png_sha256": raster.png_sha256,
        "model_profile_sha256": model_profile.fingerprint,
        "runtime_attested": False,
        "quality_verified": False,
        "release_ready": False,
    }

    if not include_evidence_bundle:
        return report

    # Reuse the exact raster and journal snapshot used for the comparison.
    # Do not fetch either again or include credentials, model paths or SVG
    # source bytes. The rasterizer already enforces PNG size/pixel bounds.
    bundle = {
        "schema_version": 2,
        "evidence_kind": "LOCAL_OCR_UNATTESTED",
        "approval": approval_evidence,
        "policy_digest": policy,
        "canvas": {"width": raster.width, "height": raster.height},
        "png_base64": base64.b64encode(raster.png).decode("ascii"),
        "journal": row,
        "comparison": report,
        "runtime_attested": False,
        "quality_verified": False,
        "release_ready": False,
    }
    canonical = json.dumps(
        bundle, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    return {**bundle, "bundle_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest()}
