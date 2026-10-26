"""Export user-entered M6 review assertions as an immutable, untrusted sidecar.

This endpoint does not save decisions or change the frozen OCR-bound CSV.
A successfully validated file is an operator assertion, NOT independent human
attestation, an OCR runtime attestation, or quality / publishing approval.
"""
from __future__ import annotations

import hashlib
import re
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .m6_overlay_routes import _package

router = APIRouter(prefix="/api/batches", tags=["m6-review-sidecar"])

CHECK_NAMES = frozenset((
    "sku_integrity", "text_legibility", "title", "subtitle", "price",
    "original_price", "price_position", "original_price_position", "cta",
    "composition",
))
SHA = re.compile(r"[a-f0-9]{64}\Z")
MAX_REQUEST_BYTES = 256 * 1024


class ReviewDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    sku_id: str = Field(min_length=2, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    variant_id: Literal["square", "feed_4x5_native"]
    reviewer_alias: str = Field(min_length=2, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    checks: dict[str, Literal["PASS", "FAIL", "N/A"]] = Field(
        min_length=10, max_length=10,
    )
    overall_accept: Literal["PASS", "FAIL"]
    overall_reason: str = Field(max_length=500)


class ReviewSidecarRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    reviews_csv_sha256: str = Field(min_length=64, max_length=64)
    ocr_capture_sha256: str = Field(min_length=64, max_length=64)
    decisions: list[ReviewDecision] = Field(min_length=1, max_length=200)


def _unavailable(message: str) -> HTTPException:
    return HTTPException(status_code=409, detail="M6 审核侧车导出受阻：" + message)


def _invalid(message: str) -> HTTPException:
    return HTTPException(status_code=422, detail="M6 人工审核输入无效：" + message)


@router.post("/{batch_id}/m6-review-sidecar")
async def export_m6_review_sidecar(batch_id: str, request: Request):
    """Check source identity and output a bounded draft; never persist human data."""
    from scripts.capture_m6_paddle_ocr import (
        MAX_PNG_BYTES, OCREvidenceError, _identities, _read_regular, _review_rows,
    )
    from scripts.verify_m6_ocr_capture import (
        OCRCaptureEvidenceError, verify_ocr_capture,
    )

    # Match the local-only completed-batch gate before evaluating any review input.
    package = _package(batch_id)

    # Bound request bytes before decoding JSON, without trusting Content-Length.
    incoming = bytearray()
    async for chunk in request.stream():
        if len(incoming) + len(chunk) > MAX_REQUEST_BYTES:
            raise HTTPException(status_code=413, detail="M6 人工审核请求超过上限")
        incoming.extend(chunk)
    try:
        submitted = ReviewSidecarRequest.model_validate_json(bytes(incoming))
    except ValidationError as exc:
        raise _invalid("JSON 结构或字段不符合 M6 审核协议") from exc

    if not SHA.fullmatch(submitted.reviews_csv_sha256) or not SHA.fullmatch(
        submitted.ocr_capture_sha256
    ):
        raise _invalid("冻结证据 SHA-256 格式不合法")

    keys = set()
    for decision in submitted.decisions:
        key = (decision.sku_id, decision.variant_id)
        if key in keys:
            raise _invalid("同一 SKU×版式不能重复提交")
        keys.add(key)
        if set(decision.checks) != CHECK_NAMES:
            raise _invalid("必须逐项填写官方 M6 的 10 个检查字段")
        if decision.overall_accept == "PASS" and any(
            value == "FAIL" for value in decision.checks.values()
        ):
            raise _invalid("存在不通过项目时不能将总体标记 PASS")
        if decision.overall_accept == "FAIL" and not decision.overall_reason.strip():
            raise _invalid("标记 FAIL 时必须填写原因")

    try:
        reviews_sha, source_rows = _review_rows(package)
        cohort, rows, sku_count = _identities(source_rows)
        if reviews_sha != submitted.reviews_csv_sha256:
            raise OCREvidenceError("冻结人工审核 CSV 已变更")
        verified = verify_ocr_capture(
            package / "m6_paddle_ocr_evidence.json",
            rows, cohort_id=cohort, reviews_sha256=reviews_sha,
        )
        if verified["evidence_sha256"] != submitted.ocr_capture_sha256:
            raise OCREvidenceError("PaddleOCR 证据版本已变更")
        rows_by_key = {(row["sku_id"], row["variant_id"]): row for row in rows}
        output = []
        for decision in submitted.decisions:
            row = rows_by_key.get((decision.sku_id, decision.variant_id))
            if row is None:
                raise _invalid("提交了当前冻结 cohort 之外的 SKU 或版式")
            image = _read_regular(package / row["output_image_path"], MAX_PNG_BYTES)
            if hashlib.sha256(image).hexdigest() != row["output_image_sha256"]:
                raise OCREvidenceError("对应海报原图已变化")
            output.append({
                "sku_id": decision.sku_id,
                "variant_id": decision.variant_id,
                "poster_id": row["poster_id"],
                "image_sha256": row["output_image_sha256"],
                "reviewer_alias": decision.reviewer_alias,
                "checks": dict(decision.checks),
                "manual_overall_label": decision.overall_accept,
                "overall_reason": decision.overall_reason.strip(),
                # The server cannot prove the actual time of human inspection.
                "reviewed_at_utc": None,
            })
    except HTTPException:
        raise
    except (OSError, ValueError, OCREvidenceError, OCRCaptureEvidenceError) as exc:
        raise _unavailable(f"冻结图像或 OCR 证据不一致：{exc}") from exc

    return {
        "schema_version": "m6-human-review-sidecar-1",
        "status": "OPERATOR_ASSERTED_NOT_ATTESTED",
        "cohort_id": cohort,
        "cohort_sku_count": sku_count,
        "reviews_csv_sha256": reviews_sha,
        "ocr_capture_sha256": verified["evidence_sha256"],
        "decision_count": len(output),
        "decisions": sorted(output, key=lambda row: (row["sku_id"], row["variant_id"])),
        "human_review_verified": False,
        "automatically_accepted": False,
        "quality_verified": False,
        "runtime_attested": False,
        "note": (
            "仅导出操作员填写的人工标签；不修改冻结评审 CSV，不证明独立人工审核、"
            "OCR 推理执行或 M6 质量 PASS。侧车须由独立验收流程核对。"
        ),
    }
