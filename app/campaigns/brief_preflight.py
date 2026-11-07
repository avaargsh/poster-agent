"""Read-only CampaignPlan preflight against the *current* approved Brief.

Success returns None, never an approval, attestation, permission, or export
authorization. Re-run at the protected execution boundary. This function
does not prove SKU catalog ownership, original-asset provenance, or identity
of the human who approved the Brief.
"""

from __future__ import annotations

import hashlib
import hmac
import re

from pydantic import ValidationError

from app.review_service import get_run
from app.schemas import PosterSpec

from .models import CampaignPlan

_BRIEF_RUN_ID = re.compile(r"^[a-f0-9]{32}$")


class CampaignBriefPreflightError(ValueError):
    """An untrusted plan or its source could not pass read-only preflight."""


def check_campaign_brief_preflight(plan: CampaignPlan, *, original_product_bytes: bytes) -> None:
    """Require an exact current Brief/source match; never grant approval.

    The Brief digest deliberately uses the existing Brief-link algorithm,
    not CampaignPlan.fingerprint(). The supplied asset bytes are checked
    against frozen facts, but their source/catalog authority is not proved.
    """

    if not isinstance(plan, CampaignPlan):
        raise CampaignBriefPreflightError("validated CampaignPlan required")
    if not _BRIEF_RUN_ID.fullmatch(plan.brief_run_id):
        raise CampaignBriefPreflightError("noncanonical Brief run ID")
    if not isinstance(original_product_bytes, bytes) or not original_product_bytes:
        raise CampaignBriefPreflightError("nonempty original asset bytes required")

    asset_sha256 = hashlib.sha256(original_product_bytes).hexdigest()
    if not hmac.compare_digest(asset_sha256, plan.facts.product_asset_sha256):
        raise CampaignBriefPreflightError("original asset digest mismatch")

    try:
        current = get_run(plan.brief_run_id)
    except Exception as exc:
        raise CampaignBriefPreflightError("authoritative Brief unavailable") from exc

    if (
        not isinstance(current, dict)
        or current.get("run_id") != plan.brief_run_id
        or current.get("status") != "APPROVED"
        or current.get("pending_approval") is not False
    ):
        raise CampaignBriefPreflightError("Brief is not currently approved")

    try:
        spec = PosterSpec.model_validate(current.get("confirmed_spec"))
    except (ValidationError, TypeError, ValueError) as exc:
        raise CampaignBriefPreflightError("invalid approved Brief specification") from exc

    # Existing app/designs/routes.py Brief-link contract. Do not replace this
    # with sorted CampaignPlan JSON or a checksum of partial business fields.
    confirmed_digest = hashlib.sha256(spec.model_dump_json().encode("utf-8")).hexdigest()
    if not hmac.compare_digest(confirmed_digest, plan.brief_spec_digest):
        raise CampaignBriefPreflightError("approved Brief digest mismatch")

    if spec.title != plan.facts.title or spec.price != plan.facts.price:
        raise CampaignBriefPreflightError("frozen commercial facts do not match approved Brief")
