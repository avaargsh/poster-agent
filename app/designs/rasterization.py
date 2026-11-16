"""Optional, bounded raster of the exact server-rendered approved SVG.

The pixels and their hashes are local, untrusted execution outputs, NOT OCR
attestation or publication approval. Input must come only from render_svg().
"""

from __future__ import annotations

import ctypes
import hashlib
import os
import re
import shutil
import subprocess
import sys
import warnings
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from io import BytesIO
from xml.etree import ElementTree

from PIL import Image, UnidentifiedImageError

from .models import MAX_ASSET_PIXELS, PAGE_MAX_SIZE


class RasterUnavailable(RuntimeError):
    """Optional raster engine is not installed or cannot be loaded."""


class RasterFontUnavailable(RasterUnavailable):
    """The declared fixed typefaces cannot be resolved by the raster engine."""


def _register_process_fonts(paths: list[str]) -> None:
    # Homebrew Cairo can use CoreText, which does not see Fontconfig's
    # process-local font directory. Register the selected faces in this process.
    if sys.platform != "darwin":
        return
    try:
        foundation = ctypes.CDLL(
            "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
        )
        text = ctypes.CDLL("/System/Library/Frameworks/CoreText.framework/CoreText")
        foundation.CFURLCreateFromFileSystemRepresentation.restype = ctypes.c_void_p
        foundation.CFURLCreateFromFileSystemRepresentation.argtypes = [
            ctypes.c_void_p,
            ctypes.c_char_p,
            ctypes.c_long,
            ctypes.c_bool,
        ]
        foundation.CFRelease.argtypes = [ctypes.c_void_p]
        foundation.CFErrorGetCode.restype = ctypes.c_long
        foundation.CFErrorGetCode.argtypes = [ctypes.c_void_p]
        text.CTFontManagerRegisterFontsForURL.restype = ctypes.c_bool
        text.CTFontManagerRegisterFontsForURL.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_void_p),
        ]
        for path in paths:
            raw = os.fsencode(path)
            url = foundation.CFURLCreateFromFileSystemRepresentation(None, raw, len(raw), False)
            if not url:
                raise RasterFontUnavailable("font URL unavailable")
            error = ctypes.c_void_p()
            try:
                # kCTFontManagerScopeProcess = 1, no system font installation.
                registered = text.CTFontManagerRegisterFontsForURL(url, 1, ctypes.byref(error))
                # kCTFontManagerErrorAlreadyRegistered = 105 is idempotent success.
                if not registered and (not error.value or foundation.CFErrorGetCode(error) != 105):
                    raise RasterFontUnavailable("process-local font registration failed")
            finally:
                foundation.CFRelease(url)
                if error.value:
                    foundation.CFRelease(error)
    except (OSError, AttributeError):
        raise RasterFontUnavailable("native font registration unavailable") from None


def _require_fixed_fonts(svg_bytes: bytes) -> None:
    root = ElementTree.fromstring(svg_bytes)
    fonts = {
        (
            node.attrib["font-family"],
            "Bold" if node.attrib.get("font-weight") == "700" else "Regular",
        )
        for node in root.iter("{http://www.w3.org/2000/svg}text")
    }
    if not fonts:
        return
    executable = shutil.which("fc-match")
    if executable is None:
        raise RasterFontUnavailable("fontconfig tools unavailable")
    paths = []
    for family, style in sorted(fonts):
        try:
            result = subprocess.run(
                [
                    executable,
                    "--format",
                    "%{family}\n%{style}\n%{file}\n",
                    f"{family}:style={style}",
                ],
                capture_output=True,
                text=True,
                timeout=3,
                check=True,
            )
        except (OSError, subprocess.SubprocessError):
            raise RasterFontUnavailable("fixed font lookup unavailable") from None
        lines = result.stdout.splitlines()
        if len(lines) != 3 or family not in lines[0].split(",") or style not in lines[1].split(","):
            raise RasterFontUnavailable("declared raster font family and style unavailable")
        paths.append(lines[2])
    _register_process_fonts(paths)


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

    _require_fixed_fonts(svg_bytes)
    try:
        raw = cairosvg.svg2png(bytestring=svg_bytes, output_width=width, output_height=height)
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
