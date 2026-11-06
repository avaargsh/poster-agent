"""Optional, bounded raster of the exact server-rendered approved SVG.

The pixels and their hashes are local, untrusted execution outputs, NOT OCR
attestation or publication approval. Input must come only from render_svg().
"""

from __future__ import annotations

import hashlib
import re
import warnings
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from io import BytesIO
from xml.etree import ElementTree

from PIL import Image, UnidentifiedImageError

from .models import MAX_ASSET_PIXELS, PAGE_MAX_SIZE


class RasterUnavailable(RuntimeError):
    """Optional raster engine is not installed or cannot be loaded."""


class RasterInvalid(RuntimeError):
    """Raster execution failed or produced invalid, oversized image data."""


@dataclass(frozen=True)
class RasterArtifact:
    png: bytes
    svg_sha256: str
    png_sha256: str
    rasterizer: str
    width: int
    height: int


_MAX_PNG_BYTES = 24 * 1024 * 1024
_SVG_NS = "{http://www.w3.org/2000/svg}svg"


def _canvas(svg: str) -> tuple[int, int]:
    """Only accepts integer page sizes emitted by the constrained renderer."""
    try:
        root = ElementTree.fromstring(svg)
        if root.tag != _SVG_NS:
            raise RasterInvalid("unexpected SVG root")
        raw_width, raw_height = root.attrib["width"], root.attrib["height"]
        if re.fullmatch(r"[0-9]+", raw_width) is None:
            raise RasterInvalid("invalid SVG width")
        if re.fullmatch(r"[0-9]+", raw_height) is None:
            raise RasterInvalid("invalid SVG height")
        width, height = int(raw_width), int(raw_height)
    except (ElementTree.ParseError, KeyError, ValueError, TypeError):
        raise RasterInvalid("invalid approved SVG canvas") from None
    if (
        width < 64
        or height < 64
        or width > PAGE_MAX_SIZE
        or height > PAGE_MAX_SIZE
        or width * height > MAX_ASSET_PIXELS
    ):
        raise RasterInvalid("approved SVG canvas exceeds raster limits")
    return width, height


def _convert_svg_to_png(svg_bytes: bytes, width: int, height: int) -> tuple[bytes, str]:
    """Opt-in, local CairoSVG engine; no default dependency or auto-install."""
    try:
        import cairosvg

        installed_version = version("CairoSVG")
    except (ImportError, OSError, PackageNotFoundError):
        raise RasterUnavailable("optional CairoSVG raster engine unavailable") from None

    try:
        raw = cairosvg.svg2png(
            bytestring=svg_bytes, output_width=width, output_height=height
        )
    except Exception:
        raise RasterInvalid("approved SVG rasterization failed") from None
    return raw, f"cairosvg/{installed_version}"


def rasterize_svg(svg: str) -> RasterArtifact:
    """Produce bounded PNG bytes and content hashes from server-rendered SVG."""
    if not isinstance(svg, str):
        raise RasterInvalid("approved SVG must be text")
    width, height = _canvas(svg)
    svg_bytes = svg.encode("utf-8")
    raw, rasterizer = _convert_svg_to_png(svg_bytes, width, height)
    if not isinstance(raw, bytes) or not raw or len(raw) > _MAX_PNG_BYTES:
        raise RasterInvalid("raster payload empty or exceeds size limit")
    if not isinstance(rasterizer, str) or not rasterizer:
        raise RasterInvalid("raster engine provenance missing")

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(raw)) as image:
                if image.format != "PNG" or image.size != (width, height):
                    raise RasterInvalid("raster format or dimensions mismatch")
                image.load()
    except RasterInvalid:
        raise
    except (
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
        UnidentifiedImageError,
        OSError,
        SyntaxError,
        ValueError,
    ):
        raise RasterInvalid("raster output is not a valid PNG") from None

    return RasterArtifact(
        png=raw,
        svg_sha256=hashlib.sha256(svg_bytes).hexdigest(),
        png_sha256=hashlib.sha256(raw).hexdigest(),
        rasterizer=rasterizer,
        width=width,
        height=height,
    )
