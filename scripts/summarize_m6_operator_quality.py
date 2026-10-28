"""Produce an anonymized, evidence-bound summary of operator-reported M6 quality.

Consumes a successfully validated M6 report and the exact final human CSV.
This command never opens poster images, invokes OCR or authenticates reviewers.
All rates are fractions of operator-entered labels; NEVER publication approval.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

from scripts.validate_m6_cohort import (
    FIELD_LABELS,
    REVIEW_FIELDS,
    VARIANTS,
    M6ValidationError,
    _publish_report,
    _read_csv,
    _read_json,
    _safe_report_destination,
)
from scripts.validate_m6_cohort import (
    SCHEMA as M6_SCHEMA,
)

SCHEMA = "m6-operator-quality-summary-1"
MINIMUM_SKUS = 20
LABELS = ("PASS", "FAIL", "UNVERIFIED")
FIELD_STATES = (*LABELS, "N/A")


class OperatorQualityError(ValueError):
    """Inputs are not sufficient to summarize a validated human review cohort."""


def _counts(values, allowed: tuple[str, ...]) -> dict[str, int]:
    counts = Counter(values)
    unexpected = set(counts) - set(allowed)
    if unexpected:
        raise OperatorQualityError("人工审核标签存在非法状态")
    return {label: counts[label] for label in allowed}


def _ratio(count: int, denominator: int) -> float | None:
    return round(count / denominator, 6) if denominator else None


def summarize_operator_quality(
    validation_report: dict[str, Any],
    reviews: Path,
) -> dict[str, Any]:
    """Aggregate only validated operator labels; omit all SKU and reason text."""
    report = validation_report
    if not isinstance(report, dict) or report.get("schema_version") != M6_SCHEMA:
        raise OperatorQualityError("M6 元数据报告格式不合法")
    if report.get("status") != "PASS":
        raise OperatorQualityError("M6 原始证据未通过一致性校验，禁止评分")
    if report.get("ocr_capture_consistency") != "MATCHED_DECLARED_REAL":
        raise OperatorQualityError("OCR 本地声明与冻结图片/CSV 未完成一致性核对")
    if report.get("ocr_source_binding") not in ("SAME_REVIEW_SNAPSHOT", "MATCHED_IMMUTABLE_FIELDS"):
        raise OperatorQualityError("人工审核表与 OCR 冻结来源未建立允许的绑定")
    if report.get("ocr_runtime_attested") is not False or (
        report.get("real_ocr_quality_verified") is not False
    ):
        raise OperatorQualityError("不接受宣称独立证明 OCR 或质量通过的元数据报告")
    hashes = report.get("input_sha256")
    if not isinstance(hashes, dict) or not isinstance(hashes.get("ocr_capture"), str):
        raise OperatorQualityError("缺少 OCR 采集摘要")
    _review_path, rows, csv_sha = _read_csv(Path(reviews), REVIEW_FIELDS, "最终 M6 人工评审")
    if hashes.get("output_reviews") != csv_sha:
        raise OperatorQualityError("最终人工评审 CSV 的 SHA-256 与 M6 校验报告不一致")
    if report.get("ocr_source_binding") == "MATCHED_IMMUTABLE_FIELDS" and (
        not isinstance(hashes.get("ocr_source_reviews"), str)
    ):
        raise OperatorQualityError("双快照报告缺少原始 OCR 冻结表摘要")

    keys: dict[str, set[str]] = {}
    variant_states: dict[str, list[str]] = {variant: [] for variant in VARIANTS}
    for row in rows:
        sku, variant, status = row["sku_id"], row["variant_id"], row["overall_accept"]
        if variant not in VARIANTS or status not in LABELS:
            raise OperatorQualityError("SKU 版式或人工总体状态不合法")
        variants = keys.setdefault(sku, set())
        if variant in variants:
            raise OperatorQualityError("最终人工评审重复 SKU×版式")
        variants.add(variant)
        variant_states[variant].append(status)
    sku_count = len(keys)
    if (
        not sku_count
        or len(rows) != 2 * sku_count
        or any(variants != set(VARIANTS) for variants in keys.values())
    ):
        raise OperatorQualityError("SKU 缺少方图/原生 4:5 配对，不得缩小分母")
    if (
        report.get("expected_sku_count") != sku_count
        or report.get("expected_output_count") != len(rows)
        or report.get("review_row_count") != len(rows)
        or report.get("present_output_count") != len(rows)
        or report.get("missing_output_count") != 0
    ):
        raise OperatorQualityError("人工评审与已验证 cohort 分母或图片数不一致")
    for variant in VARIANTS:
        original_counts = report.get("manual_overall_counts", {}).get(variant)
        observed = _counts(variant_states[variant], LABELS)
        if original_counts != observed:
            raise OperatorQualityError("人工总体 PASS/FAIL 数与 M6 校验报告不一致")

    overall = _counts((row["overall_accept"] for row in rows), LABELS)
    by_variant = {}
    for variant in VARIANTS:
        counts = _counts(variant_states[variant], LABELS)
        by_variant[variant] = {
            **counts,
            "denominator": sku_count,
            "reported_pass_fraction": _ratio(counts["PASS"], sku_count),
            "decision_coverage_fraction": _ratio(counts["PASS"] + counts["FAIL"], sku_count),
        }
    by_field = {}
    for field in FIELD_LABELS:
        count = _counts((row[field] for row in rows), FIELD_STATES)
        applicable = len(rows) - count["N/A"]
        by_field[field] = {
            **count,
            "applicable_denominator": applicable,
            "reported_pass_fraction": _ratio(count["PASS"], applicable),
            "decision_coverage_fraction": _ratio(count["PASS"] + count["FAIL"], applicable),
        }
    status_by_key = {(row["sku_id"], row["variant_id"]): row["overall_accept"] for row in rows}
    paired_pass = sum(
        all(status_by_key[(sku, variant)] == "PASS" for variant in VARIANTS) for sku in keys
    )
    min_met = sku_count >= MINIMUM_SKUS
    return {
        "schema_version": SCHEMA,
        "status": "OPERATOR_LABELS_ONLY_NOT_ATTESTED",
        "scope": "anonymous_aggregated_operator_claims_from_m6_validated_snapshot",
        "source_binding": report["ocr_source_binding"],
        "input_sha256": {
            "final_reviews": csv_sha,
            "ocr_capture": hashes["ocr_capture"],
            **(
                {"ocr_source_reviews": hashes["ocr_source_reviews"]}
                if "ocr_source_reviews" in hashes
                else {}
            ),
        },
        "sku_count": sku_count,
        "variant_count": len(rows),
        "minimum_skus_required": MINIMUM_SKUS,
        "minimum_sku_count_met": min_met,
        "overall": {
            **overall,
            "denominator": len(rows),
            "reported_pass_fraction": _ratio(overall["PASS"], len(rows)),
            "decision_coverage_fraction": _ratio(overall["PASS"] + overall["FAIL"], len(rows)),
        },
        "by_variant": by_variant,
        "both_variants_passed": {
            "count": paired_pass,
            "denominator": sku_count,
            "fraction": _ratio(paired_pass, sku_count),
        },
        "by_field": by_field,
        "real_distinct_skus_verified": False,
        "source_authorizations_verified": False,
        "operator_authenticity_verified": False,
        "ocr_runtime_attested": False,
        "quality_verified": False,
        "approved_for_publication": False,
        "model_calls": 0,
        "limitations": [
            "All pass/fail labels are operator-reported; not independently audited.",
            "The M6 metadata PASS does not verify real OCR execution or source rights.",
            "A cohort size of 20 does not prove 20 independent authorized real SKUs.",
            "Neither the source validation report nor this summary grants deployment approval.",
            "No SKU names, paths, reviewer identifiers, or failure reasons are exported.",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Summarize M6 manual quality labels without revealing individual SKUs"
    )
    parser.add_argument("--validation-report", type=Path, required=True)
    parser.add_argument("--reviews", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        destination = _safe_report_destination(args.output, (args.validation_report, args.reviews))
        _, validation, report_sha = _read_json(args.validation_report, "M6 完整验证报告")
        summary = summarize_operator_quality(validation, args.reviews)
        summary["input_sha256"]["validation_report"] = report_sha
        payload = (json.dumps(summary, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        destination.parent.mkdir(parents=True, exist_ok=True)
        _publish_report(destination, payload)
    except (OperatorQualityError, M6ValidationError, OSError, ValueError) as exc:
        parser.exit(2, f"M6 匿名汇总失败：{exc}\n")
    print(
        json.dumps(
            {
                "status": summary["status"],
                "sku_count": summary["sku_count"],
                "minimum_sku_count_met": summary["minimum_sku_count_met"],
                "report_sha256": hashlib.sha256(payload).hexdigest(),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
