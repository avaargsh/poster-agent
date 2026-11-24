# Manual DesignDocument OCR smoke client (operator-run)

This is the first implementation slice of #57. It exercises the actual local
HTTP routes, not the old synthetic preview OCR probe. It does not start,
provision, attest or isolate the backend. Never point it at a populated
customer instance.

## Prerequisites

Use a single-worker loopback service with a new isolated database, assets,
checkpoint and execution stores. Follow
[operator-pinned model setup](design-ocr-offline-model-pins.md),
[approved raster setup](design-approved-raster.md), and the
[OCR journal](design-ocr-operation-journal.md). Provide the models, Chinese
fonts, CairoSVG, PaddlePaddle and PaddleOCR locally, and enforce read-only
model mounts and outbound network denial outside the application.
Use two distinct named approve/export reviewers. Supply their raw tokens
through the private environment variables POSTER_SMOKE_APPROVER_TOKEN and
POSTER_SMOKE_EXPORTER_TOKEN; do not put them in shell history, command-line
arguments, GitHub or logs.

From the locked project environment, after #54–#56 are available:

```sh
uv run --no-sync python -m scripts.run_design_ocr_smoke \
  --run-model --confirm-isolated-server \
  --origin http://127.0.0.1:8000 \
  --output-dir /private/existing-parent/new-smoke-directory \
  --digest-file /private/separate-existing-parent/new-digest.txt
```

The output directory and digest file must not exist. The client accepts
only numeric IPv4 loopback, disables proxies/redirects and transport retries,
creates a programmatic synthetic product, approves one design, checks the
approved raster's PNG media type, byte digest and complete decoded PNG content
against its response headers, and persists the operation request only after
these checks. The client verifies PNG structure and pixel decoding, requires
a single non-animated frame and IEND at the end of the response (no appended
payload), and enforces the server's 24 MiB, 4096px dimension and
8-million-pixel limits. A matching server-supplied digest or PNG signature
alone is insufficient: truncated, animated, corrupt or appended raster bytes
fail at the raster stage with NOT_SUBMITTED, and no OCR operation is sent.
The approval response is retained as a whitelisted `approval.json`
record containing the exact revision/digests, server-attributed approver,
policy digest and replay state. The OCR journal must retain the same revision
and renderer version, and identify a different export principal. Raw bearer
tokens and authorization headers are never written to the evidence directory.

Only a successful, identity-bound EXECUTED response with observations,
boxes and timing permits one exact-ID replay, followed by bundle export
and local verifier replay. After successful verification, the client changes
only the title of its newly created synthetic design and requires the old
revision's export to return HTTP 409 REVISION_CONFLICT. It saves the rejection
and retains the historic evidence digest only after that probe succeeds. No
additional OCR invocation is made. Non-success or a lost submission response
halts without retrying. Duplicate JSON keys, non-finite values and malformed
observation entries are refused. Before replay, the client reuses bounded
journal observation and runtime validators, requires exact typed revision
identity and checks the approval spec and asset digests. Scores outside 0–1
and invalid inference timings are refused. A malformed submission response retains
UNKNOWN rather than recording successful execution; replay and bundle journal
comparisons use canonical JSON to distinguish booleans from numbers. Inspect outcome.json and the saved source/operation
request; reconcile the same operation through the journal status GET
before deciding whether a new operation is warranted.

The digest written to a separate local file is a retained local content
anchor, not an independently authenticated digest. Preserve it through
a separate controlled channel before subsequent verification. Do not
commit artifacts or credentials. The success status
LOCAL_SMOKE_CONSISTENT_UNATTESTED does not imply genuine runtime attestation,
quality acceptance or a release permit. Synthetic unit tests substitute
the adapter and rasterizer; an actual operator run is separate evidence.

## Reuse the approved baseline for source drift probes

Add `--keep-approved` to the command above to retain the exact approved
revision for the [source drift probe](design-ocr-source-drift-probe.md).
The client still submits one OCR operation and verifies its replay and
bundle, but does not mutate the synthetic title or run the stale-revision
check. It retains the digest and reports
`LOCAL_BASELINE_CONSISTENT_UNATTESTED`, with
`stale_revision_rejected=false` and all authority flags false.
This is a partial smoke result, not completion of #57.
Use only disposable state and independently controlled copies of the
sources for drift testing. Run the default stale-revision smoke separately
if needed; never repeat an ambiguous operation.

## Bind the environment manifest to the executed journal

Run the manifest collector from the same locked Python environment and clean
checkout that will start the isolated server. Set
`POSTER_DESIGN_OCR_MODEL_CONFIG_JSON` to the exact pinned local model profile.
Use one to eight absolute paths to the installed font files; the collector
records their byte digests but does not prove which font CairoSVG selected.
Write the manifest outside the checkout:

```sh
uv run --no-sync python -m scripts.collect_ocr_runtime_manifest \
  --repo "$PWD" \
  --font /absolute/path/to/selected-cjk-font.ttc \
  --output /private/ocr-runtime-manifest.json
```

Before starting the server, retain the exact checkout SHA outside the
repository (the manifest collector independently requires a clean checkout):

```sh
git rev-parse HEAD > /private/server-checkout.sha
```

After a successful smoke, bind that manifest to the verified evidence bundle.
Supply the previously retained checkout SHA and the bundle digest retained
through the separate channel:

```sh
uv run --no-sync python -m scripts.verify_design_ocr_smoke_manifest \
  --manifest /private/ocr-runtime-manifest.json \
  --bundle /private/smoke-directory/evidence.json \
  --expected-bundle-sha256 "$(cat /private/separate-digest-parent/bundle.sha256)" \
  --expected-git-commit "$(cat /private/server-checkout.sha)" \
  --output /private/ocr-manifest-binding.json
```

The verifier requires exact agreement for the checkout assertion, renderer
version, model profile digest, Python version, machine, PaddleOCR/PaddlePaddle
package versions, CairoSVG rasterizer identity and recorded font identities.
A mismatch fails closed. Its report is still only local consistency: the operator-supplied
commit is not proof of the server process image, package telemetry is not
independent runtime attestation, font file hashes do not prove font selection,
and neither this command nor the manifest proves OS-level egress denial.
The report deliberately keeps `server_process_verified`,
`font_selection_verified`, `egress_denied_verified`, `runtime_attested`,
`quality_verified` and `release_ready` false.

## Remaining #57 work

Prove isolated server startup and OS-level egress policy; execute the existing
model/asset source-drift negative probes; execute the real pinned smoke. A
20-authorized-SKU human evaluation remains #14, and is not replaced by this
client or the manifest binding report.


## Approval attribution verification boundary

For schema v2, the offline verifier's `approval_attribution_consistent` field means only that the bundled approval record is structurally valid and internally matches the OCR journal's document, revision, approval ID, spec digest, asset digest and renderer identity, with distinct approver/exporter principals. The approver and exporter policy digests must match because both actions are evaluated against the same configured reviewer-policy snapshot. It does **not** independently query the live approval database, authenticate a human reviewer, validate a signed timestamp, or establish tamper-evident audit history. The `is_current_head` and `matches_current_snapshot` values are server-derived local database assertions captured at export time; the offline verifier can only check their included values. See [design approval history](design-approval-history.md) for the SQLite evidence limitations. Legacy schema v1 bundles report `approval_attribution_consistent=false`.
