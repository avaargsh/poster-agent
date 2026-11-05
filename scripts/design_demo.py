"""Generate an offline design sample without any model or network call.

Usage:
    python -m scripts.design_demo --output-dir <new-directory>

The command refuses to overwrite an existing directory and writes a document
JSON, a self-contained SVG, the programmatic product asset, and a manifest with
hashes binding those files.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from typing import Sequence

from PIL import Image, ImageDraw

from app.designs import (
    RENDERER_VERSION,
    AssetRef,
    DesignDocument,
    create_template,
    render_svg,
)

DEMO_ASSET_ID = "demo-product"
DEMO_TITLE = "AI 画布离线演示"
DEMO_PRICE = Decimal("199.90")


def _programmatic_product_png() -> bytes:
    """Draw a deterministic, publishable sample product with Pillow."""

    image = Image.new("RGBA", (480, 640), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle(
        (172, 36, 308, 560), radius=48, fill="#e8f8ff", outline="#9cb6ca", width=8
    )
    draw.rounded_rectangle((204, 88, 276, 116), radius=12, fill="#2295d9")
    draw.rounded_rectangle((212, 296, 268, 304), radius=4, fill="#20a3d7")
    draw.rounded_rectangle((214, 520, 266, 610), radius=22, fill="#adcbdc")
    draw.ellipse((198, 150, 282, 234), outline="#5fc6f2", width=6)

    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _file_record(path: Path) -> dict[str, object]:
    content = path.read_bytes()
    return {
        "path": path.name,
        "sha256": hashlib.sha256(content).hexdigest(),
        "byte_size": len(content),
    }


def _write_json(path: Path, value: object) -> None:
    payload = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    path.write_text(payload, encoding="utf-8")


def _build_manifest(
    document: DesignDocument,
    document_path: Path,
    svg_path: Path,
    product_path: Path,
) -> dict[str, object]:
    return {
        "schema_version": document.schema_version,
        "renderer_version": RENDERER_VERSION,
        "document_id": document.document_id,
        "revision": document.revision,
        "template_id": document.source.template_id,
        "offline": True,
        "model_used": False,
        "files": {
            "document": _file_record(document_path),
            "svg": _file_record(svg_path),
            "product": _file_record(product_path),
        },
        "assets": [
            {
                "asset_id": ref.asset_id,
                "sha256": ref.sha256,
                "media_type": ref.media_type,
                "byte_size": ref.byte_size,
            }
            for ref in document.asset_refs
        ],
        "fonts": [
            {"family": font.family, "weight": font.weight, "source": font.source}
            for font in document.font_refs
        ],
        "limitations": [
            "A1 renders deterministic SVG only; PNG export and browser fidelity are not verified.",
            "Line coordinates are explicit and fixed; font metrics are not measured in A1.",
            "Template creation records asset ids; hashes are bound by rendering and the demo manifest "
            "until the A2 asset repository exists.",
        ],
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Write an offline design document, SVG and manifest to a new directory."
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        help="New directory to create. Existing directories are never overwritten.",
    )
    args = parser.parse_args(argv)

    output_dir = Path(args.output_dir)
    if output_dir.exists():
        parser.error(f"output directory already exists, refusing to overwrite: {output_dir}")
    try:
        output_dir.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        parser.error(f"output directory already exists, refusing to overwrite: {output_dir}")

    product_bytes = _programmatic_product_png()
    document = create_template(
        title=DEMO_TITLE,
        price=DEMO_PRICE,
        product_asset_id=DEMO_ASSET_ID,
    )
    # The demo binds the content hash before writing the snapshot.  Templates
    # only receive an id; a later A2 asset repository binds hashes at upload.
    document = document.model_copy(
        update={
            "asset_refs": [
                AssetRef(
                    asset_id=DEMO_ASSET_ID,
                    sha256=hashlib.sha256(product_bytes).hexdigest(),
                    media_type="image/png",
                    byte_size=len(product_bytes),
                )
            ]
        }
    )
    svg = render_svg(document, assets={DEMO_ASSET_ID: product_bytes})

    product_path = output_dir / "product.png"
    document_path = output_dir / "document.json"
    svg_path = output_dir / "design.svg"
    manifest_path = output_dir / "manifest.json"

    product_path.write_bytes(product_bytes)
    _write_json(document_path, document.model_dump(mode="json"))
    svg_path.write_text(svg, encoding="utf-8")
    _write_json(
        manifest_path,
        _build_manifest(document, document_path, svg_path, product_path),
    )

    print(f"Wrote offline design demo to {output_dir}")
    print(f"  document: {document_path.name}")
    print(f"  svg:      {svg_path.name}")
    print(f"  manifest: {manifest_path.name}")
    print("No model or network call was made.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
