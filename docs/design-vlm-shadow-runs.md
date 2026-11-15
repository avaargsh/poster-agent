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

Set both environment variables before starting the server; they are frozen at lifespan startup. A configured model name alone never enables execution. The model/digest pair is checked against the **local server's claimed model listing** before and after the invocation; this does not independently prove weights or provider identity.

## Explicit operation

1. Create the DesignDocument and obtain an explicit approval of the current revision.
2. With the named export credential, GET `/api/designs/{id}/revisions/{revision}/approved.png`. Save the `X-Approved-SVG-SHA256` and `X-Raster-PNG-SHA256` headers.
3. Generate one operation ID as `vlm_` plus 32 lowercase random hexadecimal digits (e.g. UUIDv4 hex). POST `/api/designs/{id}/revisions/{revision}/visual-runs` with:

```json
{"operation_id":"vlm_0123456789abcdef0123456789abcdef","svg_sha256":"<64 hex>","png_sha256":"<64 hex>"}
```

4. GET `/api/designs/{id}/revisions/{revision}/visual-runs/{operation_id}` to inspect the journal. This lookup does not contact the model, even if inference is disabled after startup.

Every POST checks the pinned profile, principal/policy, current approval, source asset/SVG hashes, approved pixel hash and revision. A persistent SQLite row is committed **before** model inference. Duplicate operation IDs with identical input replay the journal result; changed input conflicts. One model call may execute per process at a time; other distinct operations receive a busy response and should not be auto-queued.

The response is content-bounded, uses JSON-only strict QualityFinding validation, and rejects unknown fields, malformed regions, claims about release/repair, oversized responses and changed reported model. The model is instructed to report only soft visual defects such as layout, hierarchy, spacing and contrast. It is never instructed to edit or regenerate prices, SKU, CTA or Logo.

## Failure, recovery and evidence

- `EXECUTED` means the local adapter accepted schema-valid JSON findings; it **does not** mean the observations were true.
- `UNKNOWN` means the model may have run but the outcome or source is ambiguous (timeout, lost response, invalid model output, changed approval, changed digest). Do not resubmit the same operation to invoke the model; reconciliation or a distinct explicit operator decision is required.
- `EXECUTING` after a crash is converted to `UNKNOWN` when DesignRepository is reopened under its exclusive storage lock.
- Provider or model digest unavailable **before journal admission** returns an error without calling /api/chat; after admission any ambiguity is journaled UNKNOWN.
- `model_execution_attested=false`, `quality_verified=false`, `repair_authorized=false`, `release_ready=false` in every status, including empty findings.
- The SQLite journal, hashes, Ollama tags and adapter runtime fields are **local operator assertions**, not immutable third-party evidence, signed model execution or independently verified human judgment. This does not close OCR provenance Issue #14.

The request is limited to a 4 MiB PNG, one pinned model, at most 512 model output tokens, 20 findings, 32 KiB JSON response, and one local inference at a time. HTTP timeout is configured but is not an OS-level kill signal for a hung model process. Contain the Ollama service with host-level network and resource controls; a malicious local server can lie about its digest.

## Verification

```bash
uv run --locked python -m pytest -q tests/test_design_vlm_shadow_runs.py tests/test_design_vlm_local.py
uv run --locked ruff format --check app tests scripts evals
uv run --locked ruff check app tests scripts evals
uv run --locked python -m pytest -q
```

Tests use a synthetic PNG and mocked local Ollama transport. They prove the integration contract and fail-closed outcomes, **not** actual model inference or predictive accuracy. A pinned local model and at least 20 independently labeled authorized SKUs remain future manual acceptance gates.

Next: shadow evaluation against human labels (false positives, latency, per-category sensitivity), then human-confirmed, version-bound repair proposals and independent OCR/brand-rule rechecks. Neither stage is implemented by this PR.
