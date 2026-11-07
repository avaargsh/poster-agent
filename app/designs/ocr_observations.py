"""Deterministic checks of untrusted OCR observations against approved design inputs.

Not proof that OCR ran; never emits publish or release authorization.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from .models import validate_plain_text

OCRCheckStatus = Literal["MATCHED", "MISMATCH", "NO_OBSERVATION", "MISSING_IN_DESIGN"]
_SCORE_MIN = 0.40
_CORE_FIELDS = ("title", "price", "cta")
_COMPARISON_SCHEMA_VERSION = 1


class OCRObservation(BaseModel):
    """One externally asserted OCR recognition line, never runtime-attested."""

    model_config = ConfigDict(extra="forbid")

    text: Annotated[str, StringConstraints(min_length=1, max_length=200)]
    score: Annotated[float, Field(strict=True, ge=0, le=1, allow_inf_nan=False)]

    @field_validator("text")
    @classmethod
    def check_text(cls, value: str) -> str:
        return validate_plain_text(value)


class OCRObservationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lines: Annotated[list[OCRObservation], Field(max_length=200)]


def _normalize(value: str) -> str:
    return "".join(value.split()).replace("￥", "¥")


def _price_matches(observed: str, expected: str) -> bool:
    """Reject numeric-prefix collisions: ¥199.90 is not ¥1990 or ¥199.90.5."""
    escaped = re.escape(expected)
    return re.search(r"(?<![0-9])" + escaped + r"(?![0-9.,．])", observed) is not None


def _canonical_sha256(value: object) -> str:
    """Stable content identity only; this is NOT a signature or trusted audit receipt."""
    serialized = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def assess_unattested(target: dict, observations: OCRObservationRequest) -> dict:
    """Compare declared text only; absence, mismatch and missing design block."""
    eligible = [
        (index, _normalize(line.text))
        for index, line in enumerate(observations.lines)
        if line.score >= _SCORE_MIN
    ]
    checks: list[dict] = []
    all_matched = True
    for field in _CORE_FIELDS:
        expected = target["expected_fields"].get(field)
        matched_index: int | None = None
        if field in target["missing_fields"] or not expected:
            status: OCRCheckStatus = "MISSING_IN_DESIGN"
        elif not eligible:
            status = "NO_OBSERVATION"
        else:
            normalized_expected = _normalize(expected)
            if field == "price":
                matched_index = next(
                    (
                        index
                        for index, text in eligible
                        if _price_matches(text, normalized_expected)
                    ),
                    None,
                )
            else:
                matched_index = next(
                    (index for index, text in eligible if text == normalized_expected),
                    None,
                )
            status = "MATCHED" if matched_index is not None else "MISMATCH"
        all_matched = all_matched and status == "MATCHED"
        checks.append({"field": field, "status": status, "matched_line_index": matched_index})
    consistency = "MATCHES_EXPECTED" if all_matched else "BLOCKED"
    declared_lines = observations.model_dump(mode="json")
    comparison_input = {
        "schema_version": _COMPARISON_SCHEMA_VERSION,
        "target": target,
        "observations": declared_lines,
        "field_checks": checks,
        "declared_consistency": consistency,
    }
    return {
        **target,
        "ocr_status": "OBSERVATIONS_UNATTESTED",
        "field_checks": checks,
        "declared_consistency": consistency,
        "comparison_schema_version": _COMPARISON_SCHEMA_VERSION,
        "observations_sha256": _canonical_sha256(declared_lines),
        "comparison_fingerprint_sha256": _canonical_sha256(comparison_input),
        "runtime_attested": False,
        "quality_verified": False,
        "release_ready": False,
    }


def replay_consistent_unattested(
    authoritative_target: dict,
    observations: OCRObservationRequest,
    report: dict,
) -> bool:
    """Check exact *unattested* replay content, never OCR execution or release authority.

    The caller must separately obtain and validate the authoritative revision target
    and retain the exact caller observations. Self-consistent input and report are
    forgeable and cannot stand in for an authenticated OCR execution receipt.
    """
    if not isinstance(observations, OCRObservationRequest) or not isinstance(report, dict):
        return False

    try:
        expected = assess_unattested(authoritative_target, observations)
        # Do not compare dictionaries directly: bool and int compare equal in Python.
        # Canonical JSON also rejects NaN and distinguishes missing or extra claims.
        replay_json = json.dumps(
            expected, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
        supplied_json = json.dumps(
            report, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
    except (KeyError, TypeError, ValueError):
        return False

    return replay_json == supplied_json
