"""Capture genuine PaddleOCR 3.x text as local, unapproved M6 evidence.

Requires already-installed PaddleOCR/PaddlePaddle and caller-supplied local
detection/recognition model directories. Does not enforce OS network isolation.
No synthetic results can be passed through the CLI. Never sets quality PASS.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.metadata
import io
import json
import math
import os
import platform
import re
import stat
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from PIL import Image

MAX_CSV_BYTES = 2_000_000
MAX_PNG_BYTES = 20 * 1024 * 1024
SKU_PATTERN = re.compile(r"^[A-Za-z0-9_-]{2,64}$")
SHA_PATTERN = re.compile(r"^[a-f0-9]{64}$")
VARIANTS = {
    "square": ((1024, 1024), "square"),
    "feed_4x5_native": ((1080, 1350), "native-4x5"),
}
# Exact M6 output worksheet schema: fail closed on drift.
REVIEW_FIELDS = (
    "cohort_id", "sku_id", "poster_id", "source_version", "variant_id",
    "output_image_path", "source_product_sha256", "request_sha256",
    "spec_sha256", "output_image_sha256", "source_square_image_sha256",
    "reviewer_alias", "sku_integrity", "text_legibility", "title",
    "subtitle", "price", "original_price", "price_position",
    "original_price_position", "cta", "composition", "overall_accept",
    "overall_reason", "reviewed_at_utc",
)
IDENTITY_COLUMNS = (
    "poster_id", "source_version", "source_product_sha256",
    "request_sha256", "spec_sha256", "source_square_image_sha256",
)


class OCREvidenceError(ValueError):
    """Input integrity or prediction evidence cannot be trusted."""


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _open_regular(path: Path):
    """Open a regular file without following a symlink at the leaf."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise OCREvidenceError(f"无法安全读取文件：{path.name}") from exc
    file = os.fdopen(fd, "rb")
    if not stat.S_ISREG(os.fstat(file.fileno()).st_mode):
        file.close()
        raise OCREvidenceError("只允许普通文件，禁止链接/设备文件")
    return file


def _read_regular(path: Path, limit: int) -> bytes:
    with _open_regular(path) as file:
        raw = file.read(limit + 1)
    if not raw or len(raw) > limit:
        raise OCREvidenceError(f"文件为空或超过限制：{path.name}")
    return raw


def _review_rows(package: Path) -> tuple[str, list[dict[str, str]]]:
    raw = _read_regular(package / "m6_output_reviews.csv", MAX_CSV_BYTES)
    try:
        reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig"), newline=""), strict=True)
        header = reader.fieldnames
        if not header or len(header) != len(set(header)) or set(header) != set(REVIEW_FIELDS):
            raise OCREvidenceError("评审 CSV 不符合官方列定义")
        rows = []
        for row in reader:
            if None in row or any(v is None for v in row.values()):
                raise OCREvidenceError("评审 CSV 行列数错误")
            rows.append(row)
            if len(rows) > 200:
                raise OCREvidenceError("M6 评审行数不能超过 200")
    except (UnicodeError, csv.Error) as exc:
        raise OCREvidenceError("M6 CSV 不是合法的 UTF-8 CSV") from exc
    if not rows:
        raise OCREvidenceError("M6 评审 CSV 不能为空")
    return _sha(raw), rows


def _identities(rows: list[dict[str, str]]):
    cohorts = {row["cohort_id"] for row in rows}
    if len(cohorts) != 1 or not next(iter(cohorts)):
        raise OCREvidenceError("M6 必须只有一个非空 cohort_id")
    seen = set()
    pinned: dict[str, tuple[str, ...]] = {}
    for row in rows:
        sku, variant = row["sku_id"], row["variant_id"]
        if not SKU_PATTERN.fullmatch(sku) or variant not in VARIANTS:
            raise OCREvidenceError("SKU 或版式 ID 不合法")
        key = (sku, variant)
        if key in seen:
            raise OCREvidenceError("重复的 SKU×版式")
        seen.add(key)
        expected = f"images/{sku}-{VARIANTS[variant][1]}.png"
        if row["output_image_path"] != expected:
            raise OCREvidenceError("图片路径不符合固定的相对路径规则")
        if not SHA_PATTERN.fullmatch(row["output_image_sha256"]):
            raise OCREvidenceError("图片 SHA-256 不合法")
        if not re.fullmatch(r"[a-f0-9]{32}", row["poster_id"]):
            raise OCREvidenceError("poster_id 格式不合法")
        if not row["source_version"].isdigit() or int(row["source_version"]) < 1:
            raise OCREvidenceError("source_version 不合法")
        if any(
            not SHA_PATTERN.fullmatch(row[field])
            for field in IDENTITY_COLUMNS if field.endswith("_sha256")
        ):
            raise OCREvidenceError("来源 SHA-256 不合法")
        identity = tuple(row[field] for field in IDENTITY_COLUMNS)
        if sku in pinned and pinned[sku] != identity:
            raise OCREvidenceError("同一 SKU 两种版式的来源凭据不一致")
        pinned[sku] = identity
    if not 1 <= len(pinned) <= 100 or len(seen) != 2 * len(pinned):
        raise OCREvidenceError("缺少完整的 SKU×方形/4:5 版式对")
    return next(iter(cohorts)), sorted(
        rows, key=lambda row: (row["sku_id"], row["variant_id"])
    ), len(pinned)


def _model_fingerprint(directory: Path) -> dict[str, Any]:
    if directory.is_symlink() or not directory.is_dir():
        raise OCREvidenceError("本地模型目录无效或包含符号链接")
    root = directory.resolve(strict=True)
    files = []
    for current, directories, filenames in os.walk(root, followlinks=False):
        parent = Path(current)
        for name in directories:
            if (parent / name).is_symlink():
                raise OCREvidenceError("模型目录包含符号链接")
        for name in filenames:
            candidate = parent / name
            if candidate.is_symlink() or not candidate.is_file():
                raise OCREvidenceError("模型文件不是普通文件或包含符号链接")
            files.append(candidate)
    if not 1 <= len(files) <= 500:
        raise OCREvidenceError("模型文件数量不合法")
    digest = hashlib.sha256()
    total = 0
    for file in sorted(files, key=lambda path: path.relative_to(root).as_posix()):
        name = file.relative_to(root).as_posix().encode("utf-8")
        digest.update(len(name).to_bytes(4, "big"))
        digest.update(name)
        content_digest = hashlib.sha256()
        with _open_regular(file) as stream:
            while data := stream.read(1024 * 1024):
                total += len(data)
                if total > 2 * 1024**3:
                    raise OCREvidenceError("模型文件总大小超过 2 GiB")
                content_digest.update(data)
        digest.update(content_digest.digest())
    return {
        "sha256": digest.hexdigest(),
        "file_count": len(files),
        "total_bytes": total,
    }


def _rgb_snapshot(raw: bytes, size: tuple[int, int]):
    import numpy as np  # Optional PaddleOCR dependency, not required by base install.

    try:
        with Image.open(io.BytesIO(raw)) as image:
            if image.format != "PNG" or image.size != size:
                raise OCREvidenceError("PNG 格式或版式尺寸不符合要求")
            image.load()
            return np.asarray(image.convert("RGB"))
    except (OSError, Image.DecompressionBombError) as exc:
        raise OCREvidenceError("PNG 损坏或尺寸异常") from exc


def _ocr_lines(result: Any) -> list[dict[str, Any]]:
    if result is None:
        raise OCREvidenceError("OCR 未返回结果")
    lines = []
    try:
        for page in result:
            texts = page["rec_texts"]
            scores = page["rec_scores"]
            if len(texts) != len(scores) or len(lines) + len(texts) > 2000:
                raise OCREvidenceError("OCR 文本与置信度数量不一致或超限")
            for text, value in zip(texts, scores):
                confidence = float(value)
                if not isinstance(text, str) or not math.isfinite(confidence) or not 0 <= confidence <= 1:
                    raise OCREvidenceError("OCR 置信度或文本不合法")
                lines.append({"text": text, "confidence": confidence})
    except (KeyError, TypeError, AttributeError, ValueError) as exc:
        if isinstance(exc, OCREvidenceError):
            raise
        raise OCREvidenceError("PaddleOCR 结果结构不兼容；需要 3.x rec_texts/rec_scores") from exc
    return lines


def capture(
    package_dir: Path,
    detection_model_dir: Path,
    recognition_model_dir: Path,
    *,
    engine_factory: Callable[..., Any] | None = None,
    device: str = "cpu",
) -> dict[str, Any]:
    """Capture actual text, or isolated test-double output, never quality PASS."""
    package = Path(package_dir).expanduser()
    if package.is_symlink() or not package.is_dir():
        raise OCREvidenceError("必须指定本地普通审核包目录")
    package = package.resolve(strict=True)
    images = package / "images"
    if images.is_symlink() or not images.is_dir():
        raise OCREvidenceError("审核包 images 目录不存在或是符号链接")
    destination = package / "m6_paddle_ocr_evidence.json"
    if destination.exists() or destination.is_symlink():
        raise OCREvidenceError("OCR 证据文件已存在，禁止覆盖")
    csv_sha, original_rows = _review_rows(package)
    cohort, rows, sku_count = _identities(original_rows)
    det_dir, rec_dir = Path(detection_model_dir).expanduser(), Path(recognition_model_dir).expanduser()
    det_digest = _model_fingerprint(det_dir)
    rec_digest = _model_fingerprint(rec_dir)
    if device not in {"cpu", "gpu", "gpu:0"}:
        raise OCREvidenceError("设备仅允许 cpu/gpu/gpu:0")
    real = engine_factory is None
    if real:
        try:
            from paddleocr import PaddleOCR
            paddleocr_version = importlib.metadata.version("paddleocr")
            paddle_version = importlib.metadata.version("paddlepaddle")
        except (ImportError, importlib.metadata.PackageNotFoundError) as exc:
            raise OCREvidenceError("请先安装并确认 PaddleOCR 3.x 和 PaddlePaddle") from exc
        if not paddleocr_version.startswith("3."):
            raise OCREvidenceError("只支持 PaddleOCR 3.x")
        engine_factory = PaddleOCR
    else:
        paddleocr_version = "test-double-not-real-ocr"
        paddle_version = "test-double-not-real-ocr"
    start = time.perf_counter()
    engine = engine_factory(
        text_detection_model_dir=str(det_dir.resolve()),
        text_recognition_model_dir=str(rec_dir.resolve()),
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=False,
        device=device,
        lang="ch",
    )
    observations = []
    for row in rows:
        path = package / row["output_image_path"]
        data = _read_regular(path, MAX_PNG_BYTES)
        digest = _sha(data)
        if digest != row["output_image_sha256"]:
            raise OCREvidenceError(f"PNG 已被修改：{row['sku_id']}/{row['variant_id']}")
        snapshot = _rgb_snapshot(data, VARIANTS[row["variant_id"]][0])
        recognized = _ocr_lines(engine.predict(snapshot))
        observations.append({
            "sku_id": row["sku_id"],
            "variant_id": row["variant_id"],
            "poster_id": row["poster_id"],
            "image_sha256": digest,
            "recognized_lines": recognized,
            "recognized_line_count": len(recognized),
            "status": "CAPTURED_REVIEW_REQUIRED",
        })
    summary = {
        "schema_version": "m6-paddle-ocr-evidence-1",
        "cohort_id": cohort,
        "reviews_csv_sha256": csv_sha,
        "sku_count": sku_count,
        "expected_variant_count": 2 * sku_count,
        "captured_variant_count": len(observations),
        "min_20_real_skus_proven": False,
        "source_authorizations_verified": False,
        "human_review_performed": False,
        "automatically_accepted": False,
        "real_ocr_inference_attempted": real,
        "paddleocr_version": paddleocr_version,
        "paddlepaddle_version": paddle_version,
        "python_version": sys.version.split()[0],
        "os_platform": platform.platform(),
        "elapsed_seconds": round(time.perf_counter() - start, 3),
        "model_digests": {"detection": det_digest, "recognition": rec_digest},
        "device": device,
        "captured_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "observations": observations,
        "next_step": "Compare OCR with frozen specs and independent human reviews; run the final M6 gate separately.",
    }
    # Build a complete UTF-8 payload BEFORE publishing evidence. The hard link
    # is atomic and no-clobber: partial output or conflicting attempts never
    # overwrite an existing reviewer artifact.
    payload = (json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=package, prefix=".m6-ocr-", suffix=".tmp", mode="w+b", delete=False
        ) as file:
            temporary = Path(file.name)
            os.chmod(temporary, 0o600)
            file.write(payload)
            file.flush()
            os.fsync(file.fileno())
        os.link(temporary, destination, follow_symlinks=False)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Capture local PaddleOCR 3.x M6 evidence")
    parser.add_argument("--package-dir", required=True, type=Path)
    parser.add_argument("--det-model-dir", required=True, type=Path)
    parser.add_argument("--rec-model-dir", required=True, type=Path)
    parser.add_argument("--device", choices=("cpu", "gpu", "gpu:0"), default="cpu")
    args = parser.parse_args(argv)
    try:
        report = capture(
            args.package_dir, args.det_model_dir, args.rec_model_dir, device=args.device
        )
    except (OCREvidenceError, OSError, ValueError) as exc:
        parser.exit(2, f"M6 OCR 采集失败：{exc}\n")
    print(json.dumps({
        key: report[key] for key in (
            "cohort_id", "sku_count", "captured_variant_count", "automatically_accepted"
        )
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
