# DesignDocument exact-revision OCR target (input only)

`GET /api/designs/{document_id}/revisions/{revision}/ocr-target` computes
an OCR **input target**, not an OCR result. It uses the same exact current
DesignDocument approval, local reviewer `export` scope, persisted asset integrity
checks, and renderer contract as the protected SVG export endpoint.

The target's fields are computed while holding one repository SQLite transaction,
preventing a concurrent design revision from mixing the approval of one head
with the rendered SVG hash of another:

- `document_id`, exact `revision` and persisted `approval_id`
- `spec_digest` and `asset_manifest_digest` from the stored design
- `renderer_version`
- `svg_sha256` over UTF-8 bytes of the **actual currently approved SVG**
- `expected_fields`: visible, nonempty title/price/CTA text actually present
  in the DesignDocument, **not** guessed from Brief defaults
- `missing_fields`: critical fields absent or hidden in the rendered design
- `ocr_status=NOT_RUN`, `runtime_attested=false`,
  `quality_verified=false`, `release_ready=false`

The current template renders title and price but **does not contain a CTA
text node**. Consequently, the target reports `missing_fields=["cta"]`.
It must not fill that gap with an imagined “立即抢购” label.

HTTP responses specify `Cache-Control: no-store` and
`X-Content-Type-Options: nosniff`. Missing approvals, stale revisions,
corrupt referenced assets, unauthorized export and policy rotation fail closed.

## Future OCR receipt and release gate (not implemented)

A real OCR acceptance receipt must attest what was **actually analyzed**:
render the same approved SVG into a frozen image, record the raster bytes
SHA-256, OCR engine/version and model/configuration digests, observed line
text/scores/locations, per-field exact-versus-normalized comparisons, and the
original DesignDocument revision/spec/asset/renderer/SVG hashes.

Missing OCR runtime, missing CTA, bad/conflicting price, stale revision,
corrupt assets or any missing receipt must block release. OCR score alone is
not identity proof; an external agent's claim that it ran OCR is not a
trusted execution attestation. A reviewer identity and independent
human confirmation must remain separate from automated OCR evidence.

This PR does not rasterize SVG, run PaddleOCR or certify OCR execution. It
only creates the immutable *input description* necessary for a safe
subsequent receipt and release gate.
