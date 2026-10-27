"""Offline read-only integrity verifier for M6 cutout quality evidence.

Recomputes PNG metrics from persisted, receipt-checked source assets. Operator
labels remain self-asserted, not independently verified human or model runtime.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path

from app import storage
from app.batch_artifact_integrity import verify_batch_artifact
from app.cutout_quality import (
    REVIEW_FIELDS, SCHEMA, evidence_from_pngs,
)
from evals.native_batch_scope import load_batch_scope


class CutoutEvidenceError(ValueError):
    """Tampered, incomplete or undersized cutout cohort evidence."""


def _bounded(path: Path, limit: int = 2_000_000) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise CutoutEvidenceError("M6 抠图证据缺失或存在符号链接")
    if path.stat().st_size <= 0 or path.stat().st_size > limit:
        raise CutoutEvidenceError("M6 抠图证据文件过大或为空")
    return path.read_bytes()


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _csv_rows(path: Path) -> tuple[list[dict], bytes]:
    try:
        raw = _bounded(path)
        content = raw.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(content, newline=""), strict=True)
        header = reader.fieldnames
        if (not header or len(header) != len(set(header))
                or set(header) != set(REVIEW_FIELDS)):
            raise CutoutEvidenceError("M6 抠图审核表字段不符合冻结规格")
        rows = list(reader)
        if not rows or any(
            None in row or any(value is None for value in row.values())
            for row in rows
        ):
            raise CutoutEvidenceError("M6 抠图审核表存在缺行或非法 CSV 字段")
        return rows, raw
    except (UnicodeError, csv.Error) as exc:
        raise CutoutEvidenceError("M6 抠图审核表 CSV 格式错误") from exc


def _model_provenance_unverified(row: dict) -> bool:
    return (
        row["model_runtime_attested"] == "NO"
        and row["model_sha256"] == ""
    )


def _utc(value: str) -> bool:
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return dt.tzinfo is not None and dt.utcoffset() == timezone.utc.utcoffset(dt)


def verify_cutout_evidence(
    package: Path, journal: Path, *, min_skus: int = 20,
) -> dict:
    """Validate all original/cutout/paired pixels, row identities and human claims."""
    if min_skus < 1:
        raise CutoutEvidenceError("最少 SKU 数应为正整数")
    package, journal = Path(package), Path(journal)
    try:
        journal_raw = _bounded(journal)
        ledger = json.loads(journal_raw)
        scope = load_batch_scope(journal)
        cohort = "m6-" + _sha(journal_raw)[:12]
        rows, reviews_raw = _csv_rows(package / "m6_cutout_reviews.csv")
        metadata_raw = _bounded(package / "m6_cutout_evidence.json")
        data = json.loads(metadata_raw)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        if isinstance(exc, CutoutEvidenceError):
            raise
        raise CutoutEvidenceError("无法读取完成批次及 M6 抠图证据") from exc
    expected = ledger["rows"]
    if not isinstance(data, dict) or data.get("schema_version") != SCHEMA:
        raise CutoutEvidenceError("M6 抠图证据 Schema 不匹配")
    if (data.get("cohort_id") != cohort
            or data.get("source_batch_journal_sha256") != _sha(journal_raw)
            or data.get("model_inference_performed") is not False
            or data.get("model_runtime_attested") is not False
            or data.get("human_acceptance_performed") is not False
            or data.get("comparison_panels")
                != "LEFT_ORIGINAL_RIGHT_TRANSPARENT_CHECKERBOARD"):
        raise CutoutEvidenceError("M6 抠图证据来源或模型断言无效")
    if len(expected) < min_skus:
        raise CutoutEvidenceError(f"真实质量验收至少需要 {min_skus} 个不同 SKU")
    if (len(rows) != len(expected)
            or len(scope["sku_by_id"]) != len(expected)
            or {row["sku_id"] for row in rows} != set(expected)):
        raise CutoutEvidenceError("M6 抠图审核表 SKU 集合存在缺失或重复")
    item_rows = data.get("items")
    if not isinstance(item_rows, list):
        raise CutoutEvidenceError("M6 抠图证据 items 无效")
    items = {item.get("sku_id"): item for item in item_rows if isinstance(item, dict)}
    if len(items) != len(item_rows):
        raise CutoutEvidenceError("M6 抠图证据存在重复或非法 SKU")
    candidates = {sku for sku, record in expected.items()
                  if record.get("product_cutout_sha256")}
    if (data.get("candidate_count") != len(candidates)
            or data.get("evidence_count") != len(candidates)
            or data.get("invalid_evidence_count") != 0
            or set(items) != candidates):
        raise CutoutEvidenceError("M6 抠图候选计数或证据集合不完整")
    accepted = 0
    rejected = 0
    for row in rows:
        sku = row["sku_id"]
        record = expected[sku]
        poster_id = record["poster_id"]
        if (row["cohort_id"] != cohort or row["poster_id"] != poster_id
                or row["source_version"] != str(record["version"])):
            raise CutoutEvidenceError(f"{sku}: 抠图审核身份和版本不一致")
        try:
            verify_batch_artifact(poster_id, record)
        except (OSError, ValueError) as exc:
            raise CutoutEvidenceError(f"{sku}: 原始产物执行凭据失败") from exc
        if sku not in candidates:
            if (row["quality_state"] != "NOT_APPLICABLE"
                    or row["human_accept"] != "N/A"
                    or row["comparison_image_path"] != ""
                    or row["comparison_image_sha256"] != ""):
                raise CutoutEvidenceError(f"{sku}: 原图模式存在伪造抠图结论")
            continue
        item = items[sku]
        assets = storage.poster_dir(poster_id) / "assets"
        try:
            source = _bounded(assets / "product.png", 40 * 1024 * 1024)
            cut = _bounded(assets / "product_cutout.png", 40 * 1024 * 1024)
            calculated, preview = evidence_from_pngs(source, cut)
        except (OSError, ValueError) as exc:
            raise CutoutEvidenceError(f"{sku}: 已保存的原图/抠图无法复核") from exc
        filename = f"images/{sku}-cutout-comparison.png"
        compare_raw = _bounded(package / filename, 20 * 1024 * 1024)
        if (compare_raw != preview
                or item.get("metrics") != calculated
                or item.get("poster_id") != poster_id
                or item.get("source_version") != record["version"]
                or item.get("comparison_image_path") != filename):
            raise CutoutEvidenceError(f"{sku}: 对照图片或像素测量证据被篡改")
        fields = {
            "source_product_sha256": calculated["source_png_sha256"],
            "cutout_sha256": calculated["cutout_png_sha256"],
            "comparison_image_path": filename,
            "comparison_image_sha256": calculated["comparison_png_sha256"],
            "foreground_fraction": str(calculated["foreground_fraction"]),
            "soft_alpha_fraction": str(calculated["soft_alpha_fraction"]),
            "edge_touch_fraction": str(calculated["edge_touch_fraction"]),
            "component_count": str(calculated["component_count"]),
            "retained_rgb_mae": str(calculated["retained_rgb_mae"]),
            "machine_risks": ";".join(calculated["machine_risks"]),
            "quality_state": "REVIEW_REQUIRED",
            "candidate_origin": "USER_SUPPLIED_OR_PREVIEW_NOT_ATTESTED",
        }
        if any(row[k] != v for k, v in fields.items()) or not _model_provenance_unverified(row):
            raise CutoutEvidenceError(f"{sku}: 已冻结的机器证据列发生变化")
        labels = [
            row[k] for k in (
                "subject_integrity_review", "edge_artifact_review", "brand_mark_review"
            )
        ]
        if any(label not in ("PASS", "FAIL", "UNVERIFIED") for label in labels):
            raise CutoutEvidenceError(f"{sku}: 人工复核字段存在非法标签")
        state = row["human_accept"]
        if state not in ("PASS", "FAIL", "UNVERIFIED"):
            raise CutoutEvidenceError(f"{sku}: 人工总评状态无效")
        if state in ("PASS", "FAIL"):
            if not row["reviewer_alias"] or not _utc(row["reviewed_at_utc"]):
                raise CutoutEvidenceError(f"{sku}: 人工复核缺少审核人或 UTC 时间")
            if state == "PASS":
                if any(label != "PASS" for label in labels):
                    raise CutoutEvidenceError(f"{sku}: 人工总评 PASS 必须各维度均为 PASS")
                accepted += 1
            else:
                if not any(label == "FAIL" for label in labels):
                    raise CutoutEvidenceError(f"{sku}: 人工 FAIL 必须注明受损维度")
                rejected += 1
        elif (row["reviewer_alias"] or row["reviewed_at_utc"]
              or any(label != "UNVERIFIED" for label in labels)):
            raise CutoutEvidenceError(f"{sku}: 人工复核处于半填写状态")
    status = (
        "NOT_APPLICABLE" if not candidates else
        "HUMAN_REVIEW_REQUIRED" if accepted + rejected != len(candidates) else
        "OPERATOR_ASSERTED_NOT_ATTESTED" if rejected == 0 else
        "OPERATOR_REJECTED"
    )
    return {
        "schema_version": SCHEMA, "cohort_id": cohort,
        "batch_sku_count": len(expected),
        "candidate_count": len(candidates),
        "operator_accepted_count": accepted,
        "operator_rejected_count": rejected,
        "model_runtime_attested": False,
        "independent_human_review_attested": False,
        "status": status,
        "reviews_csv_sha256": _sha(reviews_raw),
        "evidence_json_sha256": _sha(metadata_raw),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify local M6 cutout evidence")
    parser.add_argument("--package", required=True, type=Path)
    parser.add_argument("--journal", required=True, type=Path)
    args = parser.parse_args()
    try:
        result = verify_cutout_evidence(args.package, args.journal)
    except (OSError, ValueError, KeyError) as exc:
        parser.exit(2, f"M6 抠图证据校验失败：{exc}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
