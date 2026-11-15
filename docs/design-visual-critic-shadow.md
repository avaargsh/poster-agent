# Visual Critic shadow preview (v1)

This is a read-only observation contract for a currently approved DesignDocument raster. It does **not** execute a VLM, verify aesthetic quality, edit a document, run OCR, authorize repair, or grant release permission.

## Procedure

1. Create a design and explicitly approve its current revision.
2. Fetch GET /api/designs/{id}/revisions/{revision}/approved.png with the existing export reviewer scope. Record X-Approved-SVG-SHA256 and X-Raster-PNG-SHA256.
3. POST /api/designs/{id}/revisions/{revision}/visual-critic-preview with JSON: schema_version=1, svg_sha256 and png_sha256 taken from the response headers, and findings (0–20 items).

Each finding contains a unique finding_id, category (contrast, spacing, visual_hierarchy, product_visibility, text_legibility, brand_style), severity (info/minor/major), strict numeric confidence in [0,1], a single-line explanation up to 400 characters, optional normalized rectangle {x,y,width,height} entirely inside the canvas, and a suggested_action (NONE, REVIEW_LAYOUT, REVIEW_STYLE, REVIEW_BACKGROUND). These are *review suggestions*, never executable changes.

The server obtains an authoritative approval/OCR target and protected SVG, renders bounded PNG pixels and rechecks the approval/source after rasterization. Missing/stale approval, changed export policy, corrupt assets and hash mismatches fail closed. CairoSVG is optional; this endpoint does not download image or model files.

Every successful response, including an empty findings list, has critic_status=DECLARED_FINDINGS_UNATTESTED, model_execution_attested=false, quality_verified=false, repair_authorized=false and release_ready=false. It includes source identities plus observations_sha256 and critic_fingerprint_sha256 for deterministic local comparison. These hashes are neither signatures nor proof the caller inspected those pixels. The response is no-store and never persists caller findings.

Do not infer OCR quality, genuine model execution, trustworthy provider identity, independent review or publish permission from this preview. The existing local-only host restrictions and optional named export reviewer policy remain in force. Do not deploy to the public internet.

## Test

Focused: uv run --locked python -m pytest -q tests/test_design_visual_critic.py
Full: uv run --locked python -m pytest -q
Lint: uv run --locked ruff check app scripts evals tests

Synthetic tests must reject forged release flags, malformed bounds, duplicate IDs, wrong SVG/PNG digests, stale approval, revision changes during rasterization and missing export scope. They cannot establish real VLM precision/recall.

## Follow-on steps (not implemented)

- P0-B: explicit pinned VLM execution in shadow mode, with bounded costs, provenance and independently labeled human review.
- P0-C: separate constrained Repair Planner that creates version-bound proposals but never autonomously applies them; immutable price/SKU and locked product/Logo layers remain protected.
- P0-D: after explicit proposal application, rerasterize and rerun OCR/hard rules, maintain a durable operation journal and reconcile UNKNOWN outcomes rather than assuming failure/success.

This slice does not close https://github.com/avaargsh/poster-agent/issues/14 or establish independent OCR attestation.
