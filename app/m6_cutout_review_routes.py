"""Loopback-only human cutout review: CAS CSV changes, immutable source evidence.

The M6 package's machine evidence and PNGs remain frozen. Each explicit
review operation preserves the pre-write CSV by its SHA-256 in private
history, validates the current whole cohort, and updates just human fields.
Operator claims are intentionally NOT scientific or model attestation.
"""

from __future__ import annotations

import csv
import hashlib
import io
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from .m6_batch_package_routes import _verified_source

router = APIRouter(prefix="/api/batches", tags=["m6-cutout-human-review"])
_HISTORY_DIR = "_m6_cutout_review_history"
_LOCK = threading.RLock()
_SHA = re.compile(r"^[0-9a-f]{64}$")
_SKU = re.compile(r"^[A-Za-z0-9_-]{2,64}$")
_HUMAN_KEYS = (
    "subject_integrity_review",
    "edge_artifact_review",
    "brand_mark_review",
    "human_accept",
    "reviewer_alias",
    "reviewer_notes",
    "reviewed_at_utc",
)


class CutoutReviewUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expected_csv_sha256: str = Field(min_length=64, max_length=64)
    sku_id: str = Field(min_length=2, max_length=64)
    subject_integrity_review: Literal["PASS", "FAIL"]
    edge_artifact_review: Literal["PASS", "FAIL"]
    brand_mark_review: Literal["PASS", "FAIL"]
    human_accept: Literal["PASS", "FAIL"]
    reviewer_alias: str = Field(min_length=2, max_length=80)
    reviewer_notes: str = Field(default="", max_length=500)


def _conflict(detail: str) -> HTTPException:
    return HTTPException(status_code=409, detail="M6 抠图人工审核受阻：" + detail)


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _source(batch_id: str):
    from scripts.verify_m6_cutout_evidence import verify_cutout_evidence

    journal, folder = _verified_source(batch_id)
    if folder.is_symlink() or not folder.is_dir():
        raise _conflict("审核包尚未生成")
    try:
        report = verify_cutout_evidence(folder, journal, min_skus=1)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise _conflict(f"原始素材或冻结证据未通过验证：{exc}") from exc
    return journal, folder, report


def _read_csv(folder: Path, expected_sha: str):
    from scripts.verify_m6_cutout_evidence import _csv_rows

    path = folder / "m6_cutout_reviews.csv"
    rows, raw = _csv_rows(path)
    if _sha(raw) != expected_sha:
        raise _conflict("审核表已由另一页面修改，请重新加载当前 SHA-256")
    return rows, raw


def _state(batch_id: str) -> dict:
    _, folder, report = _source(batch_id)
    rows, _ = _read_csv(folder, report["reviews_csv_sha256"])
    exposed = []
    for row in rows:
        exposed.append(
            {
                "sku_id": row["sku_id"],
                "poster_id": row["poster_id"],
                "source_version": row["source_version"],
                "quality_state": row["quality_state"],
                "model_runtime_attested": row["model_runtime_attested"],
                "human_accept": row["human_accept"],
                "subject_integrity_review": row["subject_integrity_review"],
                "edge_artifact_review": row["edge_artifact_review"],
                "brand_mark_review": row["brand_mark_review"],
                "reviewer_alias": row["reviewer_alias"],
                "reviewer_notes": row["reviewer_notes"],
                "reviewed_at_utc": row["reviewed_at_utc"],
                "machine_risks": row["machine_risks"],
                "foreground_fraction": row["foreground_fraction"],
                "soft_alpha_fraction": row["soft_alpha_fraction"],
                "edge_touch_fraction": row["edge_touch_fraction"],
                "comparison_image_path": row["comparison_image_path"],
                "comparison_image_sha256": row["comparison_image_sha256"],
                "comparison_url": (
                    f"/api/batches/{batch_id}/m6-cutout-review/{row['sku_id']}/comparison"
                    if row["quality_state"] == "REVIEW_REQUIRED"
                    else None
                ),
            }
        )
    return {
        "batch_id": batch_id,
        "cohort_id": report["cohort_id"],
        "csv_sha256": report["reviews_csv_sha256"],
        "evidence_sha256": report["evidence_json_sha256"],
        "status": report["status"],
        "candidate_count": report["candidate_count"],
        "operator_accepted_count": report["operator_accepted_count"],
        "operator_rejected_count": report["operator_rejected_count"],
        "model_runtime_attested": False,
        "independent_human_review_attested": False,
        "rows": exposed,
    }


@router.get("/{batch_id}/m6-cutout-review")
def get_cutout_reviews(batch_id: str):
    return _state(batch_id)


@router.get("/{batch_id}/m6-cutout-review/{sku_id}/comparison")
def get_cutout_comparison(batch_id: str, sku_id: str):
    if not _SKU.fullmatch(sku_id):
        raise HTTPException(status_code=404, detail="SKU 标识无效")
    _, folder, report = _source(batch_id)
    rows, _ = _read_csv(folder, report["reviews_csv_sha256"])
    record = next((x for x in rows if x["sku_id"] == sku_id), None)
    if record is None or record["quality_state"] != "REVIEW_REQUIRED":
        raise HTTPException(status_code=404, detail="此 SKU 没有可审查的抠图候选")
    image = folder / "images" / f"{sku_id}-cutout-comparison.png"
    if image.is_symlink() or not image.is_file():
        raise _conflict("原图与抠图对照 PNG 不存在")
    if image.stat().st_size > 20 * 1024 * 1024:
        raise _conflict("对照 PNG 超过审核包容量限制")
    raw = image.read_bytes()
    if _sha(raw) != record["comparison_image_sha256"]:
        raise _conflict("原图与抠图对照 PNG 已发生变化")
    return Response(
        raw,
        media_type="image/png",
        headers={
            "Cache-Control": "no-store",
            "X-Evidence-Sha256": record["comparison_image_sha256"],
        },
    )


def _preserve_previous_csv(folder: Path, expected_sha: str, raw: bytes):
    history = folder / _HISTORY_DIR
    if history.is_symlink():
        raise _conflict("旧版审核表历史目录是符号链接")
    history.mkdir(exist_ok=True)
    if not history.is_dir():
        raise _conflict("旧版审核表历史目录无效")
    previous = history / f"{expected_sha}.csv"
    if previous.is_symlink():
        raise _conflict("旧版审核表备份存在符号链接")
    if previous.exists():
        if previous.read_bytes() != raw:
            raise _conflict("已保存的旧版审核表 SHA 与内容冲突")
    else:
        with previous.open("xb") as out:
            out.write(raw)
            out.flush()
            os.fsync(out.fileno())


def _save_atomic(path: Path, raw: bytes):
    if path.is_symlink():
        raise _conflict("原审核 CSV 是符号链接")
    scratch = path.with_name("." + path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with scratch.open("xb") as out:
            out.write(raw)
            out.flush()
            os.fsync(out.fileno())
        os.replace(scratch, path)
    finally:
        scratch.unlink(missing_ok=True)


@router.post("/{batch_id}/m6-cutout-review")
def save_cutout_review(batch_id: str, update: CutoutReviewUpdate):
    if not _SHA.fullmatch(update.expected_csv_sha256):
        raise HTTPException(status_code=422, detail="expected_csv_sha256 非法")
    if not _SKU.fullmatch(update.sku_id):
        raise HTTPException(status_code=422, detail="SKU 标识非法")
    labels = [
        update.subject_integrity_review,
        update.edge_artifact_review,
        update.brand_mark_review,
    ]
    if (
        update.human_accept == "PASS"
        and any(x != "PASS" for x in labels)
        or update.human_accept == "FAIL"
        and not any(x == "FAIL" for x in labels)
    ):
        raise HTTPException(status_code=422, detail="总评结果与逐项审核不一致")
    alias = update.reviewer_alias.strip()
    notes = update.reviewer_notes.strip()
    if (
        len(alias) < 2
        or alias != update.reviewer_alias
        or (update.human_accept == "FAIL" and not notes)
    ):
        raise HTTPException(status_code=422, detail="请填写有效审核人，失败时需明确原因")
    with _LOCK:
        _, folder, report = _source(batch_id)
        if update.expected_csv_sha256 != report["reviews_csv_sha256"]:
            raise _conflict("审核表已更新，请重新加载后再提交")
        rows, prior = _read_csv(folder, update.expected_csv_sha256)
        item = next((row for row in rows if row["sku_id"] == update.sku_id), None)
        if item is None or item["quality_state"] != "REVIEW_REQUIRED":
            raise _conflict("SKU 没有有效待审抠图或已不在当前批次")
        item.update(
            {
                "subject_integrity_review": update.subject_integrity_review,
                "edge_artifact_review": update.edge_artifact_review,
                "brand_mark_review": update.brand_mark_review,
                "human_accept": update.human_accept,
                "reviewer_alias": alias,
                "reviewer_notes": notes,
                "reviewed_at_utc": datetime.now(timezone.utc).isoformat(),
            }
        )
        columns = list(rows[0].keys())
        text = io.StringIO(newline="")
        writer = csv.DictWriter(text, fieldnames=columns, extrasaction="raise", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
        next_bytes = text.getvalue().encode("utf-8")
        _preserve_previous_csv(folder, update.expected_csv_sha256, prior)
        _save_atomic(folder / "m6_cutout_reviews.csv", next_bytes)
        return _state(batch_id)
