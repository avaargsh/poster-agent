"""Offline M6 operator-sidecar assessment against frozen local evidence.

The assessment reports declared human-label coverage and failures, not image
quality, identity of the reviewer, OCR runtime execution or publication approval.
The input sidecar remains separate from the OCR-bound official review CSV.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from fastapi import HTTPException

from app.m6_review_sidecar_routes import MAX_REQUEST_BYTES, validate_saved_sidecar
from scripts.capture_m6_paddle_ocr import (
    OCREvidenceError, _identities, _read_regular, _review_rows,
)
from scripts.validate_m6_cohort import (
    M6ValidationError, _publish_report, _safe_report_destination,
)
from scripts.verify_m6_ocr_capture import _read_snapshot

SCHEMA = "m6-operator-sidecar-assessment-1"
MIN_REAL_SKUS = 20


class SidecarAssessmentError(ValueError):
    """Sidecar or frozen evidence cannot be independently reconciled."""


def assess_sidecar(package_dir: Path, raw_sidecar: bytes) -> dict:
    """Reconcile saved operator decisions without changing any source artifact."""
    package = Path(package_dir)
    if package.is_symlink() or not package.is_dir():
        raise SidecarAssessmentError("审核包目录不存在或是符号链接")
    if (package / "images").is_symlink() or not (package / "images").is_dir():
        raise SidecarAssessmentError("审核包图片目录缺失或被链接替换")
    if not raw_sidecar or len(raw_sidecar) > MAX_REQUEST_BYTES:
        raise SidecarAssessmentError("侧车 JSON 为空或超过 256 KiB")
    try:
        # Exactly the same strict schema, frozen OCR and per-image SHA checks
        # as the web restore endpoint, including fail-closed forged flags.
        canonical = validate_saved_sidecar(package, raw_sidecar)
        reviews_sha, rows = _review_rows(package)
        _, frozen_rows, sku_count = _identities(rows)
        # Detect changes to the review CSV or OCR evidence between checks.
        _, current_ocr_sha = _read_snapshot(package / "m6_paddle_ocr_evidence.json")
        if (reviews_sha != canonical["reviews_csv_sha256"]
                or current_ocr_sha != canonical["ocr_capture_sha256"]):
            raise SidecarAssessmentError("冻结评审表或 OCR 证据已变更")
    except (HTTPException, OCREvidenceError, OSError, ValueError) as exc:
        raise SidecarAssessmentError(f"侧车与冻结源证据不一致：{exc}") from exc

    expected = {(row["sku_id"], row["variant_id"]) for row in frozen_rows}
    claimed = {(item["sku_id"], item["variant_id"]) for item in canonical["decisions"]}
    missing = sorted(expected - claimed)
    failures = sorted(
        (item for item in canonical["decisions"]
         if item["manual_overall_label"] == "FAIL"),
        key=lambda item: (item["sku_id"], item["variant_id"]),
    )
    if missing:
        status = "PARTIAL"
    elif failures:
        status = "MANUAL_FAILURE_REPORTED"
    else:
        status = "LABELS_COMPLETE_UNATTESTED"

    return {
        "schema_version": SCHEMA,
        "status": status,
        "scope": "operator_assertion_coverage_and_local_hash_consistency_only",
        "cohort_id": canonical["cohort_id"],
        "source_sidecar_sha256": hashlib.sha256(raw_sidecar).hexdigest(),
        "reviews_csv_sha256": reviews_sha,
        "ocr_capture_sha256": canonical["ocr_capture_sha256"],
        "expected_sku_count": sku_count,
        "minimum_skus_required": MIN_REAL_SKUS,
        "minimum_sku_count_met": sku_count >= MIN_REAL_SKUS,
        "real_distinct_skus_verified": False,
        "expected_output_count": len(expected),
        "operator_decision_count": len(claimed),
        "missing_decision_count": len(missing),
        "missing_decisions": [
            {"sku_id": sku, "variant_id": variant} for sku, variant in missing
        ],
        "reported_pass_count": len(claimed) - len(failures),
        "reported_fail_count": len(failures),
        "manual_failures": [
            {"sku_id": item["sku_id"], "variant_id": item["variant_id"],
             "reason": item["overall_reason"]} for item in failures
        ],
        "ocr_capture_consistency_checked": True,
        "decision_image_hashes_checked": True,
        "all_m6_bundle_inputs_verified": False,
        "operator_authenticity_verified": False,
        "source_authorizations_verified": False,
        "real_ocr_runtime_attested": False,
        "quality_verified": False,
        "approved_for_publication": False,
        "automatically_accepted": False,
        "next_action": (
            "Complete the missing SKU/variant operator review entries"
            if missing else "Investigate reported manual failures"
            if failures else
            "Run independent M6 bundle, authorization, OCR runtime and quality acceptance"
        ),
        "limitations": [
            "Human labels are unverified operator claims; they are not signed reviewer attestations.",
            "OCR capture metadata and hashes do not prove the real OCR engine ran.",
            "SKU count alone does not establish real distinct SKU ownership or authorization.",
            "Run the independent M6 bundle validation; this does not modify the frozen OCR-bound review CSV.",
            "No result from this assessment grants product-quality PASS or publication approval.",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Audit an exported M6 operator sidecar against its frozen local review package"
    )
    parser.add_argument("--package-dir", required=True, type=Path)
    parser.add_argument("--sidecar", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    package = args.package_dir.expanduser().resolve()
    try:
        dest = _safe_report_destination(args.output, (
            args.sidecar, package / "m6_output_reviews.csv",
            package / "m6_paddle_ocr_evidence.json",
        ))
        # Never risk writing a report over any file in the source package.
        if dest.is_relative_to(package):
            raise SidecarAssessmentError("验收报告必须写到 M6 审核包目录之外")
    except (M6ValidationError, SidecarAssessmentError) as exc:
        parser.error(str(exc))

    try:
        raw = _read_regular(args.sidecar, MAX_REQUEST_BYTES)
        report = assess_sidecar(package, raw)
    except (OSError, OCREvidenceError, SidecarAssessmentError) as exc:
        report = {
            "schema_version": SCHEMA,
            "status": "BLOCKED",
            "scope": "operator_assertion_coverage_and_local_hash_consistency_only",
            "quality_verified": False,
            "approved_for_publication": False,
            "automatically_accepted": False,
            "errors": [str(exc)],
        }

    dest.parent.mkdir(parents=True, exist_ok=True)
    result = (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    try:
        _publish_report(dest, result)
    except (OSError, M6ValidationError) as exc:
        parser.exit(2, f"M6 侧车验收报告保存失败：{exc}\n")
    print(json.dumps({
        "status": report["status"],
        "report_sha256": hashlib.sha256(result).hexdigest(),
        "report_path": str(dest),
    }, ensure_ascii=False))
    return 2 if report["status"] == "BLOCKED" else 0


if __name__ == "__main__":
    raise SystemExit(main())
