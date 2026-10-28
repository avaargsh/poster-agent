"""核心领域模型：输入规格与修改接口。"""

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class LayerLayoutV1(BaseModel):
    """Versioned, safe square layer offsets in pixels, not arbitrary canvas code.

    A strict, bounded schema protects prices, CTA and logo from accidental
    clipping/overlap. Existing specs without this object have zero offsets.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    title_dx: int = Field(default=0, ge=-20, le=20, strict=True)
    title_dy: int = Field(default=0, ge=-20, le=20, strict=True)
    price_dx: int = Field(default=0, ge=-12, le=12, strict=True)
    price_dy: int = Field(default=0, ge=-8, le=8, strict=True)
    cta_dx: int = Field(default=0, ge=-16, le=16, strict=True)
    cta_dy: int = Field(default=0, ge=-12, le=12, strict=True)
    logo_dx: int = Field(default=0, ge=-16, le=16, strict=True)
    logo_dy: int = Field(default=0, ge=-16, le=16, strict=True)


class PosterSpec(BaseModel):
    title: str = Field(min_length=1, max_length=35)
    subtitle: str = Field(default="品质生活 · 限时优惠", max_length=45)
    price: Decimal = Field(gt=0, le=999999, decimal_places=2)
    original_price: Decimal | None = Field(default=None, gt=0, le=999999, decimal_places=2)
    style: str = Field(default="蓝色科技风", min_length=1, max_length=80)
    cta: str = Field(default="立即抢购", min_length=1, max_length=12)
    background_mode: Literal["local", "ai"] = "local"
    # Bounded, deterministic square layout controls. Old saved versions
    # without these keys continue to render with the original composition.
    layout_id: Literal["classic", "hero"] = "classic"
    product_align: Literal["top", "center", "bottom"] = "center"
    product_scale: Literal["full", "compact"] = "full"
    # Original source and optional operator-approved processed candidate both
    # survive every revision. Old posters default to original pixels.
    product_source_mode: Literal["original", "cutout"] = "original"
    layer_layout: LayerLayoutV1 = Field(default_factory=LayerLayoutV1)

    @field_validator("title", "subtitle", "style", "cta")
    @classmethod
    def no_empty_or_controls(cls, value: str) -> str:
        value = value.strip()
        if any(ord(ch) < 32 for ch in value):
            raise ValueError("不能包含控制字符或换行符")
        return value

    @model_validator(mode="after")
    def validate_prices(self):
        if self.original_price is not None and self.original_price < self.price:
            raise ValueError("原价不能低于活动价")
        return self


class PosterEdit(BaseModel):
    # Optional optimistic-concurrency guard; legacy API clients may omit it.
    expected_version: int | None = Field(default=None, ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=35)
    subtitle: str | None = Field(default=None, max_length=45)
    price: Decimal | None = Field(default=None, gt=0, le=999999, decimal_places=2)
    original_price: Decimal | None = Field(default=None, gt=0, le=999999, decimal_places=2)
    cta: str | None = Field(default=None, min_length=1, max_length=12)
    layout_id: Literal["classic", "hero"] | None = None
    product_align: Literal["top", "center", "bottom"] | None = None
    product_scale: Literal["full", "compact"] | None = None
    product_source_mode: Literal["original", "cutout"] | None = None
    layer_layout: LayerLayoutV1 | None = None

    @field_validator("title", "subtitle", "cta")
    @classmethod
    def no_empty_or_controls(cls, value):
        if value is None:
            return value
        value = value.strip()
        if any(ord(ch) < 32 for ch in value):
            raise ValueError("不能包含控制字符或换行符")
        return value


def price_text(value: Decimal) -> str:
    return format(value, ".2f").rstrip("0").rstrip(".")
