"""Offline consistency checks for a local, unattested OCR evidence bundle.

No database, model execution, network, or publication authority is involved.
The expected digest must be obtained separately from the input file.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import re
import stat
import sys
from io import BytesIO
from pathlib import Path

from PIL import Image

from app.designs.errors import DesignError
from app.designs.models import MAX_ASSET_PIXELS, PAGE_MAX_SIZE
from app.designs.ocr_layout_hints import _intersection_fraction
from app.designs.ocr_observations import OCRObservationRequest, assess_unattested
from app.designs.ocr_runs import _decode_observations, _decode_runtime
from app.designs.ocr_spatial_evidence import assess_spatial_evidence

_MAX_FILE_BYTES = 36 * 1024 * 1024
_MAX_PNG_BYTES = 24 * 1024 * 1024
_FLAGS = ("runtime_attested", "quality_verified", "release_ready")
_IDENTITY = (
    "document_id",
    "revision",
    "approval_id",
    "spec_digest",
    "asset_manifest_digest",
    "renderer_version",
    "svg_sha256",
)
_DIGESTS = (
    "spec_digest",
    "asset_manifest_digest",
    "svg_sha256",
    "png_sha256",
    "model_profile_sha256",
)
_JOURNAL_KEYS = {
    *_IDENTITY,
    *_DIGESTS,
    *_FLAGS,
    "operation_id",
    "principal_id",
    "rasterizer",
    "status",
    "reason",
    "observations",
    "runtime",
    "started_at",
    "finished_at",
    "replayed",
}
_BUNDLE_V1_KEYS = {
    *_FLAGS,
    "schema_version",
    "evidence_kind",
    "policy_digest",
    "canvas",
    "png_base64",
    "journal",
    "comparison",
    "bundle_sha256",
}
_BUNDLE_V2_KEYS = _BUNDLE_V1_KEYS | {"approval"}
_APPROVAL_KEYS = {
    "document_id",
    "revision",
    "approval_id",
    "spec_digest",
    "asset_manifest_digest",
    "renderer_version",
    "principal_id",
    "policy_digest",
    "created_at",
    "attributed_at",
    "is_current_head",
    "matches_current_snapshot",
}


class EvidenceError(ValueError):
    """Invalid or inconsistent local evidence."""


def _canonical(value: object) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise EvidenceError(message)


def _sha(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _unique(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        _require(key not in result, "duplicate JSON key")
        result[key] = value
    return result


def _nonfinite(value: str) -> None:
    raise EvidenceError("non-finite JSON number")


def _check_png(encoded: object, canvas: dict, journal: dict) -> None:
    width, height = canvas["width"], canvas["height"]
    _require(
        type(width) is int
        and type(height) is int
        and 64 <= width <= PAGE_MAX_SIZE
        and 64 <= height <= PAGE_MAX_SIZE
        and width * height <= MAX_ASSET_PIXELS,
        "invalid canvas dimensions",
    )
    _require(
        isinstance(encoded, str) and len(encoded) <= 4 * ((_MAX_PNG_BYTES + 2) // 3),
        "invalid PNG encoding size",
    )
    raw = base64.b64decode(encoded, validate=True)
    _require(
        0 < len(raw) <= _MAX_PNG_BYTES
        and base64.b64encode(raw).decode("ascii") == encoded
        and hashlib.sha256(raw).hexdigest() == journal["png_sha256"],
        "PNG bytes do not match journal",
    )
    with Image.open(BytesIO(raw), formats=["PNG"]) as image:
        _require(image.format == "PNG" and image.size == (width, height), "PNG canvas mismatch")
        image.load()


def _check_layout_claims(layout: dict, spatial: dict, canvas: dict) -> None:
    """Recompute consistency of supplied rectangles, not their design provenance."""
    findings = layout["design_node_overlap_checks"]
    spatial_checks = spatial["spatial_field_checks"]
    width, height = canvas["width"], canvas["height"]
    for finding, observed in zip(findings, spatial_checks, strict=True):
        _require(
            isinstance(finding, dict)
            and set(finding)
            == {"field", "status", "node_id", "node_rect", "ocr_bbox", "overlap_fraction"}
            and finding["field"] == observed["field"]
            and _canonical(finding["ocr_bbox"]) == _canonical(observed["bbox"]),
            "layout finding does not match spatial evidence",
        )
        node_id, rect = finding["node_id"], finding["node_rect"]
        status, fraction = finding["status"], finding["overlap_fraction"]
        if node_id is None:
            _require(
                rect is None and status == "NO_VISIBLE_TEXT_NODE" and fraction is None,
                "invalid missing node claim",
            )
            continue
        _require(isinstance(node_id, str) and 0 < len(node_id) <= 128, "invalid node identity")
        if rect is None:
            _require(
                status == "UNSUPPORTED_NODE_GEOMETRY" and fraction is None,
                "invalid unsupported geometry claim",
            )
            continue
        _require(
            isinstance(rect, list)
            and len(rect) == 4
            and all(type(value) in (int, float) for value in rect)
            and 0 <= rect[0] < rect[2] <= width
            and 0 <= rect[1] < rect[3] <= height,
            "invalid node rectangle",
        )
        box = observed["bbox"]
        if box is None:
            _require(status == "NO_VALID_OCR_BOX" and fraction is None, "invalid missing box claim")
            continue
        expected_fraction = _intersection_fraction(box, rect)
        expected_status = "OVERLAP_UNCALIBRATED" if expected_fraction > 0 else "OUTSIDE_DESIGN_NODE"
        _require(
            status == expected_status and _canonical(fraction) == _canonical(expected_fraction),
            "layout overlap mismatch",
        )
    expected_state = (
        "OVERLAP_ONLY_UNCALIBRATED"
        if all(item["status"] == "OVERLAP_UNCALIBRATED" for item in findings)
        else "BLOCKED"
    )
    _require(layout["design_node_overlap_status"] == expected_state, "layout status mismatch")


def _verify(bundle: dict, expected_sha256: str) -> dict:
    _require(isinstance(bundle, dict), "invalid bundle fields")
    schema_version = bundle.get("schema_version")
    _require(type(schema_version) is int, "unsupported bundle schema")
    if schema_version == 1:
        expected_keys = _BUNDLE_V1_KEYS
    elif schema_version == 2:
        expected_keys = _BUNDLE_V2_KEYS
    else:
        raise EvidenceError("unsupported bundle schema")
    _require(set(bundle) == expected_keys, "invalid bundle fields")
    _require(bundle["evidence_kind"] == "LOCAL_OCR_UNATTESTED", "unsupported bundle schema")
    claimed = bundle["bundle_sha256"]
    _require(_sha(claimed) and _sha(expected_sha256), "invalid digest")
    computed = _digest({key: value for key, value in bundle.items() if key != "bundle_sha256"})
    _require(
        hmac.compare_digest(claimed, computed) and hmac.compare_digest(expected_sha256, computed),
        "bundle digest mismatch",
    )
    journal, report, canvas = bundle["journal"], bundle["comparison"], bundle["canvas"]
    _require(isinstance(journal, dict) and set(journal) == _JOURNAL_KEYS, "invalid journal fields")
    _require(isinstance(report, dict), "invalid comparison report")
    _require(isinstance(canvas, dict) and set(canvas) == {"width", "height"}, "invalid canvas")
    for source in (bundle, journal, report):
        _require(all(source.get(flag) is False for flag in _FLAGS), "unexpected trust claim")

    _require(
        journal["status"] == "EXECUTED"
        and journal["reason"] is None
        and journal["replayed"] is False
        and type(journal["revision"]) is int
        and journal["revision"] >= 1,
        "invalid journal state",
    )
    _require(
        all(_sha(journal[key]) for key in _DIGESTS) and _sha(bundle["policy_digest"]),
        "invalid source digest",
    )
    for key in (
        "document_id",
        "approval_id",
        "renderer_version",
        "rasterizer",
        "started_at",
        "finished_at",
    ):
        value = journal[key]
        _require(isinstance(value, str) and 0 < len(value) <= 256, "invalid source identity")
    _require(
        isinstance(journal["operation_id"], str)
        and re.fullmatch(r"ocr_[0-9a-f]{32}", journal["operation_id"]) is not None
        and isinstance(journal["principal_id"], str)
        and re.fullmatch(r"[a-z][a-z0-9_-]{2,63}", journal["principal_id"]) is not None,
        "invalid operation or principal",
    )
    approval_attribution_consistent = False
    if schema_version == 2:
        approval = bundle["approval"]
        _require(
            isinstance(approval, dict) and set(approval) == _APPROVAL_KEYS,
            "invalid approval attribution fields",
        )
        _require(
            approval["document_id"] == journal["document_id"]
            and type(approval["revision"]) is int
            and approval["revision"] == journal["revision"]
            and approval["approval_id"] == journal["approval_id"]
            and approval["spec_digest"] == journal["spec_digest"]
            and approval["asset_manifest_digest"] == journal["asset_manifest_digest"]
            and approval["renderer_version"] == journal["renderer_version"],
            "approval attribution does not match OCR source",
        )
        _require(
            isinstance(approval["principal_id"], str)
            and re.fullmatch(r"[a-z][a-z0-9_-]{2,63}", approval["principal_id"]) is not None
            and approval["principal_id"] != journal["principal_id"]
            and _sha(approval["policy_digest"])
            and hmac.compare_digest(approval["policy_digest"], bundle["policy_digest"]),
            "invalid or non-independent approval principal or policy",
        )
        _require(
            all(
                isinstance(approval[key], str) and 0 < len(approval[key]) <= 256
                for key in ("created_at", "attributed_at")
            )
            and approval["is_current_head"] is True
            and approval["matches_current_snapshot"] is True,
            "approval is not bound to the current snapshot",
        )
        approval_attribution_consistent = True
    _check_png(bundle["png_base64"], canvas, journal)

    # Reuse the repository's bounded OCR observation/runtime validation.
    lines = _decode_observations(_canonical(journal["observations"]))
    runtime = _decode_runtime(
        _canonical(journal["runtime"]),
        model_profile_sha256=journal["model_profile_sha256"],
        observation_count=len(lines),
    )
    _require(_canonical(lines) == _canonical(journal["observations"]), "invalid observations")
    _require(_canonical(runtime) == _canonical(journal["runtime"]), "invalid runtime")
    width, height = canvas["width"], canvas["height"]
    for line in lines:
        box = line["bbox"]
        _require(
            box is None or (0 <= box[0] < box[2] <= width and 0 <= box[1] < box[3] <= height),
            "observation outside raster",
        )

    # The expected text is carried by the bundle, not independently authenticated.
    expected = report["expected_fields"]
    _require(
        isinstance(expected, dict)
        and set(expected) == {"title", "price", "cta"}
        and all(isinstance(value, str) and 0 < len(value) <= 200 for value in expected.values())
        and report["missing_fields"] == [],
        "incomplete expected fields",
    )
    target = {
        **{key: journal[key] for key in _IDENTITY},
        "expected_fields": expected,
        "missing_fields": [],
        "ocr_status": "NOT_RUN",
        **dict.fromkeys(_FLAGS, False),
    }
    observations = OCRObservationRequest.model_validate(
        {"lines": [{"text": line["text"], "score": line["score"]} for line in lines]}
    )
    compared = assess_unattested(target, observations)
    source_binding = {
        **{key: journal[key] for key in _IDENTITY},
        "png_sha256": journal["png_sha256"],
        "rasterizer": journal["rasterizer"],
        "model_profile_sha256": journal["model_profile_sha256"],
        "operation_id": journal["operation_id"],
        "principal_id": journal["principal_id"],
        "policy_digest": bundle["policy_digest"],
    }
    spatial = assess_spatial_evidence(
        lines, compared["field_checks"], width=width, height=height, source_binding=source_binding
    )
    # The design document itself is not exported. Verify the layout claim's
    # content fingerprint and internal rectangle math, not design provenance.
    layout = {
        key: report[key]
        for key in (
            "design_node_overlap_status",
            "design_node_overlap_checks",
            "design_node_overlap_schema_version",
            "design_node_overlap_fingerprint_sha256",
        )
    }
    _require(
        type(layout["design_node_overlap_schema_version"]) is int
        and layout["design_node_overlap_schema_version"] == 1
        and layout["design_node_overlap_status"] in ("BLOCKED", "OVERLAP_ONLY_UNCALIBRATED")
        and isinstance(layout["design_node_overlap_checks"], list)
        and len(layout["design_node_overlap_checks"]) == 3,
        "invalid layout claim",
    )
    _check_layout_claims(layout, spatial, canvas)
    _require(
        layout["design_node_overlap_fingerprint_sha256"]
        == _digest(
            {
                "schema_version": 1,
                "spatial_source_sha256": spatial["spatial_source_sha256"],
                "canvas": canvas,
                "design_node_overlap_status": layout["design_node_overlap_status"],
                "design_node_overlap_checks": layout["design_node_overlap_checks"],
            }
        ),
        "layout claim digest mismatch",
    )
    replay = {
        **compared,
        **spatial,
        **layout,
        "operation_id": journal["operation_id"],
        "journal_status": "EXECUTED",
        "source_state": "CURRENT_EXECUTED_UNATTESTED",
        "png_sha256": journal["png_sha256"],
        "model_profile_sha256": journal["model_profile_sha256"],
        **dict.fromkeys(_FLAGS, False),
    }
    _require(_canonical(report) == _canonical(replay), "comparison replay mismatch")
    return {
        "schema_version": 1,
        "status": "CONSISTENT_LOCAL_EVIDENCE_ONLY",
        "bundle_sha256": computed,
        "png_sha256": journal["png_sha256"],
        "operation_id": journal["operation_id"],
        "layout_geometry_independently_verified": False,
        "current_design_verified": False,
        "expected_digest_authenticity_verified": False,
        "approval_attribution_consistent": approval_attribution_consistent,
        **dict.fromkeys(_FLAGS, False),
    }


def _read_bounded_bundle(path: Path) -> bytes:
    """Read one bounded snapshot from a regular file without following links.

    O_NONBLOCK prevents a substituted FIFO from hanging the offline verifier;
    fstat then rejects FIFOs, devices, sockets and directories before reading.
    O_NOFOLLOW closes the final-component symlink race between the path check
    and opening the file.
    """
    try:
        if path.is_symlink():
            raise EvidenceError("bundle must not be a symlink")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        fd = os.open(path, flags)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            _require(stat.S_ISREG(info.st_mode), "bundle must be a regular file")
            _require(info.st_size <= _MAX_FILE_BYTES, "bundle exceeds size limit")
            raw = stream.read(_MAX_FILE_BYTES + 1)
    except EvidenceError:
        raise
    except OSError:
        raise EvidenceError("bundle is missing or unreadable") from None
    _require(len(raw) <= _MAX_FILE_BYTES, "bundle exceeds size limit")
    return raw


def verify_bundle(path: Path, expected_sha256: str) -> dict:
    """Check a bounded regular file using a separately obtained content digest."""
    try:
        raw = _read_bounded_bundle(path)
        bundle = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_unique, parse_constant=_nonfinite
        )
        return _verify(bundle, expected_sha256)
    except EvidenceError:
        raise
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        RecursionError,
        DesignError,
        Image.DecompressionBombError,
    ):
        raise EvidenceError("invalid or inconsistent evidence") from None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument(
        "--expected-sha256", required=True, help="separately retained bundle digest"
    )
    args = parser.parse_args(argv)
    try:
        result = verify_bundle(args.bundle, args.expected_sha256)
    except EvidenceError as exc:
        print(f"OCR evidence refused: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
