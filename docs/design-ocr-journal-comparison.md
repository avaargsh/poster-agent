# Journaled OCR comparison preview (read-only, not an attestation)

The local DesignDocument OCR journal records a bounded, opt-in attempt with
an `EXECUTED`, `UNKNOWN`, or `UNAVAILABLE` state. **EXECUTED does not mean
the result is correct, independently verified or publishable.**

`GET /api/designs/{document_id}/revisions/{revision}/ocr-runs/{operation_id}/comparison-preview`

This route reads only the **server-persisted** observations for a completed
operation; it takes no OCR text from the HTTP caller and never reruns the
engine or mutates the journal. It requires the same named local reviewer
`export` scope and exact policy digest as the original run. Its checks:

1. The journal row belongs to this document, revision, principal and policy,
   and its status is exactly `EXECUTED`.
2. The current approved design still has the same approval ID, specification,
   asset manifest, renderer version and SVG SHA-256 as the journal record.
3. The current local pinned OCR model bundle still matches its recorded
   profile SHA-256. This is a **local file check**, not model authenticity.
4. The approved SVG is rasterized again; the resulting SVG/PNG SHA-256 and
   rasterizer label must match the journaled input. The current design head is
   reread afterward to reject a concurrent revision change.
5. The persisted OCR observations are validated as bounded text and scores,
   then compared with the exact current title, price and CTA using the same
   deterministic, non-attesting comparison contract as the OCR observation
   preview.

`UNKNOWN`/`UNAVAILABLE` => `409 OCR_RUN_NOT_EXECUTED`.
Changed source, model identity or raster => `409 OCR_SOURCE_MISMATCH`,
or `503` for unavailable pinned model/rasterizer. Stale revision or
revoked approval fails closed. Nonexistent/other-principal operation IDs
are not disclosed.

Even when all fields match, the result explicitly has
`ocr_status=OBSERVATIONS_UNATTESTED`,
`source_state=CURRENT_EXECUTED_UNATTESTED`,
`runtime_attested=false`, `quality_verified=false`, and
`release_ready=false`. No authority treats it as an externally signed
runtime receipt, immutable audit log, human approval, or release permit.

The read-time check cannot prevent a *future* design revision immediately
after the response, and a database administrator can rewrite local records.
Production acceptance still requires a separate trusted evidence authority,
independent model/runtime provenance, human review and a final atomic
release-time guard. This route is local diagnostic evidence only.
