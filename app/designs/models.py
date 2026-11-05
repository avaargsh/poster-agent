"""Bounded, validation-first design documents for the AI canvas.

A design document is intentionally narrower than an arbitrary SVG/HTML
document.  It only contains text, raster images and rectangles whose values are
validated before any renderer sees them.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Literal, Union

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

SCHEMA_VERSION = 1
TEMPLATE_ID = "square-promo-v1"
RENDERER_VERSION = "design-svg@1"

PAGE_MIN_SIZE = 64
PAGE_MAX_SIZE = 4096
NODE_MAX_SIZE = 4096
MAX_ASSET_BYTES = 8 * 1024 * 1024
MAX_ASSET_DIMENSION = 4096
MAX_ASSET_PIXELS = 8_000_000

CURRENCY_SYMBOLS = {"CNY": "¥"}

NodeRole = Literal[
    "title",
    "subtitle",
    "price",
    "original_price",
    "product",
    "logo",
    "background",
    "cta",
    "decoration",
]
TextRole = Literal["title", "subtitle", "price", "original_price", "cta"]
ImageRole = Literal["product", "logo", "background"]
RectRole = Literal["background", "decoration"]

AssetId = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_-]{0,63}$")]
NodeId = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_-]{0,63}$")]
HexColor = Annotated[str, StringConstraints(pattern=r"^#[0-9a-fA-F]{6}$")]
Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
DocumentId = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")]

Coordinate = Annotated[float, Field(ge=-8192, le=8192, allow_inf_nan=False)]
Dimension = Annotated[float, Field(gt=0, le=NODE_MAX_SIZE, allow_inf_nan=False)]
PageDimension = Annotated[int, Field(ge=PAGE_MIN_SIZE, le=PAGE_MAX_SIZE, strict=True)]

_TEXT_MAX_LENGTH = 200


def validate_plain_text(value: str) -> str:
    """Validate one line of plain text that must stay well-formed XML.

    The document model, template title and command operations all share this
    check.  XML 1.0 cannot represent C0 controls, surrogate code points or
    U+FFFE/U+FFFF; C1 controls are also rejected as user-facing control text.
    Normal CJK, emoji and ``& < >`` remain accepted and are escaped only by the
    renderer.
    """

    if not value.strip():
        raise ValueError("text cannot be blank")
    for character in value:
        code_point = ord(character)
        if code_point < 0x20 or 0x7F <= code_point <= 0x9F:
            raise ValueError("text cannot contain control characters or newlines")
        if 0xD800 <= code_point <= 0xDFFF:
            raise ValueError("text cannot contain surrogate code points")
        if code_point in {0xFFFE, 0xFFFF}:
            raise ValueError("text contains a character that XML 1.0 cannot represent")
    return value


class Page(BaseModel):
    """A single page.  The first-version template is always 1024 x 1024."""

    model_config = ConfigDict(extra="forbid")

    width: PageDimension
    height: PageDimension
    background: HexColor


class BusinessFacts(BaseModel):
    """Facts owned by the product, never invented by an agent's prose."""

    model_config = ConfigDict(extra="forbid")

    price: Decimal = Field(gt=0, le=999999, decimal_places=2)
    currency: Literal["CNY"] = "CNY"

    @field_validator("price")
    @classmethod
    def canonical_price(cls, value: Decimal) -> Decimal:
        # JSON keeps Decimal as a string; quantizing keeps "199.90" exact and
        # prevents the rendered price from disagreeing with the stored fact.
        return value.quantize(Decimal("0.01"))


class AssetRef(BaseModel):
    """An immutable asset reference.

    ``create_template`` only receives an asset id, not bytes, so content hash
    metadata is optional at document creation.  When a hash is present the
    renderer verifies it; the asset repository task binds hashes at upload.
    """

    model_config = ConfigDict(extra="forbid")

    asset_id: AssetId
    sha256: Sha256 | None = None
    media_type: Literal["image/png", "image/jpeg", "image/webp"] | None = None
    byte_size: Annotated[int, Field(ge=1, le=MAX_ASSET_BYTES, strict=True)] | None = None

    @model_validator(mode="after")
    def metadata_is_all_or_nothing(self) -> AssetRef:
        metadata = (self.sha256, self.media_type, self.byte_size)
        if any(value is not None for value in metadata) and any(
            value is None for value in metadata
        ):
            raise ValueError("asset sha256, media_type and byte_size must be set together")
        return self


class FontRef(BaseModel):
    """A fixed font face allowed by the first-version document model."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    family: Literal["Noto Sans CJK SC"]
    weight: Literal[400, 700]
    style: Literal["normal"] = "normal"
    source: Literal["system"] = "system"


class DocumentSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["template"] = "template"
    template_id: Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")]


class TextLine(BaseModel):
    """One explicitly positioned line; the renderer never relies on auto-wrap."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    text: Annotated[str, StringConstraints(min_length=1, max_length=_TEXT_MAX_LENGTH)]
    x: Coordinate
    y: Coordinate

    @field_validator("text")
    @classmethod
    def text_is_plain(cls, value: str) -> str:
        return validate_plain_text(value)


class _NodeBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_id: NodeId
    role: NodeRole
    x: Coordinate
    y: Coordinate
    width: Dimension
    height: Dimension
    rotation: Annotated[float, Field(ge=-360, le=360, allow_inf_nan=False)] = 0
    opacity: Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)] = 1
    visible: bool = True
    locked: bool = False


class TextNode(_NodeBase):
    node_type: Literal["text"] = "text"
    role: TextRole
    lines: Annotated[list[TextLine], Field(min_length=1, max_length=12)]
    font_family: Literal["Noto Sans CJK SC"] = "Noto Sans CJK SC"
    font_weight: Literal[400, 700] = 700
    font_size: Annotated[float, Field(ge=8, le=256, allow_inf_nan=False)] = 48
    color: HexColor = "#111111"
    text_anchor: Literal["start", "middle", "end"] = "start"

    @property
    def plain_text(self) -> str:
        return "".join(line.text for line in self.lines)


class ImageNode(_NodeBase):
    node_type: Literal["image"] = "image"
    role: ImageRole
    asset_id: AssetId
    fit: Literal["contain", "cover"] = "contain"


class RectNode(_NodeBase):
    node_type: Literal["rect"] = "rect"
    role: RectRole
    fill: HexColor
    stroke: HexColor | None = None
    stroke_width: Annotated[float, Field(ge=0, le=32, allow_inf_nan=False)] = 0
    corner_radius: Annotated[float, Field(ge=0, le=512, allow_inf_nan=False)] = 0


DesignNode = Annotated[Union[TextNode, ImageNode, RectNode], Field(discriminator="node_type")]


class DesignDocument(BaseModel):
    """The authoritative, bounded design snapshot."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = SCHEMA_VERSION
    document_id: DocumentId
    revision: Annotated[int, Field(ge=1, strict=True)] = 1
    page: Page
    nodes: Annotated[list[DesignNode], Field(min_length=1, max_length=200)]
    asset_refs: Annotated[list[AssetRef], Field(max_length=200)] = Field(default_factory=list)
    font_refs: Annotated[list[FontRef], Field(min_length=1, max_length=8)]
    facts: BusinessFacts
    source: DocumentSource

    @model_validator(mode="after")
    def validate_document_invariants(self) -> DesignDocument:
        node_ids = [node.node_id for node in self.nodes]
        if len(node_ids) != len(set(node_ids)):
            raise ValueError("node_id values must be unique")

        asset_ids = [ref.asset_id for ref in self.asset_refs]
        if len(asset_ids) != len(set(asset_ids)):
            raise ValueError("asset_id values must be unique")

        referenced_assets = {node.asset_id for node in self.nodes if isinstance(node, ImageNode)}
        undeclared = referenced_assets.difference(asset_ids)
        if undeclared:
            raise ValueError(f"image nodes reference undeclared assets: {sorted(undeclared)}")

        authored_roles = (
            "title",
            "subtitle",
            "price",
            "original_price",
            "product",
            "logo",
            "background",
            "cta",
        )
        for role in authored_roles:
            if sum(node.role == role for node in self.nodes) > 1:
                raise ValueError(f"role {role} may only appear once")

        price_nodes = [node for node in self.nodes if node.role == "price"]
        if len(price_nodes) != 1:
            raise ValueError("exactly one price text node is required")
        expected_price = format_price_text(self.facts.price, self.facts.currency)
        if price_nodes[0].plain_text != expected_price:
            raise ValueError("price text must match the business price fact")

        declared_fonts = {(font.family, font.weight) for font in self.font_refs}
        for node in self.nodes:
            if isinstance(node, TextNode):
                font_key = (node.font_family, node.font_weight)
                if font_key not in declared_fonts:
                    raise ValueError(
                        f"text node {node.node_id} uses an undeclared font "
                        f"{node.font_family} {node.font_weight}"
                    )

        return self


def format_price_text(value: Decimal, currency: str = "CNY") -> str:
    """Format a business price using a stable currency symbol and two decimals."""

    if currency not in CURRENCY_SYMBOLS:
        raise ValueError(f"unsupported currency: {currency}")
    return f"{CURRENCY_SYMBOLS[currency]}{value:.2f}"
