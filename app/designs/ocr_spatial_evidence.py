"""Bounded spatial diagnostics for a local, unattested OCR journal.

This deliberately checks only rectangle presence and raster bounds. A valid
box does not prove that text occupies the intended title/price/CTA zone.
"""

from __future__ import annotations

import hashlib
import json

_SPATIAL_SCHEMA_VERSION = 1


def _canonical_sha256(value: object) -> str:
    serialized = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _bounded_box(box: object, *, width: int, height: int) -> bool:
    if not isinstance(box, list) or len(box) != 4 or any(type(n) is not int for n in box):
        return False
    left, top, right, bottom = box
    return 0 <= left < right <= width and 0 <= top < bottom <= height


def assess_spatial_evidence(
    captured_lines: list[dict], field_checks: list[dict], *, width: int, height: int
) -> dict:
    """Bind journaled geometry to matched text without asserting field placement.

    This is a local, forgeable content fingerprint, never a signed OCR receipt
    or a visual acceptance decision. Unknown/missing boxes are blocked.
    """
    checks: list[dict] = []
    for check in field_checks:
        index = check.get("matched_line_index")
        status = "TEXT_NOT_MATCHED"
        bbox = None
        if check.get("status") == "MATCHED" and type(index) is int:
            if 0 <= index < len(captured_lines):
                candidate = captured_lines[index].get("bbox")
                if candidate is None:
                    status = "MISSING_BBOX"
                elif _bounded_box(candidate, width=width, height=height):
                    status = "BOX_PRESENT_UNCALIBRATED"
                    bbox = candidate
                else:
                    status = "INVALID_BBOX"
            else:
                status = "INVALID_BBOX"
        checks.append(
            {
                "field": check["field"],
                "matched_line_index": index,
                "status": status,
                "bbox": bbox,
            }
        )

    state = (
        "BOUNDS_ONLY_UNCALIBRATED"
        if checks and all(check["status"] == "BOX_PRESENT_UNCALIBRATED" for check in checks)
        else "BLOCKED"
    )
    observations_hash = _canonical_sha256(captured_lines)
    fingerprint = _canonical_sha256(
        {
            "schema_version": _SPATIAL_SCHEMA_VERSION,
            "journaled_observations_sha256": observations_hash,
            "canvas": {"width": width, "height": height},
            "text_field_checks": field_checks,
            "spatial_field_checks": checks,
            "spatial_evidence_status": state,
        }
    )
    return {
        "spatial_evidence_status": state,
        "spatial_field_checks": checks,
        "spatial_comparison_schema_version": _SPATIAL_SCHEMA_VERSION,
        "journaled_observations_sha256": observations_hash,
        "spatial_comparison_fingerprint_sha256": fingerprint,
    }
