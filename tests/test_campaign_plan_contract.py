"""CampaignPlan v1: draft-only contract and immutable commercial facts."""

from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.campaigns.models import CampaignPlan, product_scene_projection


def draft():
    return {
        "campaign_id": "launch_001",
        "brief_run_id": "brief_run_01",
        "brief_spec_digest": "a" * 64,
        "facts": {
            "sku_id": "SKU_001",
            "title": "电动牙刷",
            "price": "199.90",
            "currency": "CNY",
            "product_asset_sha256": "b" * 64,
        },
        "style": {
            "primary_color": "#123456",
            "accent_color": "#AABBCC",
            "font_family": "Noto Sans CJK SC",
        },
        "scenes": [
            {"scene_id": "hero", "channel": "square", "purpose": "hero", "marketing_copy": "新品上市"},
            {
                "scene_id": "feed",
                "channel": "feed_4x5_native",
                "purpose": "promotion",
                "marketing_copy": "了解更多",
            },
        ],
    }


def test_two_scene_plan_is_draft_only_and_fingerprints_are_stable():
    plan = CampaignPlan.model_validate(draft())
    replay = CampaignPlan.model_validate(plan.model_dump(mode="json"))
    assert plan.fingerprint() == replay.fingerprint()
    assert plan.style_fingerprint() == replay.style_fingerprint()
    assert plan.status == "DRAFT"
    assert plan.release_ready is False
    assert plan.facts.price == Decimal("199.90")


def test_scene_projection_uses_frozen_facts_not_generated_copy():
    plan = CampaignPlan.model_validate(draft())
    projection = product_scene_projection(plan, "feed")
    assert projection == {
        "scene_id": "feed",
        "channel": "feed_4x5_native",
        "sku_id": "SKU_001",
        "product_asset_sha256": "b" * 64,
        "title": "电动牙刷",
        "price": "199.90",
        "currency": "CNY",
        "marketing_copy": "了解更多",
        "style_fingerprint": plan.style_fingerprint(),
    }
    with pytest.raises(KeyError):
        product_scene_projection(plan, "missing")


def test_change_to_style_or_price_changes_plan_fingerprint():
    initial = CampaignPlan.model_validate(draft())
    changed_style = draft()
    changed_style["style"]["accent_color"] = "#FFFFFF"
    changed_price = draft()
    changed_price["facts"]["price"] = "198.90"
    assert CampaignPlan.model_validate(changed_style).fingerprint() != initial.fingerprint()
    assert CampaignPlan.model_validate(changed_price).fingerprint() != initial.fingerprint()
    assert (
        CampaignPlan.model_validate(changed_price).style_fingerprint() == initial.style_fingerprint()
    )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda data: data["scenes"].append(dict(data["scenes"][0])),
        lambda data: data["scenes"].pop(),
        lambda data: data["scenes"][0].update({"price": "0.01"}),
        lambda data: data["scenes"][0].update({"approved": True}),
        lambda data: data["facts"].update({"price": "0"}),
        lambda data: data["facts"].update({"product_asset_sha256": "bad"}),
        lambda data: data["facts"].update({"title": "商品\n危险"}),
        lambda data: data["style"].update({"primary_color": "url(x)"}),
        lambda data: data.update({"release_ready": True}),
        lambda data: data.update({"status": "APPROVED"}),
        lambda data: data.update({"schema_version": 2}),
        lambda data: data.update({"unexpected": "x"}),
    ],
)
def test_invalid_or_forged_inputs_fail_closed(mutate):
    data = draft()
    mutate(data)
    with pytest.raises(ValidationError):
        CampaignPlan.model_validate(data)
