"""Fail-closed preflight of a draft campaign against the current approved Brief."""

import hashlib
from decimal import Decimal

import pytest

from app.campaigns.brief_preflight import (
    CampaignBriefPreflightError,
    check_campaign_brief_preflight,
)
from app.campaigns.models import CampaignPlan
from app.schemas import PosterSpec


RUN_ID = "a" * 32
IMAGE_BYTES = b"synthetic-original-image-bytes"


def _digest(spec: PosterSpec) -> str:
    return hashlib.sha256(spec.model_dump_json().encode("utf-8")).hexdigest()


def _spec() -> PosterSpec:
    return PosterSpec(title="电动牙刷", price=Decimal("199.90"))


def _plan(spec: PosterSpec | None = None, **overrides) -> CampaignPlan:
    spec = spec or _spec()
    data = {
        "campaign_id": "launch_001",
        "brief_run_id": RUN_ID,
        "brief_spec_digest": _digest(spec),
        "facts": {
            "sku_id": "SKU_001",
            "title": "电动牙刷",
            "price": "199.90",
            "currency": "CNY",
            "product_asset_sha256": hashlib.sha256(IMAGE_BYTES).hexdigest(),
        },
        "style": {"primary_color": "#123456", "accent_color": "#AABBCC"},
        "scenes": [
            {
                "scene_id": "hero",
                "channel": "square",
                "purpose": "hero",
                "marketing_copy": "新品上市",
            },
            {
                "scene_id": "feed",
                "channel": "feed_4x5_native",
                "purpose": "detail",
                "marketing_copy": "了解更多",
            },
        ],
    }
    for key, value in overrides.items():
        if key == "facts":
            data["facts"].update(value)
        else:
            data[key] = value
    return CampaignPlan.model_validate(data)


def _approved(spec: PosterSpec | None = None) -> dict:
    spec = spec or _spec()
    return {
        "run_id": RUN_ID,
        "status": "APPROVED",
        "pending_approval": False,
        "confirmed_spec": spec.model_dump(mode="json"),
    }


def test_matching_current_approved_brief_and_asset_digest_is_only_preflight(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "app.campaigns.brief_preflight.get_run",
        lambda run_id: calls.append(run_id) or _approved(),
    )
    result = check_campaign_brief_preflight(_plan(), original_product_bytes=IMAGE_BYTES)
    assert result is None  # no approval token or release permit
    assert calls == [RUN_ID]


def test_noncanonical_brief_run_id_is_rejected_without_lookup(monkeypatch):
    def forbidden_lookup(_run_id):
        pytest.fail("invalid run ID must be rejected before authoritative read")

    monkeypatch.setattr("app.campaigns.brief_preflight.get_run", forbidden_lookup)
    with pytest.raises(CampaignBriefPreflightError):
        check_campaign_brief_preflight(
            _plan(brief_run_id="brief_run_01"), original_product_bytes=IMAGE_BYTES
        )


@pytest.mark.parametrize(
    "overrides",
    [
        {"status": "WAITING_CONFIRMATION"},
        {"status": "REJECTED"},
        {"pending_approval": True},
        {"pending_approval": None},
        {"confirmed_spec": None},
        {"run_id": "b" * 32},
        {"run_id": None},
    ],
)
def test_unapproved_or_invalid_authoritative_snapshot_fails_closed(monkeypatch, overrides):
    state = _approved()
    state.update(overrides)
    monkeypatch.setattr("app.campaigns.brief_preflight.get_run", lambda _rid: state)
    with pytest.raises(CampaignBriefPreflightError):
        check_campaign_brief_preflight(_plan(), original_product_bytes=IMAGE_BYTES)


def test_changed_authoritative_brief_spec_digest_rejected(monkeypatch):
    monkeypatch.setattr(
        "app.campaigns.brief_preflight.get_run",
        lambda _rid: _approved(PosterSpec(title="电动牙刷", price=Decimal("200.00"))),
    )
    with pytest.raises(CampaignBriefPreflightError):
        check_campaign_brief_preflight(_plan(), original_product_bytes=IMAGE_BYTES)


@pytest.mark.parametrize(
    "facts",
    [{"title": "未经确认的商品标题"}, {"price": "200.00"}],
)
def test_plan_cannot_override_approved_commercial_facts(monkeypatch, facts):
    monkeypatch.setattr("app.campaigns.brief_preflight.get_run", lambda _rid: _approved())
    with pytest.raises(CampaignBriefPreflightError):
        check_campaign_brief_preflight(_plan(facts=facts), original_product_bytes=IMAGE_BYTES)


@pytest.mark.parametrize("bad_bytes", [None, "text", b"", b"altered-image"])
def test_original_product_bytes_must_match_frozen_digest(monkeypatch, bad_bytes):
    monkeypatch.setattr("app.campaigns.brief_preflight.get_run", lambda _rid: _approved())
    with pytest.raises(CampaignBriefPreflightError):
        check_campaign_brief_preflight(_plan(), original_product_bytes=bad_bytes)


def test_unavailable_authoritative_checkpoint_cannot_be_accepted(monkeypatch):
    def unavailable(_run_id):
        raise OSError("simulated checkpoint outage")

    monkeypatch.setattr("app.campaigns.brief_preflight.get_run", unavailable)
    with pytest.raises(CampaignBriefPreflightError):
        check_campaign_brief_preflight(_plan(), original_product_bytes=IMAGE_BYTES)


def test_campaign_digest_is_not_a_substitute_for_poster_spec_digest(monkeypatch):
    monkeypatch.setattr("app.campaigns.brief_preflight.get_run", lambda _rid: _approved())
    with pytest.raises(CampaignBriefPreflightError):
        check_campaign_brief_preflight(
            _plan(brief_spec_digest=_plan().fingerprint()),
            original_product_bytes=IMAGE_BYTES,
        )
