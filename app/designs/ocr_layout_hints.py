"""Read-only geometry hints against the exact approved DesignDocument text nodes.

A node rectangle is an authoring envelope, NOT the measured SVG glyph
extent or a calibrated OCR target zone. Even full overlap cannot grant
quality verification, repair authority or a release permit.
"""

from __future__ import annotations

import hashlib
import json

from .models import DesignDocument, TextNode

_HINT_SCHEMA_VERSION = 1
_FIELDS = ("title", "price", "cta")


def _canonical_sha256(value: object) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _in_canvas(box: object, *, width: int, height: int) -> bool:
    if not isinstance(box, list) or len(box) != 4 or any(type(v) is not int for v in box):
        return False
    x1, y1, x2, y2 = box
    return 0 <= x1 < x2 <= width and 0 <= y1 < y2 <= height


def _safe_node_rect(node: TextNode, *, width: int, height: int) -> list[float] | None:
    if node.rotation != 0 or node.opacity <= 0:
        return None
    left, top = node.x, node.y
    right, bottom = left + node.width, top + node.height
    if not (0 <= left < right <= width and 0 <= top < bottom <= height):
        return None
    # TextLine coordinates are absolute SVG baselines. If they escape the
    # authoring envelope, this rectangular hint would be misleading.
    if any(not (left <= line.x <= right and top <= line.y <= bottom) for line in node.lines):
        return None
    return [left, top, right, bottom]


def _intersection_fraction(box: list[int], rect: list[float]) -> float:
    left = max(box[0], rect[0])
    top = max(box[1], rect[1])
    right = min(box[2], rect[2])
    bottom = min(box[3], rect[3])
    area = max(0, right - left) * max(0, bottom - top)
    total = (box[2] - box[0]) * (box[3] - box[1])
    return round(area / total, 6)


def assess_design_node_hints(
    document: DesignDocument,
    spatial_checks: list[dict],
    *,
    width: int,
    height: int,
    spatial_source_sha256: str,
) -> dict:
    """Compare OCR rectangles with authored node envelopes, with no authority.

    The caller must separately bind the document digest, approval and raster
    to the current journal source. This pure helper cannot do those checks.
    """
    canvas_matches = (
        document.page.width == width
        and document.page.height == height
        and type(width) is int
        and type(height) is int
    )
    by_field = {check["field"]: check for check in spatial_checks}
    findings: list[dict] = []
    for field in _FIELDS:
        observed = by_field.get(field, {})
        box = observed.get("bbox")
        nodes = [
            node
            for node in document.nodes
            if isinstance(node, TextNode) and node.role == field and node.visible
        ]
        status = "NO_VALID_OCR_BOX"
        node_id = None
        node_rect = None
        fraction = None
        if not canvas_matches:
            status = "UNSUPPORTED_NODE_GEOMETRY"
        elif len(nodes) != 1:
            status = "NO_VISIBLE_TEXT_NODE"
        else:
            node = nodes[0]
            node_id = node.node_id
            node_rect = _safe_node_rect(node, width=width, height=height)
            if node_rect is None:
                status = "UNSUPPORTED_NODE_GEOMETRY"
            elif observed.get("status") != "BOX_PRESENT_UNCALIBRATED" or not _in_canvas(
                box, width=width, height=height
            ):
                status = "NO_VALID_OCR_BOX"
            else:
                fraction = _intersection_fraction(box, node_rect)
                status = (
                    "OVERLAP_UNCALIBRATED" if fraction > 0 else "OUTSIDE_DESIGN_NODE"
                )
        findings.append(
            {
                "field": field,
                "status": status,
                "node_id": node_id,
                "node_rect": node_rect,
                "ocr_bbox": box if _in_canvas(box, width=width, height=height) else None,
                "overlap_fraction": fraction,
            }
        )
    result_status = (
        "OVERLAP_ONLY_UNCALIBRATED"
        if all(item["status"] == "OVERLAP_UNCALIBRATED" for item in findings)
        else "BLOCKED"
    )
    fingerprint = _canonical_sha256(
        {
            "schema_version": _HINT_SCHEMA_VERSION,
            "spatial_source_sha256": spatial_source_sha256,
            "canvas": {"width": width, "height": height},
            "design_node_overlap_status": result_status,
            "design_node_overlap_checks": findings,
        }
    )
    return {
        "design_node_overlap_status": result_status,
        "design_node_overlap_checks": findings,
        "design_node_overlap_schema_version": _HINT_SCHEMA_VERSION,
        "design_node_overlap_fingerprint_sha256": fingerprint,
    }
