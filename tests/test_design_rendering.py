"""Safety and determinism tests for constrained SVG rendering and the offline demo."""

import hashlib
import json
from decimal import Decimal
from io import BytesIO
from xml.etree import ElementTree

import pytest
from PIL import Image, PngImagePlugin
from pydantic import ValidationError

from app.designs import (
    AssetValidationError,
    DesignDocument,
    MissingAssetError,
    create_template,
    render_svg,
)
from app.designs.models import MAX_ASSET_BYTES
from scripts import design_demo

SVG_NS = "{http://www.w3.org/2000/svg}"
PRODUCT_ASSET_ID = "product"


def png_bytes(size: tuple[int, int] = (240, 320)) -> bytes:
    buffer = BytesIO()
    Image.new("RGBA", size, (24, 120, 180, 255)).save(buffer, format="PNG")
    return buffer.getvalue()


def make_document(title: str = "夏日 & <清凉> 特惠") -> DesignDocument:
    return create_template(
        title=title,
        price=Decimal("199.90"),
        product_asset_id=PRODUCT_ASSET_ID,
    )


def test_chinese_and_html_metacharacters_are_escaped_in_well_formed_svg():
    svg = render_svg(make_document(), assets={PRODUCT_ASSET_ID: png_bytes()})

    ElementTree.fromstring(svg)
    assert svg.startswith("<svg ")
    assert "夏日" in svg
    assert "&amp;" in svg
    assert "&lt;清凉&gt;" in svg
    assert "¥199.90" in svg
    assert "<script" not in svg.lower()
    assert "<!doctype" not in svg.lower()


def test_text_injection_cannot_break_out_of_markup():
    title = '"/><script>alert(1)</script>'
    document = make_document(title=title)
    svg = render_svg(document, assets={PRODUCT_ASSET_ID: png_bytes()})

    root = ElementTree.fromstring(svg)
    assert "<script" not in svg.lower()
    assert not list(root.iter(f"{SVG_NS}script"))
    title_group = next(
        group for group in root.iter(f"{SVG_NS}g") if group.get("data-role") == "title"
    )
    rendered_title = "".join(span.text or "" for span in title_group.iter(f"{SVG_NS}tspan"))
    assert rendered_title == title


def test_title_price_and_product_are_independent_layers():
    svg = render_svg(make_document(), assets={PRODUCT_ASSET_ID: png_bytes()})
    root = ElementTree.fromstring(svg)
    groups = {
        group.get("data-role"): group for group in root.iter(f"{SVG_NS}g") if group.get("data-role")
    }

    assert {"title", "price", "product"}.issubset(groups)
    assert len({groups[role].get("data-node-id") for role in ("title", "price", "product")}) == 3
    assert groups["title"].find(f"{SVG_NS}text") is not None
    assert groups["price"].find(f"{SVG_NS}text") is not None
    assert groups["product"].find(f"{SVG_NS}image") is not None


def test_svg_only_uses_inline_validated_assets():
    svg = render_svg(make_document(), assets={PRODUCT_ASSET_ID: png_bytes()})
    root = ElementTree.fromstring(svg)
    images = list(root.iter(f"{SVG_NS}image"))

    assert len(images) == 1
    href = images[0].get("href")
    assert href is not None
    assert href.startswith("data:image/png;base64,")
    assert "xlink:href" not in svg
    assert "url(" not in svg


def test_missing_product_asset_fails_and_does_not_draw_substitute():
    with pytest.raises(MissingAssetError, match=PRODUCT_ASSET_ID):
        render_svg(make_document(), assets={})


def test_hidden_image_still_requires_declared_asset_bytes():
    payload = make_document().model_dump(mode="json")
    product = next(node for node in payload["nodes"] if node["role"] == "product")
    product["visible"] = False
    hidden = DesignDocument.model_validate(payload)

    with pytest.raises(MissingAssetError, match=PRODUCT_ASSET_ID):
        render_svg(hidden, assets={})


@pytest.mark.parametrize(
    "payload",
    [
        b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>",
        b"<!DOCTYPE html><html><body>not an image</body></html>",
        b"not an image",
        b"",
        b"GIF89a",
        b"\x89PNG\r\n\x1a\n" + b"0" * 64,
    ],
)
def test_malicious_or_malformed_asset_bytes_rejected(payload):
    with pytest.raises(AssetValidationError):
        render_svg(make_document(), assets={PRODUCT_ASSET_ID: payload})


def test_asset_metadata_is_stripped_when_reencoding():
    buffer = BytesIO()
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text("comment", "<script>alert(1)</script>")
    Image.new("RGBA", (40, 40), (24, 120, 180, 255)).save(buffer, format="PNG", pnginfo=metadata)

    svg = render_svg(make_document(), assets={PRODUCT_ASSET_ID: buffer.getvalue()})

    assert "<script" not in svg.lower()
    assert "alert(1)" not in svg


def test_external_asset_reference_is_rejected_by_the_model():
    for asset_id in ("https://example.com/a.png", "/tmp/a.png", "../a.png"):
        with pytest.raises(ValidationError):
            create_template(
                title="商品",
                price=Decimal("199.90"),
                product_asset_id=asset_id,
            )


def test_oversized_asset_dimensions_and_bytes_rejected():
    with pytest.raises(AssetValidationError):
        render_svg(make_document(), assets={PRODUCT_ASSET_ID: png_bytes((5000, 2))})

    oversized = b"\x89PNG\r\n\x1a\n" + b"0" * (MAX_ASSET_BYTES + 1)
    with pytest.raises(AssetValidationError):
        render_svg(make_document(), assets={PRODUCT_ASSET_ID: oversized})


def test_declared_asset_hash_is_verified():
    product = png_bytes()
    payload = make_document().model_dump(mode="json")
    payload["asset_refs"][0].update(
        {
            "sha256": "0" * 64,
            "media_type": "image/png",
            "byte_size": len(product),
        }
    )
    mismatched = DesignDocument.model_validate(payload)

    with pytest.raises(AssetValidationError, match="hash"):
        render_svg(mismatched, assets={PRODUCT_ASSET_ID: product})

    payload["asset_refs"][0]["sha256"] = hashlib.sha256(product).hexdigest()
    matched = DesignDocument.model_validate(payload)
    assert render_svg(matched, assets={PRODUCT_ASSET_ID: product})


def test_same_document_renders_identically_after_json_roundtrip():
    document = make_document()
    product = png_bytes()
    restored = DesignDocument.model_validate_json(document.model_dump_json())

    first = render_svg(document, assets={PRODUCT_ASSET_ID: product})
    second = render_svg(restored, assets={PRODUCT_ASSET_ID: product})
    assert first == second


def test_offline_demo_writes_document_svg_and_manifest(tmp_path):
    output_dir = tmp_path / "demo"
    assert design_demo.main(["--output-dir", str(output_dir)]) == 0

    document_path = output_dir / "document.json"
    svg_path = output_dir / "design.svg"
    manifest_path = output_dir / "manifest.json"
    product_path = output_dir / "product.png"
    assert all(path.is_file() for path in (document_path, svg_path, manifest_path, product_path))

    document = DesignDocument.model_validate_json(document_path.read_text(encoding="utf-8"))
    assert document.source.kind == "template"
    assert document.facts.price == Decimal("199.90")
    product_sha256 = hashlib.sha256(product_path.read_bytes()).hexdigest()
    assert document.asset_refs[0].sha256 == product_sha256
    assert document.asset_refs[0].media_type == "image/png"

    svg = svg_path.read_text(encoding="utf-8")
    ElementTree.fromstring(svg)
    assert "¥199.90" in svg

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["offline"] is True
    assert manifest["model_used"] is False
    assert manifest["renderer_version"] == design_demo.RENDERER_VERSION
    assert manifest["document_id"] == document.document_id
    assert (
        manifest["files"]["document"]["sha256"]
        == hashlib.sha256(document_path.read_bytes()).hexdigest()
    )
    assert manifest["files"]["svg"]["sha256"] == hashlib.sha256(svg_path.read_bytes()).hexdigest()
    assert (
        manifest["files"]["product"]["sha256"]
        == hashlib.sha256(product_path.read_bytes()).hexdigest()
    )
    assert manifest["assets"][0]["asset_id"] == "demo-product"
    assert manifest["assets"][0]["sha256"] == hashlib.sha256(product_path.read_bytes()).hexdigest()


def test_offline_demo_refuses_to_overwrite_existing_directory(tmp_path):
    output_dir = tmp_path / "existing"
    output_dir.mkdir()
    sentinel = output_dir / "keep.txt"
    sentinel.write_text("keep", encoding="utf-8")

    with pytest.raises(SystemExit) as exc_info:
        design_demo.main(["--output-dir", str(output_dir)])
    assert exc_info.value.code == 2
    assert sentinel.read_text(encoding="utf-8") == "keep"
    assert sorted(path.name for path in output_dir.iterdir()) == ["keep.txt"]
