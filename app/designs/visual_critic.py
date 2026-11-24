"""Untrusted visual critique preview for a *currently approved* raster.

This module validates and fingerprints externally supplied soft visual findings.
It does not execute a VLM, attest its input, persist findings, alter designs,
propose edits or grant a repair/export/publish permit.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from .errors import DesignError
from .models import Sha256, validate_plain_text
from .rasterization import RasterArtifact

SCHEMA_VERSION = 1
VisualCategory = Literal[
    "contrast",
    "spacing",
    "visual_hierarchy",
    "product_visibility",
    "text_legibility",
    "brand_style",
]
VisualSeverity = Literal["info", "minor", "major"]
ReviewAction = Literal["NONE", "REVIEW_LAYOUT", "REVIEW_STYLE", "REVIEW_BACKGROUND"]


class VisualSourceMismatch(DesignError):
    """A declared visual input does not match the verified approved raster."""

    code = "VISUAL_SOURCE_MISMATCH"

    @property
    def default_message(self) -> str:
        return "visual critic source does not match the current approved raster"


class NormalizedRegion(BaseModel):
    """A bounded, normalized [0,1] rectangle in approved raster coordinates."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    # Strict numeric inputs prevent JSON booleans and numeric strings from
    # being coerced into plausible-looking normalized geometry.
    x: float = Field(strict=True, ge=0, le=1, allow_inf_nan=False)
    y: float = Field(strict=True, ge=0, le=1, allow_inf_nan=False)
    width: float = Field(strict=True, gt=0, le=1, allow_inf_nan=False)
    height: float = Field(strict=True, gt=0, le=1, allow_inf_nan=False)

    @model_validator(mode="after")
    def entirely_on_canvas(self) -> NormalizedRegion:
        if self.x + self.width > 1 + 1e-9 or self.y + self.height > 1 + 1e-9:
            raise ValueError("visual finding region must stay inside the canvas")
        return self


class QualityFinding(BaseModel):
    """A human-review suggestion, not a verified defect or executable command."""

    model_config = ConfigDict(extra="forbid")

    finding_id: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_-]{0,63}$")]
    category: VisualCategory
    severity: VisualSeverity
    confidence: float = Field(strict=True, ge=0, le=1, allow_inf_nan=False)
    explanation: Annotated[str, StringConstraints(min_length=1, max_length=400)]
    suggested_action: ReviewAction = "NONE"
    region: NormalizedRegion | None = None

    @field_validator("explanation")
    @classmethod
    def bounded_plain_explanation(cls, value: str) -> str:
        return validate_plain_text(value).strip()


class VisualCriticObservationRequest(BaseModel):
    """The caller claims the raster it inspected; its claim is not attestation."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = SCHEMA_VERSION
    svg_sha256: Sha256
    png_sha256: Sha256
    findings: Annotated[list[QualityFinding], Field(max_length=20)]

    @model_validator(mode="after")
    def distinct_finding_ids(self) -> VisualCriticObservationRequest:
        ids = [finding.finding_id for finding in self.findings]
        if len(ids) != len(set(ids)):
            raise ValueError("finding_id values must be unique")
        return self


def _sha256_json(value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def preview_unattested_visual(
    target: dict,
    raster: RasterArtifact,
    observations: VisualCriticObservationRequest,
) -> dict:
    """Produce a reproducible *unverified* review hint bound to approved pixels.

    The caller may fabricate both hashes and findings. The route must obtain
    target and raster from the trusted local repository and recheck current
    approval/policy after rasterization. This function never makes assertions
    about real model execution or the visual truth of a finding.
    """
    if not isinstance(target.get("svg_sha256"), str) or not all(
        hmac.compare_digest(a, b)
        for a, b in (
            (target["svg_sha256"], raster.svg_sha256),
            (observations.svg_sha256, raster.svg_sha256),
            (observations.png_sha256, raster.png_sha256),
        )
    ):
        raise VisualSourceMismatch()

    source_keys = (
        "document_id",
        "revision",
        "approval_id",
        "spec_digest",
        "asset_manifest_digest",
        "renderer_version",
        "svg_sha256",
    )
    source = {key: target[key] for key in source_keys}
    declared = observations.model_dump(mode="json")
    fingerprint_input = {
        "schema_version": SCHEMA_VERSION,
        "approved_source": source,
        "png_sha256": raster.png_sha256,
        "rasterizer": raster.rasterizer,
        "observations": declared,
    }
    return {
        **source,
        "png_sha256": raster.png_sha256,
        "rasterizer": raster.rasterizer,
        "critic_schema_version": SCHEMA_VERSION,
        "critic_status": "DECLARED_FINDINGS_UNATTESTED",
        "findings": declared["findings"],
        "observations_sha256": _sha256_json(declared),
        "critic_fingerprint_sha256": _sha256_json(fingerprint_input),
        "model_execution_attested": False,
        "quality_verified": False,
        "repair_authorized": False,
        "release_ready": False,
    }
