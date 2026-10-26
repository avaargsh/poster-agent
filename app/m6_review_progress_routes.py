"""M6 local CSV-only review-progress preview, not an evidence or OCR gate.

The operator chooses two local CSVs. We neither retain their content nor read
referenced paths: filenames, authorization claims and image hashes are untrusted.
"""
from __future__ import annotations

import csv
import io
import re
from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, File, HTTPException, UploadFile

router = APIRouter(prefix="/api/m6", tags=["m6-review-progress"])
MAX_CSV_BYTES = 2_000_000
SKU_PATTERN = re.compile(r"^[A-Za-z0-9_-]{2,64}$")
SHA_PATTERN = re.compile(r"^[a-f0-9]{64}$")
VARIANTS = ("square", "feed_4x5_native")
LABELS = frozenset({"PASS", "FAIL", "UNVERIFIED", "N/A"})
FIELDS = (
    "sku_integrity", "text_legibility", "title", "subtitle", "price",
    "original_price", "price_position", "original_price_position", "cta",
    "composition",
)


def _invalid(message: str) -> HTTPException:
    return HTTPException(status_code=422, detail=message)


async def _load_csv(
    upload: UploadFile, fields: tuple[str, ...], *, name: str, max_rows: int
) -> list[dict[str, str]]:
    """Bound bytes, columns and rows before computing private local metadata."""
    data = await upload.read(MAX_CSV_BYTES + 1)
    if not data or len(data) > MAX_CSV_BYTES:
        raise HTTPException(status_code=413, detail=f"{name} 为空或超过 2 MB")
    try:
        text = data.decode("utf-8-sig")
        if "\x00" in text:
            raise ValueError("NUL")
        reader = csv.DictReader(io.StringIO(text, newline=""), strict=True)
        header = reader.fieldnames
        if not header or len(header) != len(set(header)) or set(header) != set(fields):
            raise _invalid(f"{name} 表头不匹配 M6 官方 CSV 模板")
        rows = []
        for row in reader:
            if None in row or any(value is None for value in row.values()):
                raise _invalid(f"{name} 存在不完整或多余的单元格")
            rows.append({key: value.strip() for key, value in row.items()})
            if len(rows) > max_rows:
                raise _invalid(f"{name} 记录超过 {max_rows} 行")
    except (UnicodeError, ValueError, csv.Error) as exc:
        raise _invalid(f"{name} 不是合法的 UTF-8 CSV") from exc
    if not rows:
        raise _invalid(f"{name} 没有有效数据行")
    return rows


def _valid_utc(value: str) -> bool:
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return dt.tzinfo is not None and dt.utcoffset() == timedelta(0)


def summarize_progress(registry: list[dict[str, str]], reviews: list[dict[str, str]]) -> dict[str, Any]:
    """Return actionable counts; never infer source integrity or OCR execution."""
    registry_by_sku: dict[str, dict[str, str]] = {}
    cohort_ids = {item["cohort_id"] for item in registry}
    if len(cohort_ids) != 1 or not next(iter(cohort_ids)):
        raise _invalid("M6 registry 必须只包含一个非空 cohort_id")
    cohort_id = next(iter(cohort_ids))
    for row in registry:
        sku = row["sku_id"]
        if not SKU_PATTERN.fullmatch(sku) or sku in registry_by_sku:
            raise _invalid("M6 registry 有不合法或重复的 sku_id")
        registry_by_sku[sku] = row
    if not 1 <= len(registry_by_sku) <= 100:
        raise _invalid("M6 SKU 数必须在 1–100 之间")

    review_by_key: dict[tuple[str, str], dict[str, str]] = {}
    for row in reviews:
        sku, variant = row["sku_id"], row["variant_id"]
        if (
            row["cohort_id"] != cohort_id or sku not in registry_by_sku
            or variant not in VARIANTS or (sku, variant) in review_by_key
        ):
            raise _invalid("M6 reviews 与 registry 的 cohort、SKU 或版式不匹配/重复")
        review_by_key[(sku, variant)] = row

    issues: list[dict[str, str]] = []
    governance_asserted = 0
    complete = 0
    missing_outputs = 0
    overall_counts = {"PASS": 0, "FAIL": 0, "UNVERIFIED": 0}
    for sku in sorted(registry_by_sku):
        item = registry_by_sku[sku]
        governance_ok = (
            item["source_asset_authorization_status"] == "AUTHORIZED"
            and bool(item["authorization_evidence_ref"])
            and item["logo_authorization_status"] in {"AUTHORIZED", "NOT_APPLICABLE"}
            and item["distinct_sku_verified"] == "YES"
            and item["duplicate_source_image_reviewed"] == "YES"
            and bool(item["reviewer_alias"])
        )
        if governance_ok:
            governance_asserted += 1
        else:
            issues.append({
                "sku_id": sku, "variant_id": "", "code": "GOVERNANCE_PENDING",
            })
        for variant in VARIANTS:
            row = review_by_key.get((sku, variant))
            if row is None:
                missing_outputs += 1
                issues.append({"sku_id": sku, "variant_id": variant, "code": "MISSING_ROW"})
                continue
            if (
                not row["output_image_path"]
                or not SHA_PATTERN.fullmatch(row["output_image_sha256"])
            ):
                missing_outputs += 1
                issues.append({"sku_id": sku, "variant_id": variant, "code": "OUTPUT_MISSING"})
                continue
            values = [row[field] for field in FIELDS]
            overall = row["overall_accept"]
            valid_fields = all(value in LABELS for value in values)
            # A CSV preview cannot verify whether N/A is semantically valid:
            # that is checked against the frozen spec by the final M6 gate.
            reviewed = (
                valid_fields
                and all(value != "UNVERIFIED" for value in values)
                and overall in {"PASS", "FAIL"}
                and bool(row["reviewer_alias"])
                and _valid_utc(row["reviewed_at_utc"])
                and (overall != "FAIL" or bool(row["overall_reason"]))
                and (overall != "PASS" or all(value in {"PASS", "N/A"} for value in values))
            )
            if reviewed:
                complete += 1
                overall_counts[overall] += 1
            else:
                overall_counts["UNVERIFIED"] += 1
                issues.append({"sku_id": sku, "variant_id": variant, "code": "REVIEW_PENDING"})

    expected = len(registry_by_sku) * len(VARIANTS)
    return {
        "scope": "csv_preview_only",
        "cohort_id": cohort_id,
        "sku_total": len(registry_by_sku),
        "expected_outputs": expected,
        "reported_outputs": len(review_by_key),
        "review_complete": complete,
        "review_pending": expected - complete,
        "missing_outputs": missing_outputs,
        "governance_asserted": governance_asserted,
        "governance_pending": len(registry_by_sku) - governance_asserted,
        "manual_overall_counts": overall_counts,
        "ocr_verified": False,
        "image_hashes_verified": False,
        "approved_for_publication": False,
        "issues": issues,
        "note": (
            "仅根据上传的本地 CSV 显示人工填写进度；不保存文件、不读取路径，"
            "未执行 OCR、授权核实、图片哈希/尺寸校验，也不代表 M6 最终 PASS"
        ),
    }


@router.post("/review-progress")
async def preview_m6_progress(
    registry: UploadFile = File(...), reviews: UploadFile = File(...),
) -> dict[str, Any]:
    # Lazy import avoids importing the batch runner (which imports app.main)
    # during app.main's router initialization. These are the existing schemas.
    from scripts.validate_m6_cohort import REGISTRY_FIELDS, REVIEW_FIELDS

    registry_rows = await _load_csv(registry, REGISTRY_FIELDS, name="M6 registry", max_rows=100)
    review_rows = await _load_csv(reviews, REVIEW_FIELDS, name="M6 reviews", max_rows=200)
    return summarize_progress(registry_rows, review_rows)
