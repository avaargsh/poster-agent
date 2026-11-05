"""Deterministic checks of untrusted OCR observations against approved design inputs.

Not proof that OCR ran; never emits publish or release authorization.
"""

from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from .models import validate_plain_text

OCRCheckStatus = Literal["MATCHED", "MISMATCH", "NO_OBSERVATION", "MISSING_IN_DESIGN"]
_SCORE_MIN = 0.40
_CORE_FIELDS = ("title", "price", "cta")


class OCRObservation(BaseModel):
    """One externally asserted OCR recognition line, never runtime-attested."""

    model_config = ConfigDict(extra="forbid")

    text: Annotated[str, StringConstraints(min_length=1, max_length=200)]
    score: Annotated[float, Field(strict=True, ge=0, le=1, allow_inf_nan=False)]

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


def assess_unattested(target: dict, observations: OCRObservationRequest) -> dict:
    """Compare declared text only; absence, mismatch and missing design block."""
    texts = [
        _normalize(line.text)
        for line in observations.lines
        if line.score >= _SCORE_MIN
    ]
    checks: list[dict] = []
    all_matched = True
    for field in _CORE_FIELDS:
        expected = target["expected_fields"].get(field)
        if field in target["missing_fields"] or not expected:
            status: OCRCheckStatus = "MISSING_IN_DESIGN"
        elif not texts:
            status = "NO_OBSERVATION"
        else:
            normalized_expected = _normalize(expected)
            if field == "price":
                matched = any(_price_matches(text, normalized_expected) for text in texts)
            else:
                matched = any(text == normalized_expected for text in texts)
            status = "MATCHED" if matched else "MISMATCH"
        all_matched = all_matched and status == "MATCHED"
        checks.append({"field": field, "status": status})
    return {
        **target,
        "ocr_status": "OBSERVATIONS_UNATTESTED",
        "field_checks": checks,
        "declared_consistency": "MATCHES_EXPECTED" if all_matched else "BLOCKED",
        "runtime_attested": False,
        "quality_verified": False,
        "release_ready": False,
    }
