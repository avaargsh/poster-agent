# Operator source drift rejection probe

Use only an isolated disposable local server with a synthetic design and
private copies of assets and models. This probe performs GET requests only.
It does not change model weights, assets, approval, or journal state.

Before changing a source, capture an EXECUTED operation's evidence bundle
and retain its canonical digest separately using the documented journal
and evidence bundle endpoints. Keep the design at its approved revision.
Use the [manual smoke client](design-ocr-manual-smoke.md) with
`--keep-approved` to retain the approved revision, evidence.json and separate
digest from one OCR operation. The default smoke advances the revision;
its already stale design cannot be used for this probe.

An operator must create and document the intended drift independently in
the disposable environment, without running another OCR request. Do not
modify production models or assets; keep operational model mounts immutable.
Use the same export principal and select one expected rejection:

- model: 503 OCR_LOCAL_MODEL_PROFILE_UNAVAILABLE
- asset: 409 ASSET_INVALID

```sh
uv run --no-sync python -m scripts.probe_ocr_source_drift \
  --confirm-isolated-server \
  --bundle /private/baseline.json \
  --expected-sha256 <separately-retained-canonical-digest> \
  --drift model \
  --output /private/new-drift-report.json
```

The exporter token comes from POSTER_SMOKE_EXPORTER_TOKEN. The probe verifies
the bounded baseline in memory, reads the historical journal, demands the
exact expected HTTP status and domain error from both comparison-preview
and evidence-bundle, then reads the journal again. It refuses a changed
journal, successful export or unrelated error; no new OCR POST is issued.
The report is create-only, mode 0600. Success records only observed refusal
and journal equality, not proof of the external mutation, genuine model
execution, server isolation, independent attestation or quality acceptance.
All authority flags remain false. Synthetic tests cover both drift types and
a no-drift refusal. Actual operator evidence remains outstanding.
