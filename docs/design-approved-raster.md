# Approved DesignDocument PNG raster (local, not OCR evidence)

`GET /api/designs/{document_id}/revisions/{revision}/approved.png` exports
bounded PNG bytes from the **currently approved** constrained SVG renderer.

This endpoint uses the exact same current revision, asset checks and optional
reviewer `export` scope as `approved.svg`. It reads the authorized SVG,
rasterizes it in-process, then re-reads the authorized current SVG and compares
the SHA-256 before sending PNG bytes. If the head changes, the approval is
revoked, an asset changes, or the export policy no longer matches, it fails
closed. A later update after the second read remains possible; downstream
consumers must revalidate at their own acceptance or release boundary.

The response uses `image/png`, `Cache-Control: no-store`, and headers:
- `X-Approved-SVG-SHA256`: SHA-256 of the exact UTF-8 SVG raster source
- `X-Raster-PNG-SHA256`: SHA-256 of returned PNG bytes
- `X-Design-Rasterizer`: local raster engine/version reported by this process
- `X-Design-OCR-Status: NOT_RUN`
- `X-Design-Release-Ready: false`

The renderer validates root SVG dimensions before conversion (64–4096px each
and <=8 million pixels) and rejects empty, oversized, wrong-format, wrong-size
or undecodable PNG output. The SVG comes exclusively from the existing
allowlisted server-side renderer, not a caller-provided URL or arbitrary SVG.

## Optional local execution

CairoSVG must be intentionally installed in the **local running environment**.
It is not a default dependency and CI does not download raster or OCR models.
Install and pin CairoSVG explicitly in your operator-controlled environment
using your approved package source and lock procedure; ensure the required
native libraries and Chinese fonts are present. Missing package/runtime returns
`503 DESIGN_RASTER_UNAVAILABLE`, not a fabricated image.

The code-based CI tests inject a deterministic local raster converter and
validate the API's provenance binding, authorization, stale-revision rejection
and malformed image guardrails. These are **not** proof that a real CairoSVG
runtime successfully rendered a Chinese-character poster. Before real usage,
perform a manual pinned-engine/OS/font smoke on the actual approved SVG/PNG and
record hashes and human visual inspection.

PNG byte hashes can vary with rasterizer, OS, font files, font fallback,
Cairo/Pango and encoder versions. The header is local diagnostic metadata, not
a signed supply-chain measurement or a guarantee of cross-host
reproducibility. Do **not** treat a local CairoSVG PNG as trusted runtime
attestation, successful PaddleOCR, per-field visual legibility, independent
audit evidence or permission to publish.

## What remains for Issue #14

Bind the exact PNG SHA and current approval/SVG SHA to an explicitly invoked
PaddleOCR operation journal; capture pinned model/config and runtime receipts;
reconcile ambiguous timeouts without rerunning side effects blindly; attach
human review and independently verifiable evidence before any release gate.
Never inherit legacy 4:5 poster OCR results for this DesignDocument renderer.
