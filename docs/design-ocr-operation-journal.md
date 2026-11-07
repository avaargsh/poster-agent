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
  PaddlePaddle, model weights and matching fonts. **DesignDocument OCR requires
  `POSTER_DESIGN_OCR_MODEL_CONFIG_JSON`** with pinned local detection and
  recognition model directory digests, captured at service startup. Missing or
  changed models fail before durable admission and model inference.
  See [offline model pins](design-ocr-offline-model-pins.md).
- Both model directories are passed explicitly to the PaddleOCR 3.3 adapter;
  a missing default model path must not trigger implicit model selection.
  This reduces accidental model downloads but **does not itself enforce
  network isolation**; isolate the process at OS/container/firewall level.
  Default CI uses synthetic local models, not real weights, and downloads none.

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

The PaddleOCR adapter caches one model instance per process. Calls to its
`recognize()` are serialized by a narrow process-local lock to avoid
concurrent predictions on an engine without proven thread-safe inference.
SQLite admission, status lookup and non-model raster work are not held under
this lock. This is not a distributed execution lease or evidence of model
correctness; only the supported single-worker loopback deployment is in scope.

The durable operation also stores `model_profile_sha256`, the SHA-256 of
the operator's canonical pinned model profile. Replays with a different
profile digest do not match the original idempotency fingerprint. Existing
local databases are migrated additively; historic records without a model
profile retain a `null` digest and cannot acquire model provenance
retroactively. The model bundle is rechecked immediately before and after
inference; drift or unreadable weights after admission yields
`UNKNOWN / MODEL_PROFILE_CHANGED`, with no retained observations.

The terminal update again validates the current approved snapshot. Successful
local adapter output is bounded to at most 200 lines of plain text, confidence,
and optional 4-integer bounding boxes. Valid results are stored alongside
sanitized diagnostic package/platform telemetry, with `model_weights_sha256`
explicitly `null`: a locally computed full-tree digest binds files in the
journal but is **not** an independent authenticated model signature or
a guarantee that the loaded inference weights equal those files.

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
