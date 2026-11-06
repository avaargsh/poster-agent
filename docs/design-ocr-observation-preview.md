# Design OCR observation comparison preview (untrusted)

This endpoint compares **caller-supplied** OCR text observations with the
approved DesignDocument's exact current OCR target. It **does not execute OCR**
or certify that any particular image, model or runtime produced the lines.

`POST /api/designs/{document_id}/revisions/{revision}/ocr-evaluation-preview`

Request example:

```json
{
  "lines": [
    {"text": "双十一牙刷", "score": 0.97},
    {"text": "¥199.90", "score": 0.96}
  ]
}
```

The request model rejects unknown fields, invalid scores, empty lines,
unsafe control characters and more than 200 entries. Each line's text is
limited to 200 characters. Scores below 0.40 do **not** contribute to a
positive field match. No request content is persisted by this endpoint.

The server first revalidates the same current approval, reviewer export scope,
renderer, asset hashes and SVG digest as `ocr-target`. Stale revisions,
changed policy, corrupt assets and missing approvals fail closed.

The report includes the exact approved input's `approval_id`,
`spec_digest`, `asset_manifest_digest`, `svg_sha256`, `expected_fields`
and `missing_fields`, plus deterministic checks for title, price and CTA:

- `MATCHED`: a declared observation matches the expected field;
- `MISMATCH`: eligible text exists but does not match;
- `NO_OBSERVATION`: no observation clears the minimum score;
- `MISSING_IN_DESIGN`: the field is absent from the approved design.

Each check includes `matched_line_index`: the zero-based index in the
**original request** of the first eligible matching line. It is `null` for
anything other than `MATCHED`. This does **not** check the visual location of
text, and one declared line may match more than one expected field.

Title/CTA use whitespace-normalized complete-line equality. Price matching
accepts a fully delimited displayed amount but rejects prefix collisions such
as `¥199.90` vs `¥1990.00` or `¥199.90.5`. These checks are deterministic
hints, not proof that a visual field is readable. Real OCR could miss a field
that was rendered, and callers could fabricate matching text.

## Reproducible comparison fingerprints (schema version 1)

Two SHA-256 fingerprints can assist downstream **external** evidence storage
in identifying exactly which declared input and comparison output it received:

- `observations_sha256`: hash of the validated, original-order `lines`
  request encoded as canonical UTF-8 JSON;
- `comparison_fingerprint_sha256`: hash of the canonical UTF-8 JSON record:
  `{"schema_version":1,"target":<ocr-target dict>,"observations":<validated request>,"field_checks":<checks>,"declared_consistency":<status>}`.

Canonical JSON uses sorted keys, compact separators, UTF-8 without ASCII
escaping, and rejects NaN. It includes original text/score, not only
whitespace-normalized text, so a changed observation score invalidates the
fingerprint even when the three field statuses remain the same. A changed
approved SVG hash likewise changes the comparison fingerprint.

These are **ordinary content hashes** computed by the same server returning
the response. They are neither a signature nor immutable storage, identity
verification, independent witness, or proof of genuine OCR. An attacker can
forge both the input and matching hashes. To obtain audit-grade provenance,
a separately authenticated execution service must archive and anchor its
signed receipt and frozen raster/model evidence in an independently controlled
append-only store.

**Every response explicitly includes**
`ocr_status=OBSERVATIONS_UNATTESTED`,
`runtime_attested=false`, `quality_verified=false`,
`release_ready=false`, even if all declared fields match. The response
contains `declared_consistency` as `MATCHES_EXPECTED` only when *all three*
fields match; otherwise `BLOCKED`. It never grants an export or publish
permit. Existing DesignDocument templates without CTA remain blocked.

## Still required before OCR acceptance or release

A trusted server-side SVG rasterizer with frozen PNG bytes/hash, pinned OCR
engine/model configuration, structured engine telemetry, immutable external
evidence, calibrated OCR confidence and human reviewer approval, bound to the
same design revision and image hashes. Genuine runtime attestation and
fail-closed release checks cannot be replaced by this preview endpoint.
