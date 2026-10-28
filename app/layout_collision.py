"""Rendered-glyph geometry checks for the two deterministic 1024px templates.

Works on real Pillow font bounding boxes and the same coordinates as the
compositor. No OCR, no predictions from a background image, no paid model.
This checks text/logo/CTA intersection only; visual human review still applies.
"""

from __future__ import annotations

from PIL import Image, ImageDraw

from .compositor import (
    SIZE,
    _contain,
    _measure_hero_layout,
    _measure_text_layout,
    get_font,
)
from .schemas import PosterSpec

LABELS = {
    "heading": "主标题/副标题",
    "price": "价格信息",
    "cta": "CTA 按钮",
    "logo": "Logo",
}
Box = tuple[int, int, int, int]


def _glyph(draw: ImageDraw.ImageDraw, text: str, x: int, y: int, font, *, stroke: int = 0) -> Box:
    return tuple(
        int(v)
        for v in draw.textbbox(
            (x, y),
            text,
            font=font,
            stroke_width=stroke,
        )
    )


def _rect(x1: int, y1: int, x2: int, y2: int) -> Box:
    return (x1, y1, x2, y2)


def _boxes(spec: PosterSpec, logo: Image.Image | None = None) -> list[dict]:
    """Measure exact composited foreground geometry once; no pixel render."""
    draw = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    ofs = spec.layer_layout
    items: list[dict] = []

    def add(group: str, name: str, box: Box) -> None:
        items.append({"group": group, "name": name, "rect": box})

    if spec.layout_id == "hero":
        layout = _measure_hero_layout(draw, spec)
        for i, line in enumerate(layout["title_lines"]):
            add(
                "heading",
                f"title_{i + 1}",
                _glyph(
                    draw,
                    line,
                    75 + ofs.title_dx,
                    637 + i * 64 + ofs.title_dy,
                    layout["title_font"],
                ),
            )
        if spec.subtitle:
            add(
                "heading",
                "subtitle",
                _glyph(
                    draw,
                    spec.subtitle,
                    76 + ofs.title_dx,
                    layout["subtitle_y"] + ofs.title_dy,
                    layout["subtitle_font"],
                ),
            )
        add(
            "price",
            "label",
            _glyph(
                draw,
                "活动价",
                76 + ofs.price_dx,
                801 + ofs.price_dy,
                get_font(27),
            ),
        )
        add(
            "price",
            "value",
            _glyph(
                draw,
                layout["price_value"],
                66 + ofs.price_dx,
                830 + ofs.price_dy,
                layout["price_font"],
                stroke=1,
            ),
        )
        if layout["original_value"] is not None:
            add(
                "price",
                "original",
                _glyph(
                    draw,
                    layout["original_value"],
                    80 + ofs.price_dx,
                    944 + ofs.price_dy,
                    layout["original_font"],
                ),
            )
        add(
            "cta",
            "button",
            _rect(
                660 + ofs.cta_dx,
                877 + ofs.cta_dy,
                959 + ofs.cta_dx,
                951 + ofs.cta_dy,
            ),
        )
        if logo is not None:
            fitted = _contain(logo, (200, 68))
            add(
                "logo",
                "image",
                _rect(
                    755 + ofs.logo_dx,
                    641 + ofs.logo_dy,
                    755 + ofs.logo_dx + fitted.width,
                    641 + ofs.logo_dy + fitted.height,
                ),
            )
    else:
        layout = _measure_text_layout(draw, spec)
        for i, line in enumerate(layout["title_lines"]):
            add(
                "heading",
                f"title_{i + 1}",
                _glyph(
                    draw,
                    line,
                    75 + ofs.title_dx,
                    layout["title_top"] + i * 86 + ofs.title_dy,
                    layout["title_font"],
                ),
            )
        if spec.subtitle:
            add(
                "heading",
                "subtitle",
                _glyph(
                    draw,
                    spec.subtitle,
                    75 + ofs.title_dx,
                    layout["subtitle_top"] + ofs.title_dy,
                    layout["subtitle_font"],
                ),
            )
        add(
            "price",
            "label",
            _glyph(
                draw,
                "限时到手价",
                75 + ofs.price_dx,
                610 + ofs.price_dy,
                get_font(31),
            ),
        )
        add(
            "price",
            "value",
            _glyph(
                draw,
                layout["price_value"],
                65 + ofs.price_dx,
                668 + ofs.price_dy,
                layout["price_font"],
                stroke=1,
            ),
        )
        if layout["original_value"] is not None:
            add(
                "price",
                "original",
                _glyph(
                    draw,
                    layout["original_value"],
                    78 + ofs.price_dx,
                    800 + ofs.price_dy,
                    layout["original_font"],
                ),
            )
        add(
            "cta",
            "button",
            _rect(
                76 + ofs.cta_dx,
                867 + ofs.cta_dy,
                410 + ofs.cta_dx,
                934 + ofs.cta_dy,
            ),
        )
        if logo is not None:
            fitted = _contain(logo, (260, 105))
            add(
                "logo",
                "image",
                _rect(
                    75 + ofs.logo_dx,
                    82 + ofs.logo_dy,
                    75 + ofs.logo_dx + fitted.width,
                    82 + ofs.logo_dy + fitted.height,
                ),
            )
    return items


def _intersection(a: Box, b: Box) -> Box | None:
    common = (max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3]))
    return common if common[0] < common[2] and common[1] < common[3] else None


def _issues(boxes: list[dict]) -> list[dict]:
    issues = []
    for item in boxes:
        x1, y1, x2, y2 = item["rect"]
        if min(x1, y1) < 0 or max(x2, y2) > SIZE:
            issues.append(
                {
                    "code": "LAYER_OUT_OF_BOUNDS",
                    "layers": [item["group"]],
                    "objects": [item["name"]],
                    "overlap_xyxy": list(item["rect"]),
                    "message": LABELS[item["group"]] + "超出方形画布边界",
                }
            )
    for pos, a in enumerate(boxes):
        for b in boxes[pos + 1 :]:
            # Within a deliberately grouped region, internal objects are
            # arranged by the existing fixed compositor. Check only distinct
            # movable layers for newly introduced cross-layer occlusion.
            if a["group"] == b["group"]:
                continue
            common = _intersection(a["rect"], b["rect"])
            if common is not None:
                issues.append(
                    {
                        "code": "LAYER_COLLISION",
                        "layers": [a["group"], b["group"]],
                        "objects": [a["name"], b["name"]],
                        "overlap_xyxy": list(common),
                        "message": LABELS[a["group"]]
                        + "与"
                        + LABELS[b["group"]]
                        + "发生图层碰撞，请调整位置",
                    }
                )
    return issues


def _report(boxes: list[dict]) -> dict:
    issues = _issues(boxes)
    group_rects = {}
    for item in boxes:
        group = item["group"]
        r = item["rect"]
        if group not in group_rects:
            group_rects[group] = list(r)
        else:
            old = group_rects[group]
            group_rects[group] = [
                min(old[0], r[0]),
                min(old[1], r[1]),
                max(old[2], r[2]),
                max(old[3], r[3]),
            ]
    return {
        "status": "PASS" if not issues else "FAIL",
        "issues": issues,
        "rects": group_rects,
    }


def inspect_square_layout(
    spec: PosterSpec,
    logo: Image.Image | None = None,
) -> dict:
    """Return materialized rectangles and auditable pairwise collisions."""
    return _report(_boxes(spec, logo))


def _shifted(
    boxes: list[dict], title_dy: int, price_dy: int, original_title_dy: int, original_price_dy: int
) -> list[dict]:
    result = []
    for item in boxes:
        delta = (
            title_dy - original_title_dy
            if item["group"] == "heading"
            else price_dy - original_price_dy
            if item["group"] == "price"
            else 0
        )
        x1, y1, x2, y2 = item["rect"]
        result.append({**item, "rect": (x1, y1 + delta, x2, y2 + delta)})
    return result


def repair_square_layout(
    spec: PosterSpec,
    logo: Image.Image | None = None,
) -> dict:
    """Find smallest safe heading/price Y adjustment; preserve all SKU facts.

    This algorithm intentionally never touches title, price, brand asset,
    product, CTA or horizontal positions. Unrepairable layouts are explicit.
    """
    boxes = _boxes(spec, logo)
    before = _report(boxes)
    original = spec.layer_layout.model_dump(mode="json")
    if before["status"] == "PASS":
        return {
            "status": "UNCHANGED",
            "before": before,
            "after": before,
            "proposed_layer_layout": original,
        }
    offsets = spec.layer_layout
    candidates = [(t, p) for t in range(-20, 21) for p in range(-8, 9)]
    candidates.sort(
        key=lambda pair: (
            abs(pair[0] - offsets.title_dy) + abs(pair[1] - offsets.price_dy),
            abs(pair[1] - offsets.price_dy),
            abs(pair[0] - offsets.title_dy),
            pair[0],
            pair[1],
        )
    )
    for title_dy, price_dy in candidates:
        changed = _shifted(boxes, title_dy, price_dy, offsets.title_dy, offsets.price_dy)
        after = _report(changed)
        if after["status"] == "PASS":
            return {
                "status": "REPAIRED",
                "before": before,
                "after": after,
                "proposed_layer_layout": {
                    **original,
                    "title_dy": title_dy,
                    "price_dy": price_dy,
                },
            }
    return {
        "status": "UNREPAIRABLE",
        "before": before,
        "after": None,
        "proposed_layer_layout": None,
    }
