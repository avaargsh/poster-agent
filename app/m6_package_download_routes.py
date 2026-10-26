"""Download a bounded, integrity-checked M6 review package as a local-only ZIP.

No arbitrary filesystem browsing: entries are derived solely from the checked
batch SKU set and the two fixed M6 variants. Original material never leaves
the machine except through the operator's explicit loopback download request.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import stat
import tempfile
import zipfile
from pathlib import Path
from typing import BinaryIO, Iterator

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from .m6_batch_package_routes import _verified_source

router = APIRouter(prefix="/api/batches", tags=["m6-download"])

MAX_CSV_BYTES = 2_000_000
MAX_TOTAL_UNCOMPRESSED_BYTES = 256 * 1024 * 1024
CHUNK_BYTES = 64 * 1024
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_VARIANT_NAME = {
    "square": "square",
    "feed_4x5_native": "native-4x5",
}


def _blocked(message: str) -> HTTPException:
    return HTTPException(status_code=409, detail="M6 ZIP 拒绝导出：" + message)


def _open_regular(path: Path) -> BinaryIO:
    """Do not follow leaf symlinks or open FIFOs/devices while packaging."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(path, flags)
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            os.close(fd)
            raise _blocked("包含非普通文件")
        return os.fdopen(fd, "rb")
    except OSError as exc:
        raise _blocked("必需文件缺失或包含符号链接") from exc


def _bounded_bytes(path: Path, limit: int) -> bytes:
    with _open_regular(path) as file:
        raw = file.read(limit + 1)
    if not raw or len(raw) > limit:
        raise _blocked("CSV/JSON 为空或超出文件大小限制")
    return raw


def _read_review_rows(raw: bytes, expected: dict[str, str], cohort: str) -> list[dict[str, str]]:
    # Import after app.main has finished registering routes; the official M6
    # validator imports the CLI batch runner, which depends on app.main.
    from scripts.validate_m6_cohort import REVIEW_FIELDS

    try:
        reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig"), newline=""), strict=True)
        fields = reader.fieldnames
        if not fields or len(fields) != len(set(fields)) or set(fields) != set(REVIEW_FIELDS):
            raise _blocked("reviews CSV 列名不匹配官方模板")
        rows: list[dict[str, str]] = []
        identities: set[tuple[str, str]] = set()
        for row in reader:
            if None in row or any(value is None for value in row.values()):
                raise _blocked("reviews CSV 行的列数非法")
            sku, variant = row["sku_id"], row["variant_id"]
            key = (sku, variant)
            if (
                sku not in expected or variant not in _VARIANT_NAME
                or key in identities or row["poster_id"] != expected[sku]
                or row["cohort_id"] != cohort
            ):
                raise _blocked("reviews CSV 包含不属于本批次的 SKU / 版式 / 海报")
            identities.add(key)
            rows.append(row)
            if len(rows) > 2 * len(expected):
                raise _blocked("reviews CSV 行数超出预期")
        if identities != {
            (sku, variant) for sku in expected for variant in _VARIANT_NAME
        }:
            raise _blocked("reviews CSV 缺少本批次必需的 SKU×版式记录")
        return rows
    except (UnicodeError, ValueError, csv.Error) as exc:
        raise _blocked("reviews CSV 编码或格式错误") from exc


def _add_bounded_file(
    archive: zipfile.ZipFile,
    local: Path,
    name: str,
    limit: int,
    total: int,
    expected_sha256: str | None = None,
) -> int:
    with _open_regular(local) as file:
        size = os.fstat(file.fileno()).st_size
        if (
            size <= 0 or size > limit
            or total + size > MAX_TOTAL_UNCOMPRESSED_BYTES
        ):
            raise _blocked("审核包文件过大或为空")
        digest = hashlib.sha256()
        consumed = 0
        with archive.open(name, "w") as target:
            while True:
                chunk = file.read(CHUNK_BYTES)
                if not chunk:
                    break
                consumed += len(chunk)
                if consumed > limit or total + consumed > MAX_TOTAL_UNCOMPRESSED_BYTES:
                    raise _blocked("审核包文件在读取时增长超过限制")
                digest.update(chunk)
                target.write(chunk)
        if expected_sha256 is not None and digest.hexdigest() != expected_sha256:
            raise _blocked("图片字节与 reviews CSV 中 SHA-256 不一致")
        if consumed != size:
            raise _blocked("审核包文件在读取过程中发生变化")
        return total + consumed


def _zip_contents(
    archive: zipfile.ZipFile, folder: Path, expected: dict[str, str],
) -> None:
    from scripts.validate_m6_cohort import MAX_REVIEWED_PNG_BYTES

    if folder.is_symlink() or not folder.is_dir():
        raise _blocked("审核包目录不存在或为符号链接")
    images_dir = folder / "images"
    if images_dir.is_symlink() or not images_dir.is_dir():
        raise _blocked("审核包 images 目录不存在或为符号链接")

    summary = _bounded_bytes(folder / "m6_preparation.json", MAX_CSV_BYTES)
    try:
        metadata = json.loads(summary)
    except (UnicodeError, ValueError) as exc:
        raise _blocked("M6 审核包摘要不是有效 JSON") from exc
    if not isinstance(metadata, dict) or not isinstance(metadata.get("cohort_id"), str):
        raise _blocked("M6 审核包摘要缺少 cohort_id")
    cohort = metadata["cohort_id"]
    registry = _bounded_bytes(folder / "m6_cohort_registry.csv", MAX_CSV_BYTES)
    raw_reviews = _bounded_bytes(folder / "m6_output_reviews.csv", MAX_CSV_BYTES)
    rows = _read_review_rows(raw_reviews, expected, cohort)

    # Archive precisely the bounded bytes that were inspected. In particular,
    # an operator editing a CSV while the ZIP is built cannot cause the archive
    # to contain different data from the schema-checked review rows.
    total = sum(map(len, (summary, registry, raw_reviews)))
    if total > MAX_TOTAL_UNCOMPRESSED_BYTES:
        raise _blocked("M6 审核表超出 ZIP 容量限制")
    for filename, raw in (
        ("m6_preparation.json", summary),
        ("m6_cohort_registry.csv", registry),
        ("m6_output_reviews.csv", raw_reviews),
    ):
        archive.writestr(filename, raw)
    for row in rows:
        sku, variant = row["sku_id"], row["variant_id"]
        name = f"images/{sku}-{_VARIANT_NAME[variant]}.png"
        path, digest = row["output_image_path"], row["output_image_sha256"]
        if not path and not digest:
            continue  # Preserve missing artifacts as UNVERIFIED review rows.
        if path != name or not _SHA256.fullmatch(digest):
            raise _blocked("图片路径不符或 SHA-256 不合法")
        total = _add_bounded_file(
            archive, folder / name, name,
            MAX_REVIEWED_PNG_BYTES, total, expected_sha256=digest,
        )


@router.get("/{batch_id}/m6-package/download")
def download_m6_package(batch_id: str):
    """Build ZIP fully before HTTP headers; never stream a partially valid archive."""
    _, folder = _verified_source(batch_id)
    # Resolve identities from the same existing gate as the web review button.
    from .batch_import_routes import batch_review_scope

    scope = batch_review_scope(batch_id)
    expected = {row["sku_id"]: row["poster_id"] for row in scope["rows"]}
    spool = tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024, mode="w+b")
    try:
        with zipfile.ZipFile(spool, "w", compression=zipfile.ZIP_STORED, allowZip64=False) as archive:
            _zip_contents(archive, folder, expected)
        spool.seek(0)
    except (OSError, ValueError, zipfile.BadZipFile, RuntimeError) as exc:
        spool.close()
        raise _blocked("审核包无法安全生成 ZIP") from exc
    except BaseException:
        spool.close()
        raise

    def stream() -> Iterator[bytes]:
        try:
            while data := spool.read(CHUNK_BYTES):
                yield data
        finally:
            spool.close()

    return StreamingResponse(
        stream(),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="m6-review-{batch_id}.zip"',
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
