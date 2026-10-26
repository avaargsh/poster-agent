"""Render non-destructive visual OCR review overlays from frozen M6 evidence.

No OCR inference, price correctness decision, cloud call, or modification of
source images. Creates one locally held ZIP after checking both the worksheet
snapshot and its exact OCR capture. JSON declarations are not attestation.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import tempfile
import zipfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from app.ocr_overlay import MAX_DRAWN_BOXES, OCR_BOX_COLOR, _valid_box
from scripts.capture_m6_paddle_ocr import (
    MAX_PNG_BYTES, OCREvidenceError, _identities, _read_regular, _review_rows,
)
from scripts.verify_m6_ocr_capture import (
    OCRCaptureEvidenceError, _read_snapshot, verify_ocr_capture,
)

OUTPUT_NAME = "m6_ocr_review_overlays.zip"
MANIFEST_NAME = "m6_ocr_overlay_manifest.json"
MAX_OUTPUT_BYTES = 512 * 1024 * 1024


class OverlayPreparationError(ValueError):
    """The frozen package cannot safely produce an OCR review overlay."""


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _annotate(
    original: bytes,
    lines: list[dict],
    expected_size: tuple[int, int],
) -> tuple[bytes, int, int]:
    try:
        with Image.open(io.BytesIO(original)) as image:
            if image.format != "PNG" or image.size != expected_size:
                raise OverlayPreparationError("审核 PNG 格式或版式尺寸不符合当前 M6 约定")
            image.load()
            canvas = image.convert("RGBA")
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        if isinstance(exc, OverlayPreparationError):
            raise
        raise OverlayPreparationError("审核图片损坏或无法解码") from exc

    marks = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(marks, "RGBA")
    font = ImageFont.load_default(size=16)
    drawn = 0
    unlocated = 0
    for index, line in enumerate(lines):
        bbox = _valid_box(line.get("bbox"), canvas.size)
        if bbox is None:
            unlocated += 1
            continue
        if drawn >= MAX_DRAWN_BOXES:
            continue
        draw.rectangle(bbox, outline=OCR_BOX_COLOR, width=3)
        draw.text(
            (min(bbox[0] + 3, canvas.width - 38), min(bbox[1] + 3, canvas.height - 22)),
            str(index + 1),
            font=font,
            fill=OCR_BOX_COLOR,
            stroke_width=2,
            stroke_fill=(5, 14, 23, 255),
        )
        drawn += 1
    draw.rectangle((0, 0, canvas.width, 62), fill=(9, 21, 34, 225))
    draw.text(
        (12, 8), "M6 OCR VISUAL REFERENCE - HUMAN REVIEW REQUIRED",
        font=font, fill=(255, 255, 255, 255),
    )
    draw.text(
        (12, 36),
        f"bbox {drawn}/{len(lines)} | missing {unlocated} | limit {MAX_DRAWN_BOXES}",
        font=font, fill=(255, 231, 163, 255),
    )
    result = Image.alpha_composite(canvas, marks).convert("RGB")
    buffer = io.BytesIO()
    result.save(buffer, "PNG", optimize=True)
    return buffer.getvalue(), drawn, unlocated


def prepare_review_overlays(package_dir: Path) -> dict:
    """Build and exclusively publish ZIP of reviewer overlays, without OCR."""
    package = Path(package_dir).expanduser()
    if package.is_symlink() or not package.is_dir():
        raise OverlayPreparationError("审核包目录无效或为符号链接")
    package = package.resolve(strict=True)
    images_dir = package / "images"
    if images_dir.is_symlink() or not images_dir.is_dir():
        raise OverlayPreparationError("images 目录缺失或为符号链接")
    destination = package / OUTPUT_NAME
    if destination.exists() or destination.is_symlink():
        raise OverlayPreparationError("OCR 标注审核包已存在；禁止覆盖原有审核结果")

    try:
        reviews_sha, original_rows = _review_rows(package)
        cohort, rows, _ = _identities(original_rows)
        evidence_path = package / "m6_paddle_ocr_evidence.json"
        verified = verify_ocr_capture(
            evidence_path, rows, cohort_id=cohort, reviews_sha256=reviews_sha,
        )
        document, evidence_sha = _read_snapshot(evidence_path)
        if evidence_sha != verified["evidence_sha256"]:
            raise OverlayPreparationError("OCR 证据在校验后被修改")
    except (OCREvidenceError, OCRCaptureEvidenceError, OSError, ValueError) as exc:
        raise OverlayPreparationError(f"M6 OCR 证据前置核验失败：{exc}") from exc

    by_key = {(item["sku_id"], item["variant_id"]): item
              for item in document["observations"]}
    manifest = {
        "schema_version": "m6-ocr-review-overlays-1",
        "cohort_id": cohort,
        "reviews_csv_sha256": reviews_sha,
        "ocr_capture_sha256": evidence_sha,
        "overlay_count": len(rows),
        "positioned_line_count": verified["positioned_line_count"],
        "unlocated_line_count": verified["unlocated_line_count"],
        "automatically_accepted": False,
        "quality_verified": False,
        "runtime_attested": False,
        "usage": (
            "Visual aid for operator review only; numeric box labels refer to"
            " 1-based positions of the recognized_lines in the frozen OCR JSON."
        ),
        "items": [],
    }
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=package, prefix=".m6-overlays-", suffix=".tmp", delete=False,
        ) as temp:
            temporary = Path(temp.name)
            os.chmod(temporary, 0o600)
            with zipfile.ZipFile(temp, "w", compression=zipfile.ZIP_STORED, allowZip64=False) as zf:
                total = 0
                for row in rows:
                    relative = row["output_image_path"]
                    variant = row["variant_id"]
                    size = (1024, 1024) if variant == "square" else (1080, 1350)
                    source_bytes = _read_regular(images_dir / Path(relative).name, MAX_PNG_BYTES)
                    if _sha(source_bytes) != row["output_image_sha256"]:
                        raise OverlayPreparationError(
                            f"{row['sku_id']}/{variant}: 图片 SHA-256 与冻结 CSV 不一致"
                        )
                    observed = by_key[(row["sku_id"], variant)]
                    annotated, drawn, unlocated = _annotate(
                        source_bytes, observed["recognized_lines"], size,
                    )
                    total += len(annotated)
                    if total > MAX_OUTPUT_BYTES:
                        raise OverlayPreparationError("标注图片总量超出 512 MiB 上限")
                    zf.writestr(relative, annotated)
                    manifest["items"].append({
                        "sku_id": row["sku_id"],
                        "variant_id": variant,
                        "source_image_sha256": row["output_image_sha256"],
                        "overlay_path": relative,
                        "overlay_sha256": _sha(annotated),
                        "bbox_drawn": drawn,
                        "unlocated_line_count": unlocated,
                        "undrawn_box_limit": max(
                            0, sum(line.get("bbox") is not None
                                   for line in observed["recognized_lines"]) - drawn
                        ),
                    })
                zf.writestr(
                    MANIFEST_NAME,
                    json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                )
            temp.flush()
            os.fsync(temp.fileno())
        os.link(temporary, destination, follow_symlinks=False)
    except FileExistsError as exc:
        raise OverlayPreparationError("OCR 标注审核包已存在；并发生成不得覆盖") from exc
    except (OCREvidenceError, OSError, zipfile.BadZipFile, ValueError) as exc:
        if isinstance(exc, OverlayPreparationError):
            raise
        raise OverlayPreparationError(f"OCR 审核预览生成失败：{exc}") from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate local non-destructive OCR review overlays from frozen M6 evidence",
    )
    parser.add_argument("--package-dir", required=True, type=Path)
    args = parser.parse_args()
    try:
        manifest = prepare_review_overlays(args.package_dir)
    except (OverlayPreparationError, OSError) as exc:
        parser.exit(2, f"M6 OCR 标注预览生成失败：{exc}\n")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
