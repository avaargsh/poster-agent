"""Read-only consistency gate for an existing local M6 PaddleOCR capture.

The JSON is an operator-writable statement, NOT an independently authenticated
OCR runtime or a product-quality score. Real model execution must be verified
separately using isolated provenance / the existing M5j reports.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

SCHEMA = "m6-paddle-ocr-evidence-1"
MAX_BYTES = 8 * 1024 * 1024
_SHA = re.compile(r"^[a-f0-9]{64}$")
_VARIANTS = {"square", "feed_4x5_native"}


class OCRCaptureEvidenceError(ValueError):
    """The supplied M6 OCR evidence is missing, stale, or structurally unsafe."""


def _blocked(message: str) -> OCRCaptureEvidenceError:
    return OCRCaptureEvidenceError("M6 OCR 采集证据：" + message)


def _positive_int(value: object) -> bool:
    return type(value) is int and value > 0


def _valid_sha(value: object) -> bool:
    return isinstance(value, str) and bool(_SHA.fullmatch(value))


def _read_snapshot(path: Path) -> tuple[dict[str, Any], str]:
    if path.is_symlink():
        raise _blocked("拒绝符号链接")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(path, flags)
        with os.fdopen(fd, "rb") as file:
            if not stat.S_ISREG(os.fstat(file.fileno()).st_mode):
                raise _blocked("OCR 证据必须是普通文件")
            raw = file.read(MAX_BYTES + 1)
    except OSError as exc:
        raise _blocked("无法安全读取证据文件") from exc
    if not raw or len(raw) > MAX_BYTES:
        raise _blocked("OCR 证据为空或超过 8 MiB")
    try:
        # Python's JSON parser otherwise accepts NaN / Infinity.
        def reject_constant(value: str) -> None:
            raise ValueError(f"non-finite JSON constant: {value}")

        document = json.loads(raw, parse_constant=reject_constant)
    except (UnicodeError, ValueError, TypeError) as exc:
        raise _blocked("OCR 证据 JSON 非法或置信度包含 NaN/Infinity") from exc
    if not isinstance(document, dict):
        raise _blocked("OCR 证据必须是 JSON 对象")
    return document, hashlib.sha256(raw).hexdigest()


def _model_ok(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    return (
        _valid_sha(value.get("sha256"))
        and _positive_int(value.get("file_count"))
        and _positive_int(value.get("total_bytes"))
    )


def _capture_time_ok(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return stamp.tzinfo is not None and stamp.utcoffset() == timedelta(0)


def verify_ocr_capture(
    path: Path,
    review_rows: list[dict[str, str]],
    *,
    cohort_id: str,
    reviews_sha256: str,
) -> dict[str, Any]:
    """Check exact SKU×variant identity and snapshot hashes, not model truth."""
    document, evidence_sha = _read_snapshot(Path(path))
    if document.get("schema_version") != SCHEMA:
        raise _blocked("schema_version 不匹配")
    if document.get("cohort_id") != cohort_id:
        raise _blocked("cohort_id 与冻结 cohort 不一致")
    if not _valid_sha(reviews_sha256) or document.get("reviews_csv_sha256") != reviews_sha256:
        raise _blocked("reviews_csv_sha256 与冻结 M6 评审表不一致")
    if document.get("real_ocr_inference_attempted") is not True:
        raise _blocked("OCR 未声明真实推理，禁止测试替身或未执行状态通过")
    if not isinstance(document.get("paddleocr_version"), str) or not re.match(
        r"^3\.\d+(?:\.\d+)?(?:$|[.+-])", document["paddleocr_version"]
    ):
        raise _blocked("PaddleOCR 必须声明 3.x 正式版本")
    version = document.get("paddlepaddle_version")
    if not isinstance(version, str) or not re.match(r"^\d+\.\d+(?:\.\d+)?", version):
        raise _blocked("PaddlePaddle 版本无效")
    model_digests = document.get("model_digests")
    if not isinstance(model_digests, dict) or not all(
        _model_ok(model_digests.get(name)) for name in ("detection", "recognition")
    ):
        raise _blocked("检测/识别模型指纹或字节计数不完整")
    if document.get("automatically_accepted") is not False:
        raise _blocked("OCR 证据禁止标记自动通过")
    if document.get("human_review_performed") is not False:
        raise _blocked("OCR 采集不可代替人工审核")
    if document.get("source_authorizations_verified") is not False:
        raise _blocked("OCR 采集不可声明素材授权已验证")
    if document.get("min_20_real_skus_proven") is not False:
        raise _blocked("OCR 采集不可声明真实独立 SKU 已验收")
    elapsed = document.get("elapsed_seconds")
    if (type(elapsed) not in (int, float) or not math.isfinite(elapsed) or elapsed < 0
        or not _capture_time_ok(document.get("captured_at_utc"))):
        raise _blocked("OCR 采集时间或耗时无效")

    expected: dict[tuple[str, str], dict[str, str]] = {}
    for review in review_rows:
        sku, variant = review.get("sku_id"), review.get("variant_id")
        if not sku or variant not in _VARIANTS:
            raise _blocked("评审表存在无效 SKU 或版式")
        key = (sku, variant)
        if key in expected:
            raise _blocked("评审表存在重复 SKU×版式")
        if not _valid_sha(review.get("output_image_sha256")):
            raise _blocked("评审表包含缺失或非法输出 SHA-256")
        expected[key] = review
    skus = {key[0] for key in expected}
    if not skus or len(expected) != 2 * len(skus) or any(
        (sku, variant) not in expected for sku in skus for variant in _VARIANTS
    ):
        raise _blocked("评审表缺少必需的方形/4:5 行")

    if (type(document.get("sku_count")) is not int or
        document["sku_count"] != len(skus) or
        type(document.get("expected_variant_count")) is not int or
        document["expected_variant_count"] != len(expected) or
        type(document.get("captured_variant_count")) is not int or
        document["captured_variant_count"] != len(expected)):
        raise _blocked("OCR SKU/版式数量不一致")
    observations = document.get("observations")
    if not isinstance(observations, list) or len(observations) > 200:
        raise _blocked("OCR observations 不是有界的记录列表")
    seen: set[tuple[str, str]] = set()
    for row in observations:
        if not isinstance(row, dict):
            raise _blocked("OCR 观察记录必须是对象")
        if not isinstance(row.get("sku_id"), str) or not isinstance(row.get("variant_id"), str):
            raise _blocked("OCR 观察记录的 SKU/版式必须是字符串")
        key = (row["sku_id"], row["variant_id"])
        if key in seen:
            raise _blocked("OCR observations 出现重复 SKU×版式")
        seen.add(key)
        source = expected.get(key)
        if source is None:
            raise _blocked("OCR observations 含额外 SKU 或版式")
        if (row.get("poster_id") != source["poster_id"] or
            row.get("image_sha256") != source["output_image_sha256"]):
            raise _blocked("OCR 图片/海报身份与冻结评审记录不一致")
        if row.get("status") != "CAPTURED_REVIEW_REQUIRED":
            raise _blocked("OCR observations 状态不得声称通过")
        lines = row.get("recognized_lines")
        if (not isinstance(lines, list) or len(lines) > 2000 or
            type(row.get("recognized_line_count")) is not int or
            row["recognized_line_count"] != len(lines)):
            raise _blocked("OCR 文本与识别行数量不一致")
        for line in lines:
            if not isinstance(line, dict) or not isinstance(line.get("text"), str):
                raise _blocked("OCR 文本格式无效")
            confidence = line.get("confidence")
            if (type(confidence) not in (int, float) or
                not math.isfinite(confidence) or not 0 <= confidence <= 1):
                raise _blocked("OCR 置信度无效")
    if seen != set(expected):
        raise _blocked("OCR observations 缺少必需版式")

    return {
        "status": "MATCHED_DECLARED_REAL",
        "evidence_sha256": evidence_sha,
        "observation_count": len(observations),
        "runtime_attested": False,
        "quality_verified": False,
        "authorization_verified": False,
        "limitation": (
            "仅检查本地可编辑 OCR JSON 与冻结 CSV/图片身份的一致性；"
            "real_ocr_inference_attempted 和模型指纹为自述，不能证明真实模型执行或商品质量"
        ),
    }
