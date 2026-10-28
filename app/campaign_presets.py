"""Deterministic, non-destructive campaign copy presets for bulk poster manifests.

Templates only fill absent style/subtitle/CTA. Product facts remain authoritative.
This endpoint has no filesystem, model, staging or paid side effects.
"""

from __future__ import annotations

import csv
import io
import re

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import ValidationError

from .schemas import PosterSpec

router = APIRouter(prefix="/api/campaign-presets", tags=["campaign-presets"])

MAX_CSV_BYTES = 1024 * 1024
_REQUIRED = frozenset({"sku_id", "product_image", "title", "price"})
_OPTIONAL = frozenset(
    {"original_price", "subtitle", "cta", "style", "logo_image", "product_cutout_image"}
)
_ALLOWED = _REQUIRED | _OPTIONAL
_SKU = re.compile(r"^[A-Za-z0-9_-]{2,64}$")
_FILLABLE = ("subtitle", "cta", "style")

# Reuse existing deterministic local-background styles; these are copy presets,
# NOT configurable layout templates or AI generation instructions.
_PRESETS = (
    {
        "id": "tech",
        "name": "数码新品",
        "style": "蓝色科技风",
        "subtitle": "新品上架 · 了解详情",
        "cta": "查看详情",
    },
    {
        "id": "beauty",
        "name": "礼品甄选",
        "style": "粉色浪漫",
        "subtitle": "精选好物 · 用心呈现",
        "cta": "了解更多",
    },
    {
        "id": "nature",
        "name": "自然生活",
        "style": "绿色自然",
        "subtitle": "发现生活灵感",
        "cta": "查看商品",
    },
    {
        "id": "flash-sale",
        "name": "限时活动",
        "style": "橙色暖阳",
        "subtitle": "活动进行中 · 详情以页面为准",
        "cta": "查看活动",
    },
    {
        "id": "fashion",
        "name": "创意上新",
        "style": "紫色梦幻",
        "subtitle": "新意呈现 · 随心挑选",
        "cta": "查看新品",
    },
)
_BY_ID = {preset["id"]: preset for preset in _PRESETS}


@router.get("")
def list_campaign_presets():
    return {"items": list(_PRESETS), "fills_only": list(_FILLABLE)}


def _safe_manifest(blob: bytes, template: dict) -> tuple[str, int]:
    try:
        data = blob.decode("utf-8-sig")
        if "\x00" in data:
            raise ValueError("CSV 中包含 NUL 字符")
        source = csv.DictReader(io.StringIO(data, newline=""), strict=True)
        columns = source.fieldnames
        if not columns or not _REQUIRED.issubset(columns):
            raise ValueError("CSV 必须包含 sku_id、product_image、title、price")
        if len(columns) != len(set(columns)) or set(columns) - _ALLOWED:
            raise ValueError("CSV 含重复或不支持的列")
        entries = list(source)
        if not 1 <= len(entries) <= 100:
            raise ValueError("每次只支持 1–100 个 SKU")
        seen: set[str] = set()
        output_columns = [*columns, *(key for key in _FILLABLE if key not in columns)]
        output = io.StringIO(newline="")
        writer = csv.DictWriter(output, fieldnames=output_columns, lineterminator="\r\n")
        writer.writeheader()
        changed_rows = 0
        for index, row in enumerate(entries, start=2):
            if None in row or any(not isinstance(value, str) for value in row.values()):
                raise ValueError(f"第 {index} 行列数与表头不一致")
            # Export to a spreadsheet is a trust boundary. No formula-bearing
            # source text is echoed into a downloadable CSV.
            if any(value.lstrip().startswith(("=", "+", "-", "@")) for value in row.values()):
                raise ValueError(f"第 {index} 行存在潜在 CSV 公式")
            sku = row["sku_id"].strip()
            if not _SKU.fullmatch(sku) or sku in seen:
                raise ValueError(f"第 {index} 行 SKU 编号无效或重复")
            seen.add(sku)
            if not row["product_image"].strip():
                raise ValueError(f"第 {index} 行缺少商品图片文件名")
            result = dict(row)
            filled = False
            for key in _FILLABLE:
                if not (result.get(key) or "").strip():
                    result[key] = template[key]
                    filled = True
            # Respect all operator-entered facts and validate the resulting
            # spec against the same schema used for single-poster creation.
            try:
                PosterSpec.model_validate(
                    {
                        "title": result["title"],
                        "price": result["price"],
                        "original_price": (result.get("original_price") or "").strip() or None,
                        "subtitle": result["subtitle"],
                        "cta": result["cta"],
                        "style": result["style"],
                        "background_mode": "local",
                    }
                )
            except ValidationError as exc:
                raise ValueError(f"第 {index} 行价格或文案不符合海报规格") from exc
            writer.writerow(result)
            changed_rows += int(filled)
        return output.getvalue(), changed_rows
    except (UnicodeDecodeError, csv.Error, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"活动清单无效：{exc}") from exc


@router.post("/apply-manifest")
async def apply_campaign_preset(
    manifest: UploadFile = File(...),
    template_id: str = Form(...),
):
    template = _BY_ID.get(template_id)
    if template is None:
        raise HTTPException(status_code=422, detail="未知的活动文案预设")
    name = manifest.filename or ""
    if (
        not name.lower().endswith(".csv")
        or "/" in name
        or "\\" in name
        or any(ord(ch) < 32 for ch in name)
    ):
        raise HTTPException(status_code=422, detail="请提供安全命名的 CSV 文件")
    payload = await manifest.read(MAX_CSV_BYTES + 1)
    if len(payload) > MAX_CSV_BYTES:
        raise HTTPException(status_code=413, detail="CSV 超过 1 MiB 限制")
    transformed, filled_count = _safe_manifest(payload, template)
    return Response(
        content="\ufeff" + transformed,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": 'attachment; filename="campaign-manifest.csv"',
            "X-Template-Filled-Rows": str(filled_count),
            "Cache-Control": "no-store",
        },
    )
