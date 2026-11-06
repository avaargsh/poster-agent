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

## M6 offline replay-consistency guard (still **unattested**)

The optional `replay_consistent_unattested(authoritative_target, observations, report)`
helper checks that the **entire** supplied report is identical to a deterministic
recomputation from a separately obtained, already validated current approved OCR
target and the complete original caller observations. It rejects missing/extra
fields, changed match positions or digests, modified execution/quality/release
flags and JSON type substitutions (for example `true` in place of revision `1`).
It does **not** contact an OCR service, authenticate the submitted observations,
store immutable evidence or grant a release permit. An untrusted party can
fabricate a matching target, observations and report.

Consumers MUST independently re-check the revision approval/export scope and
retain the original request alongside the report. The v1
`comparison_fingerprint_sha256` covers the target, input lines, field checks and
`declared_consistency`, **not** the final report's four unattested/release
status fields. Therefore a matching hash alone must never authorize release;
require the full-report replay guard **and** explicit fail-closed checks for
`OBSERVATIONS_UNATTESTED` / all three `false` flags. This is local content
consistency, not trusted provenance. Tests freeze the schema-v1 UTF-8 JSON
fingerprint golden vector and cover mutation of revision, approval, renderer,
spec/asset/SVG digests, observation score, line positions and status flags.

The score threshold applies to the **whole declared line collection**, not
independently proven fields: a low-score title line plus a high-score price
line can produce `MISMATCH` for the title. Read it strictly as "not matched
among globally eligible declared lines", never as proof the rendered title
was wrong. No `MISMATCH`, `NO_OBSERVATION` or `MATCHED` here is a visual
OCR finding. Any later field-level confidence taxonomy needs a separately
versioned contract with genuine line/field attribution.

This v1 JSON profile uses Python `json.dumps` sorted keys, compact delimiters,
UTF-8, `ensure_ascii=False` and `allow_nan=False`; it is **not** an assertion
of RFC 8785/JCS interoperability. Cross-language consumers must check frozen
golden vectors rather than assume equivalent numeric encodings.

## Still required before OCR acceptance or release

The separately protected `approved.png` raster-source endpoint (PR #15)
provides bounded SVG-to-PNG conversion and pixel hashes but not genuine OCR,
independent attestation, or release authority. Remaining gates include verified
runtime use of the frozen PNG, pinned OCR model/configuration, structured execution
telemetry, an idempotent operation journal with UNKNOWN reconciliation, authenticated
receipts, independent immutable evidence anchoring, calibrated field/spatial
checks and an attributed human acceptance review on authorized real SKUs.
Synthetic comparisons and raster tests cannot substitute for these gates.
