"""Read-only comparison of a journaled local OCR run against the current design.

This rechecks local provenance and detects staleness, but cannot independently
attest OCR execution or authorize publishing.
"""

from __future__ import annotations

import hmac

from pydantic import ValidationError

from .errors import DesignError, NotFoundError
from .ocr_model_policy import load_model_profile
from .ocr_observations import OCRObservationRequest, assess_unattested
from .ocr_runs import OCRRunJournal, OCRSourceMismatch
from .rasterization import rasterize_svg
from .repository import DesignRepository


class OCRRunNotExecuted(DesignError):
    code = "OCR_RUN_NOT_EXECUTED"

    @property
    def default_message(self) -> str:
        return "OCR run has no completed observations to compare"


def compare_journaled_ocr_run(
    repository: DesignRepository,
    document_id: str,
    revision: int,
    operation_id: str,
    *,
    principal: str,
    policy: str,
    model_config_raw: str | None,
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
        observations = OCRObservationRequest.model_validate(
            {
                "lines": [
                    {"text": item["text"], "score": item["score"]} for item in row["observations"]
                ]
            }
        )
    except (ValidationError, KeyError, TypeError, ValueError):
        raise OCRSourceMismatch() from None

    compared = assess_unattested(target, observations)
    return {
        **compared,
        "operation_id": operation_id,
        "journal_status": "EXECUTED",
        "source_state": "CURRENT_EXECUTED_UNATTESTED",
        "png_sha256": raster.png_sha256,
        "model_profile_sha256": model_profile.fingerprint,
        "runtime_attested": False,
        "quality_verified": False,
        "release_ready": False,
    }
