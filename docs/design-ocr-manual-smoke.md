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
approved PNG hash and persists the operation request before invoking OCR.
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

## Remaining #57 work

Record exact server commit/environment/font/model identity separately;
prove isolated server startup and egress policy; implement the model/asset source-drift negative probe; execute the real pinned smoke. A 20-authorized-SKU
human evaluation remains #14, and is not replaced by this client.
