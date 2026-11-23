# Visual Critic shadow calibration — operator review, v1

This is an **offline read-only quality comparison**, not another VLM run, not a claim that the model genuinely executed, and not a publication gate. It follows the exact journaled Ollama shadow slice from PR #29. The tool does not call models, download weights, fetch URLs, modify designs, or create review records.

## Inputs

The operator first freezes one human cohort with exactly one DesignDocument visual operation per SKU. Start from the blank [CSV template](../evals/templates/design_vlm_shadow_labels_v1.csv). Do **not** commit customer data, authorization documents, product images, private paths, or completed human review CSV to GitHub.

Each row binds `sku_id` (a pseudonymous stable SKU identifier), `operation_id`, `document_id`, `revision`, `approval_id`, `svg_sha256`, `png_sha256` and `model_profile_sha256`. The last six category columns are exactly `contrast`, `spacing`, `visual_hierarchy`, `product_visibility`, `text_legibility`, `brand_style`. An operator must fill each with one of `PRESENT`, `ABSENT`, or `UNVERIFIED`:

- `PRESENT`: reviewer reports a defect in this category in the exact approved image
- `ABSENT`: reviewer has inspected the image and does not report a defect in this category
- `UNVERIFIED`: insufficient visual evidence or reviewer did not inspect this category; **never silently counted as ABSENT**

`reviewer_alias` is a local pseudonym, **not** authenticated identity. `source_authorization=DECLARED_AUTHORIZED` is an operator assertion that the source is licensed for this evaluation; the tool cannot verify it. Missing/invalid authorization declarations abort comparison, not treat them as acceptable. Use genuinely distinct, lawfully held SKUs; the tool can only check distinct strings.

All source identifiers must match the existing SQLite `design_vlm_runs` row exactly. The script rejects missing operations, duplicate SKU/operation IDs, mismatched approved SVG/PNG hashes, mismatched approval/revision, mixed model profile versions, invalid categories, malformed journal JSON, unexpected execution states and absent reviewer labels. For an `EXECUTED` row, its runtime JSON must have only the four local-only fields (`source`, `local_model_digest_claim`, `execution_evidence`, `attested`) with a syntactically valid SHA-256 digest claim and `attested=false`; duplicate keys, additional authority flags and malformed nesting abort evaluation. An `UNKNOWN`/`EXECUTING` row must contain neither findings nor runtime JSON. These checks validate journal structure only, not genuine model execution or independent attestation. The contract matches the online replay guard in PR #39. Never repair a historical journal row by fabricating a digest or attestation field; quarantine the inconsistent row and use a separately authorized new operation when applicable. To prevent output selection bias, write and freeze the intended sample cohort **before** reading metrics; the script cannot independently audit this selection.

## Run

Use the local application database while the application is quiescent or against a consistent SQLite backup. The evaluator issues read-only SQL with `mode=ro` and `PRAGMA query_only=ON`, and never instantiates the writer repository. SQLite may still maintain temporary journal coordination files depending on its journal mode. It prints a JSON report to stdout rather than saving a customer-bearing artifact.

```bash
uv run --locked python -m scripts.evaluate_design_vlm_shadow \
  --db /path/to/local/designs.sqlite3 \
  --labels /path/to/local/shadow_review.csv \
  > /path/to/private/shadow_calibration_report.json
```

The label CSV is opened as a single regular-file descriptor (a symlinked CSV is refused) and the read is capped at **2 MiB plus one sentinel byte** before parsing; no earlier file-size metadata is trusted to enforce the limit. This is a local input-safety control, not a guarantee that a concurrently writable file is frozen. For actual assessment, stage the cohort CSV and image evidence on read-only storage or take a controlled snapshot.

Keep the original CSV private, and retain the CSV SHA-256 and selected SQLite-row digest from the report to detect ordinary replay drift. Those are **content fingerprints**, not signatures or proof that humans looked at images.

## Metrics and denominator

Each SKU contributes at most one operation. Model findings are de-duplicated **by category within one approved image** (not region-level localization). For each category, the report shows true-positive/false-positive/false-negative/true-negative counts, precision, recall and false-positive rate, plus a separate count of human `UNVERIFIED` labels and non-executed journaled samples. Missing predictions from `UNKNOWN` or `EXECUTING` are **not** interpreted as clean images or false negatives. `None` means the relevant denominator is zero.

Adapter journal wall elapsed time is summarized as nearest-rank P50/P95 for executed records; it is not isolated VLM compute time, TTFT, GPU utilization, token throughput, or external provider billing. All timings are local operator-side telemetry.

At least **20 distinct fully labeled, journal-EXECUTED** SKU operations, and no incomplete samples, are necessary for `OPERATOR_CALIBRATION_ONLY_NOT_ATTESTED`. Smaller, partially reviewed or ambiguous cohorts remain `INSUFFICIENT_OPERATOR_CALIBRATION`. Neither status means production quality is verified. Categories with no positive human examples have undefined recall and cannot be claimed as validated despite 20 SKUs.

Every report has `model_execution_attested=false`, `source_authorizations_verified=false`, `independent_human_review_attested=false`, `quality_verified=false`, `repair_authorized=false`, and `release_ready=false`. The script omits SKU IDs, reviewer aliases, raw observations, texts and filesystem paths from stdout.

## Reproduce the synthetic checks

```bash
uv run --locked python -m pytest -q tests/test_design_vlm_calibration.py
uv run --locked ruff check app scripts evals tests
uv run --locked ruff format --check app scripts evals tests
```

These are synthetic regression tests only; no actual SKU review, independently verified cohort, human adjudication, genuine Ollama inference or model-improvement outcome is established by CI.

Next step: collect a permissioned, frozen and independently adjudicated set of at least 20 distinct SKUs; report category distribution, false positives and coverage; later compare pinned model versions under the same frozen cohort. Issue #28 remains open until such real evidence is obtained. Controlled Repair Planner and post-edit OCR checks are separate work.
