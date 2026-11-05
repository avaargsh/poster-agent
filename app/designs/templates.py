"""Deterministic first-version templates.

Templates are ordinary Python functions, not model-generated SVG.  A model may
later choose a template and emit bounded commands, but it never authors markup.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints, field_validator

from .models import (
    TEMPLATE_ID,
    AssetRef,
    BusinessFacts,
    DesignDocument,
    DocumentSource,
    FontRef,
    ImageNode,
    Page,
    RectNode,
    TextLine,
    TextNode,
    format_price_text,
    validate_plain_text,
)

TITLE_MAX_CHARS = 35
TITLE_LINE_MAX_CHARS = 12
TITLE_FONT_SIZE = 64
TITLE_LINE_HEIGHT = 76
FONT_FAMILY = "Noto Sans CJK SC"


class _TemplateTitle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: Annotated[str, StringConstraints(min_length=1, max_length=TITLE_MAX_CHARS)]

    @field_validator("value")
    @classmethod
    def normalized_title(cls, value: str) -> str:
        # Validate before trimming so a control character at either edge is
        # rejected rather than silently disappearing as whitespace.
        validate_plain_text(value)
        value = value.strip()
        if not value:
            raise ValueError("title cannot be blank")
        return value


def _wrap_title(title: str, *, x: float, first_y: float) -> list[TextLine]:
    chunks = [
        title[index : index + TITLE_LINE_MAX_CHARS]
        for index in range(0, len(title), TITLE_LINE_MAX_CHARS)
    ]
    return [
        TextLine(text=chunk, x=x, y=first_y + index * TITLE_LINE_HEIGHT)
        for index, chunk in enumerate(chunks)
    ]


def _price_font_size(text: str) -> float:
    return 88 if len(text) <= 9 else 64


def create_template(*, title: str, price: Decimal, product_asset_id: str) -> DesignDocument:
    """Create the fixed 1024 x 1024 promotional template.

    The product must be supplied by the caller as an immutable asset id.  No
    placeholder product is drawn by the template; rendering fails when the
    referenced asset bytes are absent.
    """

    clean_title = _TemplateTitle(value=title).value
    facts = BusinessFacts(price=price)
    price_text = format_price_text(facts.price, facts.currency)

    title_node = TextNode(
        node_id="title",
        role="title",
        x=72,
        y=88,
        width=880,
        height=250,
        font_family=FONT_FAMILY,
        font_weight=700,
        font_size=TITLE_FONT_SIZE,
        color="#f8fafc",
        lines=_wrap_title(clean_title, x=72, first_y=152),
    )
    accent_node = RectNode(
        node_id="accent",
        role="decoration",
        x=72,
        y=64,
        width=120,
        height=12,
        fill="#67e8f9",
        corner_radius=6,
    )
    product_node = ImageNode(
        node_id="product",
        role="product",
        x=548,
        y=356,
        width=404,
        height=404,
        asset_id=product_asset_id,
        fit="contain",
    )
    price_node = TextNode(
        node_id="price",
        role="price",
        x=72,
        y=752,
        width=620,
        height=120,
        font_family=FONT_FAMILY,
        font_weight=700,
        font_size=_price_font_size(price_text),
        color="#67e8f9",
        lines=[TextLine(text=price_text, x=72, y=840)],
    )

    document_key = "|".join((TEMPLATE_ID, clean_title, str(facts.price), product_asset_id))
    document_id = f"doc_{uuid.uuid5(uuid.NAMESPACE_URL, 'poster-agent/design/' + document_key).hex}"

    return DesignDocument(
        document_id=document_id,
        page=Page(width=1024, height=1024, background="#0b1f3a"),
        nodes=[accent_node, product_node, title_node, price_node],
        asset_refs=[AssetRef(asset_id=product_asset_id)],
        font_refs=[
            FontRef(family=FONT_FAMILY, weight=400),
            FontRef(family=FONT_FAMILY, weight=700),
        ],
        facts=facts,
        source=DocumentSource(kind="template", template_id=TEMPLATE_ID),
    )
