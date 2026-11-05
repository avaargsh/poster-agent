"""Constrained SVG rendering.

The renderer accepts only the fields modeled by ``DesignDocument``.  Text is
escaped, images are decoded, bounded, optionally hash-verified and re-encoded
as PNG data URIs.  No document field can inject markup, scripts, file paths or
network URLs into the SVG.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import warnings
from io import BytesIO
from typing import Iterable
from xml.sax.saxutils import escape

from PIL import Image, UnidentifiedImageError

from .models import (
    MAX_ASSET_BYTES,
    MAX_ASSET_DIMENSION,
    MAX_ASSET_PIXELS,
    AssetRef,
    DesignDocument,
    ImageNode,
    RectNode,
    TextNode,
)

ALLOWED_FORMATS = {
    "PNG": "image/png",
    "JPEG": "image/jpeg",
    "WEBP": "image/webp",
}


class DesignRenderError(ValueError):
    """Base class for deterministic, user-facing rendering failures."""


class MissingAssetError(DesignRenderError):
    """A visible image node references an asset whose bytes were not supplied."""


class AssetValidationError(DesignRenderError):
    """Asset bytes are malformed, unsafe, oversized or fail integrity checks."""


def _attribute(value: object) -> str:
    return escape(str(value), {'"': "&quot;"})


def _number(value: float) -> str:
    if value == int(value):
        return str(int(value))
    return repr(value)


def _png_data_uri(asset_id: str, raw: bytes, ref: AssetRef) -> str:
    if not raw:
        raise AssetValidationError(f"asset {asset_id} is empty")
    if len(raw) > MAX_ASSET_BYTES:
        raise AssetValidationError(f"asset {asset_id} exceeds {MAX_ASSET_BYTES} bytes ({len(raw)})")

    digest = hashlib.sha256(raw).hexdigest()
    if ref.sha256 is not None and not hmac.compare_digest(ref.sha256, digest):
        raise AssetValidationError(f"asset {asset_id} sha256 hash mismatch")
    if ref.byte_size is not None and ref.byte_size != len(raw):
        raise AssetValidationError(f"asset {asset_id} byte_size mismatch")

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(raw)) as image:
                media_type = ALLOWED_FORMATS.get(image.format or "")
                if media_type is None:
                    raise AssetValidationError(
                        f"asset {asset_id} format {image.format!r} is not allowed"
                    )
                if getattr(image, "is_animated", False) or getattr(image, "n_frames", 1) > 1:
                    raise AssetValidationError(f"asset {asset_id} must not be animated")
                width, height = image.size
                if width < 1 or height < 1:
                    raise AssetValidationError(f"asset {asset_id} has an invalid size")
                if width > MAX_ASSET_DIMENSION or height > MAX_ASSET_DIMENSION:
                    raise AssetValidationError(
                        f"asset {asset_id} dimensions {width}x{height} exceed {MAX_ASSET_DIMENSION}"
                    )
                if width * height > MAX_ASSET_PIXELS:
                    raise AssetValidationError(
                        f"asset {asset_id} has too many pixels ({width * height})"
                    )
                if ref.media_type is not None and ref.media_type != media_type:
                    raise AssetValidationError(
                        f"asset {asset_id} media_type does not match decoded format"
                    )
                image.load()
                normalized = image.convert("RGBA")
    except AssetValidationError:
        raise
    except (Image.DecompressionBombError, UnidentifiedImageError, OSError, SyntaxError, ValueError):
        raise AssetValidationError(
            f"asset {asset_id} is not a valid bounded raster image"
        ) from None

    buffer = BytesIO()
    normalized.save(buffer, format="PNG", optimize=False)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def _node_attributes(node) -> str:
    parts = [
        f'data-node-id="{_attribute(node.node_id)}"',
        f'data-role="{_attribute(node.role)}"',
        f'data-node-type="{_attribute(node.node_type)}"',
    ]
    if node.opacity < 1:
        parts.append(f'opacity="{_number(node.opacity)}"')
    if node.rotation:
        center_x = node.x + node.width / 2
        center_y = node.y + node.height / 2
        parts.append(
            f'transform="rotate({_number(node.rotation)} {_number(center_x)} {_number(center_y)})"'
        )
    return " ".join(parts)


def _rect_markup(node: RectNode) -> str:
    attributes = [
        f'x="{_number(node.x)}"',
        f'y="{_number(node.y)}"',
        f'width="{_number(node.width)}"',
        f'height="{_number(node.height)}"',
        f'fill="{_attribute(node.fill)}"',
    ]
    if node.corner_radius:
        attributes.append(f'rx="{_number(node.corner_radius)}"')
    if node.stroke is not None:
        attributes.extend(
            (
                f'stroke="{_attribute(node.stroke)}"',
                f'stroke-width="{_number(node.stroke_width)}"',
            )
        )
    return f"    <rect {' '.join(attributes)}/>"


def _image_markup(node: ImageNode, href: str) -> str:
    preserve = "xMidYMid meet" if node.fit == "contain" else "xMidYMid slice"
    return (
        "    <image "
        f'x="{_number(node.x)}" y="{_number(node.y)}" '
        f'width="{_number(node.width)}" height="{_number(node.height)}" '
        f'preserveAspectRatio="{preserve}" href="{_attribute(href)}"/>'
    )


def _text_markup(node: TextNode) -> str:
    first_line = node.lines[0]
    attributes = [
        f'x="{_number(first_line.x)}"',
        f'y="{_number(first_line.y)}"',
        f'font-family="{_attribute(node.font_family)}"',
        f'font-size="{_number(node.font_size)}"',
        f'font-weight="{node.font_weight}"',
        f'fill="{_attribute(node.color)}"',
        f'text-anchor="{_attribute(node.text_anchor)}"',
        'xml:space="preserve"',
    ]
    spans = [
        f'      <tspan x="{_number(line.x)}" y="{_number(line.y)}">{escape(line.text)}</tspan>'
        for line in node.lines
    ]
    return "\n".join(
        (
            f"    <text {' '.join(attributes)}>",
            *spans,
            "    </text>",
        )
    )


def _visible_nodes(document: DesignDocument) -> Iterable:
    return (node for node in document.nodes if node.visible)


def render_svg(document: DesignDocument, *, assets: dict[str, bytes]) -> str:
    """Render a design document to a white-listed, self-contained SVG string."""

    refs = {ref.asset_id: ref for ref in document.asset_refs}
    image_hrefs: dict[str, str] = {}

    for node in document.nodes:
        if not isinstance(node, ImageNode):
            continue
        if node.asset_id in image_hrefs:
            continue
        if node.asset_id not in assets:
            raise MissingAssetError(
                f"missing asset bytes for image node {node.node_id}: {node.asset_id}"
            )
        raw = assets[node.asset_id]
        if not isinstance(raw, (bytes, bytearray, memoryview)):
            raise AssetValidationError(f"asset {node.asset_id} must be bytes")
        image_hrefs[node.asset_id] = _png_data_uri(node.asset_id, bytes(raw), refs[node.asset_id])

    lines = [
        (
            '<svg xmlns="http://www.w3.org/2000/svg" '
            f'width="{document.page.width}" height="{document.page.height}" '
            f'viewBox="0 0 {document.page.width} {document.page.height}">'
        ),
        (
            '  <rect data-page-background="true" x="0" y="0" '
            f'width="{document.page.width}" height="{document.page.height}" '
            f'fill="{_attribute(document.page.background)}"/>'
        ),
    ]

    for node in _visible_nodes(document):
        lines.append(f"  <g {_node_attributes(node)}>")
        if isinstance(node, RectNode):
            lines.append(_rect_markup(node))
        elif isinstance(node, ImageNode):
            lines.append(_image_markup(node, image_hrefs[node.asset_id]))
        else:
            lines.append(_text_markup(node))
        lines.append("  </g>")

    lines.append("</svg>")
    lines.append("")
    return "\n".join(lines)
