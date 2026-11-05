"""Contract tests for the bounded design document model and deterministic template."""

import json
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.designs import (
    AssetRef,
    DesignDocument,
    ImageNode,
    create_template,
    format_price_text,
)
from app.designs.models import NODE_MAX_SIZE, PAGE_MAX_SIZE

PRODUCT_ASSET_ID = "product"


def make_document() -> DesignDocument:
    return create_template(
        title="夏日 & <清凉> 特惠",
        price=Decimal("199.90"),
        product_asset_id=PRODUCT_ASSET_ID,
    )


def document_payload() -> dict:
    return make_document().model_dump(mode="json")


def test_template_contract_has_one_page_stable_nodes_and_semantic_roles():
    document = make_document()

    assert document.schema_version == 1
    assert document.document_id.startswith("doc_")
    assert document.revision == 1
    assert (document.page.width, document.page.height) == (1024, 1024)
    assert document.facts.price == Decimal("199.90")
    assert document.facts.currency == "CNY"
    assert document.source.kind == "template"
    assert document.source.template_id == "square-promo-v1"

    node_ids = [node.node_id for node in document.nodes]
    assert len(node_ids) == len(set(node_ids))
    assert {"title", "price", "product", "accent"}.issubset(set(node_ids))

    roles = [node.role for node in document.nodes]
    assert roles.count("title") == 1
    assert roles.count("price") == 1
    assert roles.count("product") == 1

    product = next(node for node in document.nodes if node.role == "product")
    assert isinstance(product, ImageNode)
    assert product.asset_id == PRODUCT_ASSET_ID
    assert [ref.asset_id for ref in document.asset_refs] == [PRODUCT_ASSET_ID]
    assert document.asset_refs[0].sha256 is None


def test_json_roundtrip_preserves_semantics_and_price_string():
    document = make_document()
    raw = json.loads(document.model_dump_json())

    assert raw["schema_version"] == 1
    assert raw["facts"]["price"] == "199.90"
    assert raw["facts"]["currency"] == "CNY"
    assert raw["asset_refs"] == [
        {"asset_id": PRODUCT_ASSET_ID, "sha256": None, "media_type": None, "byte_size": None}
    ]

    restored = DesignDocument.model_validate_json(document.model_dump_json())
    assert restored == document
    assert [node.node_id for node in restored.nodes] == [node.node_id for node in document.nodes]
    assert restored.nodes == document.nodes


def test_template_title_uses_explicit_line_coordinates():
    document = create_template(
        title="夏日清凉特惠活动限时开启买一送一",
        price=Decimal("199.90"),
        product_asset_id=PRODUCT_ASSET_ID,
    )
    title = next(node for node in document.nodes if node.role == "title")
    assert len(title.lines) > 1
    for index, line in enumerate(title.lines):
        assert line.text
        assert isinstance(line.x, float)
        assert isinstance(line.y, float)
        assert title.y <= line.y <= title.y + title.height
        if index:
            assert line.y > title.lines[index - 1].y


def test_template_is_deterministic_and_changes_identity_with_inputs():
    first = make_document()
    second = make_document()
    changed = create_template(
        title="另一个标题",
        price=Decimal("199.90"),
        product_asset_id=PRODUCT_ASSET_ID,
    )

    assert first == second
    assert first.document_id == second.document_id
    assert changed.document_id != first.document_id


def test_price_is_quantized_and_formatted_exactly():
    document = create_template(
        title="价格测试",
        price=Decimal("199.9"),
        product_asset_id=PRODUCT_ASSET_ID,
    )
    price_node = next(node for node in document.nodes if node.role == "price")
    price_text = "".join(line.text for line in price_node.lines)

    assert json.loads(document.model_dump_json())["facts"]["price"] == "199.90"
    assert format_price_text(Decimal("199.90")) == "¥199.90"
    assert price_text == "¥199.90"


@pytest.mark.parametrize("price", [Decimal("0"), Decimal("-1"), Decimal("1000000")])
def test_out_of_range_price_rejected(price):
    with pytest.raises(ValidationError):
        create_template(title="价格测试", price=price, product_asset_id=PRODUCT_ASSET_ID)


def test_price_with_more_than_two_decimals_rejected():
    with pytest.raises(ValidationError):
        create_template(
            title="价格测试",
            price=Decimal("1.999"),
            product_asset_id=PRODUCT_ASSET_ID,
        )


@pytest.mark.parametrize(
    "title",
    [
        "",
        "   ",
        "第一行\n第二行",
        "bad\ufffe",
        "bad\uffff",
        "bad\ud800",
        "bad\x00",
        "bad\x1f",
        "bad\x85",
        "x" * 36,
    ],
)
def test_invalid_title_rejected(title):
    with pytest.raises(ValidationError):
        create_template(
            title=title,
            price=Decimal("199.90"),
            product_asset_id=PRODUCT_ASSET_ID,
        )


def test_title_preserves_cjk_emoji_and_escaped_markup():
    document = create_template(
        title="夏日😀 & <清凉>",
        price=Decimal("99"),
        product_asset_id=PRODUCT_ASSET_ID,
    )
    title = next(node for node in document.nodes if node.role == "title")

    assert title.plain_text == "夏日😀 & <清凉>"


@pytest.mark.parametrize(
    "asset_id",
    [
        "",
        "Product",
        "product.png",
        "../product",
        "/tmp/product.png",
        "https://example.com/product.png",
        "product id",
        "a" * 65,
    ],
)
def test_illegal_asset_id_rejected(asset_id):
    with pytest.raises(ValidationError):
        create_template(
            title="商品",
            price=Decimal("199.90"),
            product_asset_id=asset_id,
        )


def test_duplicate_node_ids_rejected():
    payload = document_payload()
    payload["nodes"].append(dict(payload["nodes"][-1]))

    with pytest.raises(ValidationError, match="node_id"):
        DesignDocument.model_validate(payload)


@pytest.mark.parametrize("value", [float("inf"), float("-inf"), float("nan")])
def test_non_finite_node_coordinates_rejected(value):
    payload = document_payload()
    payload["nodes"][1]["x"] = value

    with pytest.raises(ValidationError):
        DesignDocument.model_validate(payload)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("page", "width"), 0),
        (("page", "height"), PAGE_MAX_SIZE + 1),
        (("page", "width"), 1024.5),
        (("nodes", 1, "width"), 0),
        (("nodes", 1, "height"), -1),
        (("nodes", 1, "width"), NODE_MAX_SIZE + 1),
        (("nodes", 1, "rotation"), 361),
        (("nodes", 1, "opacity"), 1.5),
    ],
)
def test_abnormal_sizes_and_ranges_rejected(path, value):
    payload = document_payload()
    target = payload
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value

    with pytest.raises(ValidationError):
        DesignDocument.model_validate(payload)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload.update({"unexpected": True}),
        lambda payload: payload["nodes"][1].update({"unexpected": True}),
        lambda payload: payload["nodes"][1].update({"node_type": "path"}),
        lambda payload: payload["nodes"][1].update({"node_type": "text"}),
        lambda payload: payload.update({"schema_version": 2}),
    ],
)
def test_unknown_fields_types_and_versions_rejected(mutate):
    payload = document_payload()
    mutate(payload)

    with pytest.raises(ValidationError):
        DesignDocument.model_validate(payload)


def test_image_node_must_reference_declared_asset():
    payload = document_payload()
    payload["asset_refs"] = []

    with pytest.raises(ValidationError, match="asset"):
        DesignDocument.model_validate(payload)


def test_text_fonts_must_be_declared():
    payload = document_payload()
    payload["font_refs"] = []

    with pytest.raises(ValidationError, match="font"):
        DesignDocument.model_validate(payload)


def test_price_layer_must_match_business_fact():
    payload = document_payload()
    price_node = next(node for node in payload["nodes"] if node["role"] == "price")
    price_node["lines"][0]["text"] = "¥1.00"

    with pytest.raises(ValidationError, match="price"):
        DesignDocument.model_validate(payload)


def test_asset_ref_metadata_is_all_or_nothing():
    with pytest.raises(ValidationError):
        AssetRef(asset_id=PRODUCT_ASSET_ID, sha256="0" * 64)
    with pytest.raises(ValidationError):
        AssetRef(asset_id=PRODUCT_ASSET_ID, media_type="image/png")
