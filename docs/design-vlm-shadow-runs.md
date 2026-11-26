# Opt-in local VLM shadow execution (v1)

This slice adds a **real, explicit local Ollama /api/chat adapter** and a SQLite operation journal to the existing read-only Visual Critic contract. It is experimental, not an acceptance or publish gate. All findings are review suggestions. No automatic edit proposal or repair is created.

## Prerequisites

- Run only with the repository's loopback-only app launcher (not a public reverse proxy).
- Install and operate Ollama separately with a previously downloaded vision-capable model. No runtime model download, no arbitrary upstream URL and no cloud fallback. The adapter calls **only** 127.0.0.1:11434, with redirects and environment proxies disabled.
- Install the optional CairoSVG rasterizer to render the approved SVG into a bounded PNG. Its absence fails closed.
- Configure POSTER_DESIGN_REVIEWERS_JSON with a named `approve` reviewer and a named `export` reviewer. Legacy anonymous local mode is intentionally not enough to launch inference.
- Set POSTER_DESIGN_VLM_RUNS_ENABLED=1 explicitly and set POSTER_DESIGN_VLM_MODEL_CONFIG_JSON as a single JSON string:

```json
{"schema_version":1,"model":"llava:latest","digest":"sha256:<64 lowercase hex characters from the local Ollama /api/tags entry>"}
```

Set both environment variables before starting the server; they are frozen at lifespan startup. A configured model name alone never enables execution. The model/digest pair is checked against the **local server's claimed model listing** before and after the invocation. The pinned name must appear **exactly once**; duplicate entries fail closed even if one reports the expected digest. A preflight ambiguity refuses admission; a postflight ambiguity cannot certify the completed operation. These local assertions do not independently prove weights or provider identity.

## Explicit operation

1. Create the DesignDocument and obtain an explicit approval of the current revision.
2. With the named export credential, GET `/api/designs/{id}/revisions/{revision}/approved.png`. Save the `X-Approved-SVG-SHA256` and `X-Raster-PNG-SHA256` headers.
3. Generate one operation ID as `vlm_` plus 32 lowercase random hexadecimal digits (e.g. UUIDv4 hex). POST `/api/designs/{id}/revisions/{revision}/visual-runs` with:

```json
{"operation_id":"vlm_0123456789abcdef0123456789abcdef","svg_sha256":"<64 hex>","png_sha256":"<64 hex>"}
```

4. GET `/api/designs/{id}/revisions/{revision}/visual-runs/{operation_id}` to inspect the journal. This lookup does not contact the model, even if inference is disabled after startup.

Every POST checks the pinned profile, principal/policy, current approval, source asset/SVG hashes, approved pixel hash and revision. A persistent SQLite row is committed **before** model inference. Duplicate operation IDs with identical input replay the journal result; changed input conflicts. One model call may execute per process at a time; other distinct operations receive a busy response and should not be auto-queued.

Postflight runs within the serialized approval transaction: after inference, it rechecks the exact current design, approval, reviewer policy, asset manifest and SVG hash, then **re-rasterizes the approved SVG** and compares both PNG SHA-256 and rasterizer identity with the pre-admission journal. If pixels or renderer differ, or the renderer is no longer available, the operation is terminally journaled as `UNKNOWN` with `SOURCE_CHANGED`; any returned findings are discarded. Identical operation IDs remain replay-only and cannot retrigger the model. This is an operator-local consistency check, not independent execution attestation.

The response is content-bounded and uses strict JSON-only QualityFinding validation. Normalized region coordinates must be actual JSON numbers (integer or float); booleans and numeric strings are rejected rather than coerced. Unknown fields, malformed/out-of-canvas regions, claims about release/repair, oversized responses and changed reported model are rejected. The non-streaming `/api/chat` reply must explicitly contain JSON `done=true` and `message.role="assistant"`; missing, false or incorrectly typed completion markers and user/tool-role messages cannot become `EXECUTED`, even when they contain syntactically valid findings. Once a run is admitted, these ambiguous replies produce `UNKNOWN` without an automatic retry. The model is instructed to report only soft visual defects such as layout, hierarchy, spacing and contrast. It is never instructed to edit or regenerate prices, SKU, CTA or Logo.

## Failure, recovery and evidence

- `EXECUTED` means the local adapter accepted schema-valid JSON findings; it **does not** mean the observations were true.
- `UNKNOWN` means the model may have run but the outcome or source is ambiguous (timeout, lost response, invalid model output, changed approval, changed digest). Do not resubmit the same operation to invoke the model; reconciliation or a distinct explicit operator decision is required.
- `EXECUTING` after a crash is converted to `UNKNOWN` when DesignRepository is reopened under its exclusive storage lock.
- Provider or model digest unavailable **before journal admission** returns an error without calling /api/chat; after admission any ambiguity is journaled UNKNOWN.
- `model_execution_attested=false`, `quality_verified=false`, `repair_authorized=false`, `release_ready=false` in every status, including empty findings.
- Status GET and idempotent POST replay now **strictly revalidate** the persisted
JSON before returning a completed shadow operation: findings must be a
bounded, schema-valid QualityFinding array; runtime telemetry must retain the
exact local, explicitly **unattested** schema. Duplicate/invalid JSON keys,
malformed or oversized findings, fabricated approval fields, claimed runtime
attestation and findings attached to `UNKNOWN`/`EXECUTING` all fail closed
with `409 VLM_JOURNAL_CORRUPT`. Journal state metadata is checked as well:
timestamps must be bounded, timezone-aware ISO values with finish not before
start; `EXECUTING` has no terminal reason/time, `EXECUTED` has no failure
reason, and `UNKNOWN` requires a bounded non-empty reason and finish time.
A corrupt record must not become an empty `EXECUTED` result or trigger another
model call. This validation cannot detect a self-consistent local database
rewrite and is **not** cryptographic attestation.

The SQLite journal, hashes, Ollama tags and adapter runtime fields are **local operator assertions**, not immutable third-party evidence, signed model execution or independently verified human judgment. This does not close OCR provenance Issue #14.

The request is limited to a 4 MiB PNG, one pinned model, at most 512 model output tokens, 20 findings, 32 KiB JSON response, and one local inference at a time. HTTP timeout is configured but is not an OS-level kill signal for a hung model process. Contain the Ollama service with host-level network and resource controls; a malicious local server can lie about its digest.


## Local execution telemetry

A successful `EXECUTED` result now retains a bounded `runtime.execution_metrics`
record alongside the explicitly unattested local runtime identity:

- `client_elapsed_ms`: monotonic client-side round-trip duration for the
  non-streaming `/api/chat` request and response body.
- `provider_total_duration_ns`, `provider_load_duration_ns`,
  `provider_prompt_eval_duration_ns`, and `provider_eval_duration_ns`:
  optional Ollama-reported nanosecond counters.
- `provider_prompt_eval_count` and `provider_eval_count`: optional
  Ollama-reported token counters.
- `output_token_limit`: the configured 512-token response cap.

Missing provider counters remain `null`, never synthetic zeroes. Provider
counters must be strict nonnegative integers within bounded ranges; malformed
values turn the admitted operation into `UNKNOWN`. Historical journal rows
with the original four-field runtime envelope remain readable; new rows
validate the additional metrics again on status lookup and idempotent replay.

These values are operator-local wall-time and provider-reported telemetry.
They are not independently measured model execution, a trusted receipt, or
proof that the model's own counters are accurate. No monetary cost is inferred
for local inference: estimate infrastructure cost separately by joining
elapsed time and token counts with host/GPU utilization and an operator-owned
rate card. Missing measurements must remain visibly missing.

## Verification

```bash
uv run --locked python -m pytest -q tests/test_design_vlm_shadow_runs.py tests/test_design_vlm_local.py
uv run --locked ruff format --check app tests scripts evals
uv run --locked ruff check app tests scripts evals
uv run --locked python -m pytest -q
```

Tests use a synthetic PNG and mocked local Ollama transport. They prove the integration contract and fail-closed outcomes, **not** actual model inference or predictive accuracy. A pinned local model and at least 20 independently labeled authorized SKUs remain future manual acceptance gates.

Offline operator-only shadow comparison now has a separate [calibration tool and frozen CSV contract](design-vlm-shadow-calibration.md). It does not attest a real model run or replace independent human acceptance. Next: a genuinely authorized, independently adjudicated cohort followed by human-confirmed, version-bound repair proposals and independent OCR/brand-rule rechecks.
