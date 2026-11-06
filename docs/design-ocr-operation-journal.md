# DesignDocument OCR operation journal — local, opt-in, untrusted

This phase connects the exact **currently approved** DesignDocument SVG/PNG
input to an explicitly requested local PaddleOCR adapter call, recorded in
the existing single-writer DesignRepository SQLite database.

**This is not a production OCR attestation, immutable audit receipt, model
identity guarantee, human acceptance, or permission to publish.** The server
forces `runtime_attested=false`, `quality_verified=false`,
`release_ready=false` for every response and status.

## Opt-in and permissions

- Disabled by default. Set `POSTER_DESIGN_OCR_RUNS_ENABLED=1` **before**
  starting the FastAPI process. Any other value disables the POST route.
  This switch is frozen at lifespan startup.
- Configure `POSTER_DESIGN_REVIEWERS_JSON` with a **named** principal holding
  `export` scope. Anonymous legacy local approvals cannot invoke this
  execution endpoint.
- Only loopback access is supported; normal local origin/Host checks remain
  in force. Never expose this API through a public reverse proxy.
- Explicit operator provisioning is required for CairoSVG, PaddleOCR,
  PaddlePaddle, model weights and matching fonts. First-use PaddleOCR may try
  downloading models; run only in an operator-controlled offline network with
  models already provisioned and validated. Default CI uses injected local
  raster/model outputs and downloads no OCR models.

## Request and durable identity

Read `GET /api/designs/{id}/revisions/{revision}/approved.png` with the
named export token. Record the returned `X-Approved-SVG-SHA256` and
`X-Raster-PNG-SHA256`. The caller then explicitly submits:

```json
{
  "operation_id": "ocr_0123456789abcdef0123456789abcdef",
  "svg_sha256": "<64 lowercase hex characters>",
  "png_sha256": "<64 lowercase hex characters>"
}
```

`POST /api/designs/{id}/revisions/{revision}/ocr-runs` accepts only those
three fields. Missing current approval, missing visible title/price/CTA,
corrupt assets, stale revision, invalid/changed hash, missing export scope
or disabled OCR fails closed **before any model invocation**.

Inside one SQLite `BEGIN IMMEDIATE` transaction the server binds the
operation to document, revision, approval ID, exact SVG SHA, PNG SHA,
spec/asset digests, renderer, rasterizer, named local principal/policy and
request fingerprint. After committing `EXECUTING`, it writes the already
validated PNG to an isolated temporary directory and calls the existing
`app.ocr_provider.recognize()` in this request. The file is deleted when
the attempt exits. There is no background worker or implicit retry.

The terminal update again validates the current approved snapshot. Successful
local adapter output is bounded to at most 200 lines of plain text, confidence,
and optional 4-integer bounding boxes. Valid results are stored alongside
sanitized diagnostic package/platform telemetry, with `model_weights_sha256`
explicitly `null` because model weights are not independently verified.

## Status and replay semantics

- `EXECUTING`: durable admission; request is in flight.
- `EXECUTED`: this process received and validated local adapter observations;
  **not** independent proof of genuine model execution or quality.
- `UNAVAILABLE`: the adapter declared its model/runtime unavailable.
- `UNKNOWN`: execution or result was ambiguous, source changed while
  processing, or a previous process died with an `EXECUTING` row.
- `FAILED`: reserved for future explicitly proven pre-declared failures;
  currently not used to downgrade an ambiguous OCR invocation.

Reusing the same `operation_id` and exact bound request returns the
stored state (`HTTP 200`, `replayed=true`) **without rerunning OCR**.
The same ID with changed hashes/target fails with
`409 IDEMPOTENCY_CONFLICT`. The initial submission returns `201`,
even if its terminal state is `UNAVAILABLE` or `UNKNOWN`.
Query status with
`GET /api/designs/{id}/revisions/{revision}/ocr-runs/{operation_id}`.

On the next single-owner repository initialization after a crash, abandoned
`EXECUTING` rows are atomically converted to `UNKNOWN` with reason
`RECOVERED_AFTER_RESTART`. This does not justify blind re-execution,
because actual provider execution may have already occurred. Operators
must reconcile and issue a **new operation ID** after assessing evidence.

## Boundaries remaining before acceptance

This journal is local SQLite, not append-only tamper-resistant storage. The
rasterizer label and PaddleOCR package details are process-reported; model
weights and runtime identity are not independently authenticated. There is
no trusted model attestation or signed external evidence receipt, no
calibrated OCR/spatial gate, no independent human approval, and no proven
20-real-SKU trial. A changed design while OCR is running yields `UNKNOWN`,
not a stale approved result. Neither `EXECUTED` nor an all-matched text
comparison can grant a release permit.

See Issue #14 for the remaining real CairoSVG smoke, model checksum pinning,
UNKNOWN reconciliation, external evidence, human review and real-SKU tests.
