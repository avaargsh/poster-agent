# Journaled OCR comparison preview (read-only, not an attestation)

The local DesignDocument OCR journal records a bounded, opt-in attempt with
an `EXECUTED`, `UNKNOWN`, or `UNAVAILABLE` state. **EXECUTED does not mean
the result is correct, independently verified or publishable.**

`GET /api/designs/{document_id}/revisions/{revision}/ocr-runs/{operation_id}/comparison-preview`

This route reads only the **server-persisted** observations for a completed
operation; it takes no OCR text from the HTTP caller and never reruns the
engine or mutates the journal. It requires the same named local reviewer
`export` scope and exact policy digest as the original run. Its checks:

1. The journal row belongs to this document, revision, principal and policy,
   and its status is exactly `EXECUTED`.
2. The current approved design still has the same approval ID, specification,
   asset manifest, renderer version and SVG SHA-256 as the journal record.
3. The current local pinned OCR model bundle still matches its recorded
   profile SHA-256. This is a **local file check**, not model authenticity.
4. The approved SVG is rasterized again; the resulting SVG/PNG SHA-256 and
   rasterizer label must match the journaled input. The current design head is
   reread afterward to reject a concurrent revision change.
5. The persisted OCR observations are validated as bounded text and scores,
   then compared with the exact current title, price and CTA using the same
   deterministic, non-attesting comparison contract as the OCR observation
   preview.
6. Separately, each text-matched field's **original journaled** `bbox` is
   checked for integer `[left, top, right, bottom]` coordinates, positive
   area, and containment within the exact re-rendered PNG dimensions. Missing
   or schema-valid degenerate/out-of-raster boxes yield `BLOCKED`. Corrupted
   non-integer, non-finite or otherwise invalid stored OCR rows fail closed with
   `409 OCR_SOURCE_MISMATCH` before comparison/fingerprinting.
   All present and in-bounds boxes yield `BOUNDS_ONLY_UNCALIBRATED`, **not**
   a confirmed title/price/CTA position: expected zones have not been calibrated.

The v1 `declared_consistency` and `observations_sha256` fields are
**text-only** and remain unchanged for compatibility. New read-only fields:
`spatial_evidence_status`, `spatial_field_checks`,
`journaled_observations_sha256` (full stored lines, including bboxes),
`spatial_comparison_schema_version=1`, `spatial_source_sha256`
(content identity of approved document/revision, approval, spec, assets, PNG/SVG
digests, rasterizer, pinned model profile, operation, principal and policy), and
`spatial_comparison_fingerprint_sha256` (schema, source identity, full
original-line digest, canvas dimensions, text matches and spatial findings).
Malformed persisted observation/runtime JSON fails closed with
`409 OCR_SOURCE_MISMATCH` rather than an unhandled server response. These
hashes are local, forgeable content identities, not signed independent receipts.
Even `BOUNDS_ONLY_UNCALIBRATED` cannot infer correct field placement:
an unrelated text box can lie within canvas bounds.


## Approved DesignDocument node-envelope hints (diagnostic v1)

The comparison preview also derives a separate **read-only layout hint**
from the exact current DesignDocument revision. The loaded document's
specification and asset-manifest digests must match the approved snapshot;
its page dimensions must match the re-rendered raster. The source is
rechecked after this read. Drift returns `409 OCR_SOURCE_MISMATCH`.

For title, price and CTA, `design_node_overlap_checks` compare each
text-matched journaled OCR rectangle with the corresponding **visible,
unrotated DesignDocument text-node authoring envelope**. Missing/invalid
OCR boxes, missing or hidden text nodes, rotated/out-of-canvas node
envelopes, or SVG text baselines outside their node envelope fail closed.
Nonzero intersection is reported only as `OVERLAP_UNCALIBRATED`,
with a deterministic overlap fraction of the OCR rectangle's area.
Disjoint rectangles report `OUTSIDE_DESIGN_NODE`. This is *not*
a measurement of the actual rendered glyph bounds, a calibrated zone
classifier, or a claim of correct field placement. Even complete overlap
never authorizes editing or publishing.

New fields are `design_node_overlap_status` (`BLOCKED` or
`OVERLAP_ONLY_UNCALIBRATED`), `design_node_overlap_checks`,
`design_node_overlap_schema_version=1`, and
`design_node_overlap_fingerprint_sha256`. The latter binds the findings
to the already validated spatial source digest, canvas and version.
Existing text-only and spatial-v1 fingerprints remain unchanged.

This code and synthetic tests cannot prove that a genuine PaddleOCR model
ran, that its geometry matches text pixels, or that a reviewer approved it.
Real model execution, calibrated 20-SKU labels and independently anchored
runtime evidence remain separate #14 gates.

`UNKNOWN`/`UNAVAILABLE` => `409 OCR_RUN_NOT_EXECUTED`.
Changed source, model identity or raster => `409 OCR_SOURCE_MISMATCH`,
or `503` for unavailable pinned model/rasterizer. Stale revision or
revoked approval fails closed. Nonexistent/other-principal operation IDs
are not disclosed.

Even when all fields match, the result explicitly has
`ocr_status=OBSERVATIONS_UNATTESTED`,
`source_state=CURRENT_EXECUTED_UNATTESTED`,
`runtime_attested=false`, `quality_verified=false`, and
`release_ready=false`. No authority treats it as an externally signed
runtime receipt, immutable audit log, human approval, or release permit.

Before returning, the comparison rehashes the pinned model profile and rereads
the authorized journal record, then rechecks the current approved design.
Changes during rasterization or layout assessment fail closed; the operation
is never re-executed. These sequential local checks do not establish an atomic
snapshot across the filesystem and SQLite or independent attestation.

The read-time check cannot prevent a *future* design revision immediately
after the response, and a database administrator can rewrite local records.
Production acceptance still requires a separate trusted evidence authority,
independent model/runtime provenance, human review and a final atomic
release-time guard. This route is local diagnostic evidence only.


## Local evidence bundle export (v1)

`GET /api/designs/{document_id}/revisions/{revision}/ocr-runs/{operation_id}/evidence-bundle`

This explicit, read-only download uses the same named export principal and
policy, completed operation, exact approved revision and postflight model /
journal / design checks as comparison-preview. It does not invoke OCR or
write any state. Missing, stale, non-executed or inconsistent operations
are rejected. The response is a private JSON attachment, with no-store.

The version-1 object contains `evidence_kind=LOCAL_OCR_UNATTESTED`, the
canvas dimensions, base64 PNG bytes from the very same verified raster
used by comparison (bounded to 24 MiB before encoding), policy digest,
local journal record (including observations and runtime metadata), and
comparison report. It omits raw tokens, model directory paths and SVG source
bytes. Treat it as customer-sensitive material; do not commit it to GitHub.

`bundle_sha256` is SHA-256 of UTF-8 canonical JSON of the whole object
**excluding that field** (Python json.dumps with ensure_ascii=False,
sort_keys=True, separators=(",", ":"), allow_nan=False). Decode PNG base64
strictly and compare its SHA-256 with the journal and comparison PNG hashes.
An externally retained expected bundle digest can detect later byte/content
changes; a digest shipped inside the same bundle is not authentication.

This is an export slice, not a complete independent verifier: it does not
include SVG bytes, model files, signed runtime claims or human labels.
Repeated export of unchanged inputs has the same digest. All trust flags
remain false. A successful export is neither proof of genuine execution
nor quality acceptance nor a permit to publish.
