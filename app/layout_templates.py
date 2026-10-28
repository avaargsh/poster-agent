"""Reusable layout snapshot from an immutable poster version.

Copies *only* visual placement and layer offsets. Never transfers product
images, title, price, logo asset, background, approval, or review claims.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json

from app import storage
from app.schemas import PosterSpec

LAYOUT_FIELDS = ("layout_id", "product_align", "product_scale", "layer_layout")
MAX_ROWS = 100


def read_template(poster_id: str, version: int) -> tuple[dict, dict]:
    """Resolve an already-published version; caller maps errors to HTTP 4xx."""
    old = storage.version_view(poster_id, version)
    spec = PosterSpec.model_validate(old["spec"])
    source = storage.poster_dir(poster_id) / "versions" / f"v{version}.json"
    raw = source.read_bytes()
    template = {
        "layout_id": spec.layout_id,
        "product_align": spec.product_align,
        "product_scale": spec.product_scale,
        "layer_layout": spec.layer_layout.model_dump(mode="json"),
    }
    provenance = {
        "poster_id": poster_id,
        "version": version,
        "spec_sha256": hashlib.sha256(raw).hexdigest(),
    }
    return template, provenance


def embed_template(raw_csv: bytes, template: dict) -> bytes:
    """Deterministically freeze a saved layout into every SKU manifest row.

    Caller still invokes the existing real_sku_batch preflight to validate
    prices, image paths and actual render limits before creating any poster.
    """
    try:
        content = raw_csv.decode("utf-8-sig")
        if "\x00" in content:
            raise ValueError("CSV 包含 NUL 字符")
        reader = csv.DictReader(io.StringIO(content, newline=""), strict=True)
        columns = reader.fieldnames
        if (
            not columns
            or len(columns) != len(set(columns))
            or not {"sku_id", "product_image", "title", "price"}.issubset(columns)
        ):
            raise ValueError("清单缺少必填列或表头存在重复列")
        records = list(reader)
        if not 1 <= len(records) <= MAX_ROWS:
            raise ValueError("清单必须包含 1–100 个 SKU")
        target_columns = [*columns, *(col for col in LAYOUT_FIELDS if col not in columns)]
        output = io.StringIO(newline="")
        writer = csv.DictWriter(output, fieldnames=target_columns, lineterminator="\r\n")
        writer.writeheader()
        for index, row in enumerate(records, start=2):
            if None in row or any(not isinstance(value, str) for value in row.values()):
                raise ValueError(f"第 {index} 行与表头列数不符")
            # A source CSV may already have per-SKU template coordinates. An
            # explicit 'use saved template' operation replaces only those
            # layout fields, leaving business facts byte-equivalent as values.
            result = dict(row)
            for name in LAYOUT_FIELDS:
                result[name] = (
                    json.dumps(
                        template[name], sort_keys=True, separators=(",", ":"), ensure_ascii=False
                    )
                    if name == "layer_layout"
                    else template[name]
                )
            writer.writerow(result)
        return output.getvalue().encode("utf-8-sig")
    except (UnicodeDecodeError, csv.Error, ValueError) as exc:
        raise ValueError(f"无法应用已保存的版式模板：{exc}") from exc
