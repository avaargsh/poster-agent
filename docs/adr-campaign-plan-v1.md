# ADR: bounded, draft-only CampaignPlan v1

Status: **proposed contract slice**, not connected to HTTP, LangGraph, batch execution, image generation, or release.

## Goal

Prepare a deterministic two-to-four-scene campaign proposal that shares SKU
facts and style across independent design outputs. This takes the campaign
planning and style-lock ideas from upstream ecommerce projects without copying
an agent stack or introducing another approval authority.

## Authority / integration boundary

- An authorized operator or trusted adapter must re-read the **currently
  approved Brief** and compare the canonical `PosterSpec` and digest to the
  proposed plan. `brief_run_id` / `brief_spec_digest` are **untrusted references**
  until that check. No method in this module performs it.
- The product snapshot holds the original SKU ID, product image SHA-256, title,
  CNY price and currency. A scene can propose marketing copy but cannot set
  or override the title, price, or protected product asset. Marketing copy is
  **unreviewed** and must not be treated as a verified claim.
- The style lock is the normalized primary/accent hex colors plus one fixed
  local font family. `style_fingerprint()` is deterministic over this contract.
- `product_scene_projection()` is **preview data only**; it is not a renderer
  adapter. Converting a projection into a `DesignDocument` requires explicit
  compatibility validation, human review, and creation of an independent
  revision; a new layout must not inherit OCR or approval from another scene.
- Legacy `PosterSpec` outputs and new `DesignDocument` revisions are distinct
  today. Keep `DesignRepository` the revision authority; do not add a third
  mutable revision or approval store. Brief checkpoint and design SQLite are
  not a single atomic transaction; add reconciliation before protected use.
- The output `fingerprint()` is an ordinary SHA-256 checksum of sorted compact
  UTF-8 JSON; **not** a digital signature, execution attestation or permit.
- `status` remains `DRAFT`, `release_ready` remains false; no paid generation,
  OCR, storage, identity, signing, human acceptance or publishing is performed.

## Minimum tests / next integration

Tests freeze deterministic replay, campaign style changes, commercial fact
changes, duplicate scenes, missing scenes, caller attempts to supply a price
inside a scene, spoofed approval/release flags, bad asset hashes and invalid
text. Run the isolated contract tests:

```bash
uv run --locked python -m pytest tests/test_campaign_plan_contract.py -q
```

Before wiring production routes: bind authoritative Brief read with exact
source digest, define a human-approved draft-to-design operation with stable
idempotency, revalidate assets and reviewer scope, and build two independent
scene outputs with separate design revision, OCR, and M6 review evidence.
The existing 20-distinct-authorized-SKU M6 gate is unchanged.
