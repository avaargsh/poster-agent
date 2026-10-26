"""Local-only, explicitly prepared M6 OCR visual-review export.

Never runs OCR. Never accepts a caller-supplied filesystem path, edits review
labels, approves a product, or returns unrelated customer files.
"""
from __future__ import annotations

import hashlib
import json
import tempfile
import zipfile
from pathlib import Path
from typing import Iterator

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from .m6_batch_package_routes import _verified_source
from .m6_package_download_routes import (
    CHUNK_BYTES, MAX_CSV_BYTES, _open_regular,
)

router = APIRouter(prefix="/api/batches", tags=["m6-overlay"])
_ARCHIVE_NAME = "m6_ocr_review_overlays.zip"
_MAX_ZIP_BYTES = 520 * 1024 * 1024
_MAX_OVERLAY_BYTES = 35 * 1024 * 1024


def _blocked(message: str) -> HTTPException:
    return HTTPException(status_code=409, detail="M6 OCR 标注包：" + message)


def _package(batch_id: str) -> Path:
    """Require the verified completed batch and the server-selected directory."""
    _, package = _verified_source(batch_id)
    if package.is_symlink() or not package.is_dir():
        raise _blocked("尚未准备有效的 M6 审核包")
    if (package / "images").is_symlink() or not (package / "images").is_dir():
        raise _blocked("审核包图片目录无效")
    return package


@router.get("/{batch_id}/m6-overlay")
def inspect_overlay(batch_id: str):
    """Presence only; not a claim that either artifact has been verified."""
    package = _package(batch_id)
    ocr = package / "m6_paddle_ocr_evidence.json"
    archive = package / _ARCHIVE_NAME
    if ocr.is_symlink() or archive.is_symlink():
        raise _blocked("本机 OCR 证据或标注包不能是符号链接")
    return {
        "ocr_capture_present": ocr.is_file(),
        "overlay_prepared": archive.is_file(),
        "automatically_accepted": False,
        "quality_verified": False,
        "runtime_attested": False,
    }


@router.post("/{batch_id}/m6-overlay", status_code=201)
def create_overlay(batch_id: str):
    """Prepare only upon operator click; source images and CSV stay unchanged."""
    package = _package(batch_id)
    from scripts.render_m6_ocr_overlays import (
        OverlayPreparationError, prepare_review_overlays,
    )

    try:
        return prepare_review_overlays(package)
    except (OSError, ValueError, OverlayPreparationError) as exc:
        raise _blocked(f"不能准备标注预览：{exc}") from exc


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _archive_snapshot(package: Path):
    """Hold a bounded snapshot; validate before sending HTTP headers."""
    from scripts.capture_m6_paddle_ocr import (
        MAX_PNG_BYTES, OCREvidenceError, _identities, _read_regular, _review_rows,
    )
    from scripts.render_m6_ocr_overlays import MANIFEST_NAME, OUTPUT_NAME
    from scripts.verify_m6_ocr_capture import (
        OCRCaptureEvidenceError, verify_ocr_capture,
    )

    spool = tempfile.SpooledTemporaryFile(mode="w+b", max_size=8 * 1024 * 1024)
    try:
        reviews_sha, initial_rows = _review_rows(package)
        cohort, rows, _ = _identities(initial_rows)
        verified = verify_ocr_capture(
            package / "m6_paddle_ocr_evidence.json", rows,
            cohort_id=cohort, reviews_sha256=reviews_sha,
        )
        with _open_regular(package / OUTPUT_NAME) as source:
            copied = 0
            while chunk := source.read(CHUNK_BYTES):
                copied += len(chunk)
                if copied > _MAX_ZIP_BYTES:
                    raise _blocked("标注 ZIP 大小超出上限")
                spool.write(chunk)
        if not copied:
            raise _blocked("标注 ZIP 为空")
        spool.seek(0)
        with zipfile.ZipFile(spool) as zf:
            entries = zf.infolist()
            expected_names = {MANIFEST_NAME, *(row["output_image_path"] for row in rows)}
            if len(entries) != len(expected_names) or {i.filename for i in entries} != expected_names:
                raise _blocked("标注 ZIP 的文件清单不符合审核协议")
            if any(i.flag_bits & 1 or i.file_size > _MAX_OVERLAY_BYTES
                   for i in entries if i.filename != MANIFEST_NAME):
                raise _blocked("标注图超出单文件限制或为加密 ZIP")
            info = zf.getinfo(MANIFEST_NAME)
            if (info.file_size > MAX_CSV_BYTES or info.flag_bits & 1
                or sum(i.file_size for i in entries) > _MAX_ZIP_BYTES):
                raise _blocked("标注压缩包大小或格式超出限制")
            data = zf.read(MANIFEST_NAME)
            if len(data) > MAX_CSV_BYTES:
                raise _blocked("标注清单过大")
            manifest = json.loads(data)
            if (
                not isinstance(manifest, dict)
                or manifest.get("schema_version") != "m6-ocr-review-overlays-1"
                or manifest.get("cohort_id") != cohort
                or manifest.get("reviews_csv_sha256") != reviews_sha
                or manifest.get("ocr_capture_sha256") != verified["evidence_sha256"]
                or manifest.get("overlay_count") != len(rows)
                or manifest.get("positioned_line_count") != verified["positioned_line_count"]
                or manifest.get("unlocated_line_count") != verified["unlocated_line_count"]
                or manifest.get("automatically_accepted") is not False
                or manifest.get("quality_verified") is not False
                or manifest.get("runtime_attested") is not False
            ):
                raise _blocked("标注清单与当前冻结的 OCR 或评审表不一致")
            items = manifest.get("items")
            if not isinstance(items, list) or len(items) != len(rows):
                raise _blocked("标注清单缺少完整 SKU×版式集合")
            by_key = {}
            for item in items:
                if not isinstance(item, dict):
                    raise _blocked("标注清单记录格式错误")
                key = (item.get("sku_id"), item.get("variant_id"))
                if key in by_key:
                    raise _blocked("标注清单包含重复 SKU×版式")
                by_key[key] = item
            for row in rows:
                key = (row["sku_id"], row["variant_id"])
                item = by_key.get(key)
                if (
                    item is None or item.get("overlay_path") != row["output_image_path"]
                    or item.get("source_image_sha256") != row["output_image_sha256"]
                    or not isinstance(item.get("overlay_sha256"), str)
                ):
                    raise _blocked("标注清单与当前 SKU、版式或原图摘要不一致")
                source_bytes = _read_regular(
                    package / row["output_image_path"], MAX_PNG_BYTES,
                )
                if _sha(source_bytes) != row["output_image_sha256"]:
                    raise _blocked("原始图片已更改，标注包已过期")
                with zf.open(item["overlay_path"]) as image_stream:
                    digest = hashlib.sha256()
                    size = 0
                    while chunk := image_stream.read(CHUNK_BYTES):
                        size += len(chunk)
                        if size > _MAX_OVERLAY_BYTES:
                            raise _blocked("标注图解压后的体积超限")
                        digest.update(chunk)
                if digest.hexdigest() != item["overlay_sha256"]:
                    raise _blocked("ZIP 中的标注图摘要与清单不一致")
        spool.seek(0)
        return spool
    except HTTPException:
        spool.close()
        raise
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, zipfile.BadZipFile,
            OCREvidenceError, OCRCaptureEvidenceError) as exc:
        spool.close()
        raise _blocked(f"标注包损坏或与当前证据不匹配：{exc}") from exc
    except BaseException:
        spool.close()
        raise




# Read only one SKU at a time. Full-cohort evidence membership is verified once
# by the existing verifier; only the chosen poster images are then hashed.
_MAX_PREVIEW_LINES = 20
_MAX_TEXT_CHARS = 160
_REVIEW_LABEL_FIELDS = (
    "title", "subtitle", "price", "original_price", "price_position",
    "original_price_position", "text_legibility", "overall_accept",
    "overall_reason", "reviewer_alias", "reviewed_at_utc",
)


@router.get("/{batch_id}/m6-ocr-review")
def inspect_sku_ocr_review(batch_id: str, sku_id: str | None = None):
    """Pair frozen human CSV judgments with bounded OCR line previews.

    This is NOT an accuracy score or model-execution attestation. It never
    writes reviews, changes status or runs PaddleOCR.
    """
    from scripts.capture_m6_paddle_ocr import (
        MAX_PNG_BYTES, OCREvidenceError, _identities, _read_regular, _review_rows,
    )
    from scripts.verify_m6_ocr_capture import (
        OCRCaptureEvidenceError, _read_snapshot, verify_ocr_capture,
    )

    package = _package(batch_id)
    try:
        reviews_sha, original_rows = _review_rows(package)
        cohort, rows, _ = _identities(original_rows)
        evidence_file = package / "m6_paddle_ocr_evidence.json"
        verified = verify_ocr_capture(
            evidence_file, rows, cohort_id=cohort, reviews_sha256=reviews_sha,
        )
        document, evidence_sha = _read_snapshot(evidence_file)
        if evidence_sha != verified["evidence_sha256"]:
            raise OCRCaptureEvidenceError("OCR 证据在校验后被替换")
        ids = sorted({row["sku_id"] for row in rows})
        selected = sku_id if sku_id is not None else ids[0]
        if selected not in ids:
            raise HTTPException(status_code=404, detail="该批次中没有指定的 SKU")

        row_by_key = {(row["sku_id"], row["variant_id"]): row for row in rows}
        observation_by_key = {
            (row["sku_id"], row["variant_id"]): row
            for row in document["observations"]
        }
        previews = []
        for variant in ("square", "feed_4x5_native"):
            row = row_by_key[(selected, variant)]
            # Read and verify the exact same bytes that the CSV identifies.
            image = _read_regular(package / row["output_image_path"], MAX_PNG_BYTES)
            if hashlib.sha256(image).hexdigest() != row["output_image_sha256"]:
                raise OCRCaptureEvidenceError("原始图片已改变；当前 OCR 复核已过期")
            observation = observation_by_key[(selected, variant)]
            lines = observation["recognized_lines"]
            preview_lines = [
                {
                    "text": line["text"][:_MAX_TEXT_CHARS],
                    "confidence": line["confidence"],
                    "bbox": line.get("bbox"),
                }
                for line in lines[:_MAX_PREVIEW_LINES]
            ]
            previews.append({
                "variant_id": variant,
                "poster_id": row["poster_id"],
                "image_sha256": row["output_image_sha256"],
                "review_labels": {key: row.get(key, "") for key in _REVIEW_LABEL_FIELDS},
                "recognized_line_count": len(lines),
                "preview_line_count": len(preview_lines),
                "lines_truncated_count": len(lines) - len(preview_lines),
                "unlocated_line_count": sum(line.get("bbox") is None for line in lines),
                "lines": preview_lines,
                "status": "CAPTURED_REVIEW_REQUIRED",
            })
    except HTTPException:
        raise
    except (OSError, ValueError, OCRCaptureEvidenceError, OCREvidenceError) as exc:
        raise _blocked(f"OCR 逐 SKU 核对所需证据不完整或已过期：{exc}") from exc

    return {
        "cohort_id": cohort,
        "reviews_csv_sha256": reviews_sha,
        "ocr_capture_sha256": verified["evidence_sha256"],
        "ocr_capture_consistency": verified["status"],
        "sku_ids": ids,
        "selected_sku_id": selected,
        "variants": previews,
        "automatically_accepted": False,
        "quality_verified": False,
        "runtime_attested": False,
    }


@router.get("/{batch_id}/m6-overlay/download")
def download_overlay(batch_id: str):
    """Read-only, snapshot-verified local archive download."""
    package = _package(batch_id)
    spool = _archive_snapshot(package)

    def stream() -> Iterator[bytes]:
        try:
            while chunk := spool.read(CHUNK_BYTES):
                yield chunk
        finally:
            spool.close()

    return StreamingResponse(
        stream(), media_type="application/zip", headers={
            "Content-Disposition": f'attachment; filename="m6-ocr-overlay-{batch_id}.zip"',
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
