"""Draft-only CampaignPlan v1: bounded scenes and frozen product facts.

This is a pure proposal contract, not an approval, renderer, OCR runner or
release permit. The authoritative Brief and image assets must be revalidated
by the caller at every protected execution boundary.
"""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

Identifier = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{1,63}$")]
Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
HexColor = Annotated[str, StringConstraints(pattern=r"^#[0-9A-Fa-f]{6}$")]


def _plain_text(value: str) -> str:
    if not value.strip():
        raise ValueError("text must not be blank")
    for ch in value:
        cp = ord(ch)
        if cp < 32 or 127 <= cp <= 159 or 0xD800 <= cp <= 0xDFFF or cp in (0xFFFE, 0xFFFF):
            raise ValueError("control and invalid XML characters are forbidden")
    return value


def _fingerprint(value: dict) -> str:
    canonical = json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


class ProductFacts(BaseModel):
    """Operator-supplied frozen facts; never authoritative without source verification."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    sku_id: Identifier
    title: Annotated[str, StringConstraints(min_length=1, max_length=80)]
    price: Decimal = Field(gt=0, le=999999, decimal_places=2)
    currency: Literal["CNY"] = "CNY"
    product_asset_sha256: Digest

    @field_validator("title")
    @classmethod
    def valid_title(cls, value: str) -> str:
        return _plain_text(value)

    @field_validator("price")
    @classmethod
    def canonical_price(cls, value: Decimal) -> Decimal:
        return value.quantize(Decimal("0.01"))


class CampaignStyle(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    primary_color: HexColor
    accent_color: HexColor
    font_family: Literal["Noto Sans CJK SC"] = "Noto Sans CJK SC"

    @field_validator("primary_color", "accent_color")
    @classmethod
    def canonical_color(cls, value: str) -> str:
        return value.upper()


class SceneSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    scene_id: Identifier
    channel: Literal["square", "feed_4x5_native"]
    purpose: Literal["hero", "detail", "promotion"]
    marketing_copy: Annotated[str, StringConstraints(min_length=1, max_length=100)]

    @field_validator("marketing_copy")
    @classmethod
    def valid_copy(cls, value: str) -> str:
        return _plain_text(value)


class CampaignPlan(BaseModel):
    """Unapproved proposal; fingerprints are checksums, not signatures or permits."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    campaign_id: Identifier
    brief_run_id: Identifier
    brief_spec_digest: Digest
    facts: ProductFacts
    style: CampaignStyle
    scenes: Annotated[tuple[SceneSpec, ...], Field(min_length=2, max_length=4)]
    status: Literal["DRAFT"] = "DRAFT"
    release_ready: Literal[False] = False

    @model_validator(mode="after")
    def unique_scene_ids(self) -> CampaignPlan:
        ids = [scene.scene_id for scene in self.scenes]
        if len(ids) != len(set(ids)):
            raise ValueError("scene_id must be unique within campaign")
        return self

    def fingerprint(self) -> str:
        return _fingerprint(self.model_dump(mode="json"))

    def style_fingerprint(self) -> str:
        return _fingerprint(self.style.model_dump(mode="json"))


def product_scene_projection(plan: CampaignPlan, scene_id: str) -> dict[str, str]:
    """Read-only draft projection, NOT an export or approval authorization.

    The SKU/price/title can only come from frozen facts; scene text cannot
    replace them. Consumers must recheck Brief, asset, policy and current
    DesignDocument approval before any model call, OCR or protected export.
    """
    scene = next((s for s in plan.scenes if s.scene_id == scene_id), None)
    if scene is None:
        raise KeyError(scene_id)
    return {
        "scene_id": scene.scene_id,
        "channel": scene.channel,
        "sku_id": plan.facts.sku_id,
        "product_asset_sha256": plan.facts.product_asset_sha256,
        "title": plan.facts.title,
        "price": str(plan.facts.price),
        "currency": plan.facts.currency,
        "marketing_copy": scene.marketing_copy,
        "style_fingerprint": plan.style_fingerprint(),
    }
