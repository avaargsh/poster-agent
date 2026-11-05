# Design approval history (local evidence)

The read-only `GET /api/designs/{document_id}/approvals` endpoint lists all
persisted local approval records, including superseded revisions and renderer
versions. Entries are ordered by revision, creation timestamp and approval ID.
Each entry includes the exact recorded digests and timestamp, plus two
**derived** fields:

- `is_current_head`: this record belongs to the currently selected design
  revision, regardless of renderer version.
- `matches_current_snapshot`: its revision, renderer version and recorded
  digests match the currently stored design. **This is not export readiness**;
  protected SVG export separately revalidates actual asset bytes.

Historical approvals are never deleted or upgraded to authorize new revisions.
A renderer upgrade leaves old evidence visible but not matching the new
renderer. Idempotent approval retries do not create additional history rows.

This is **not** a tamper-evident append-only external audit log: the SQLite
database administrator can alter or delete records. It does not establish an
authenticated human reviewer, identity, role, timestamp attestation, or
EngageGuard authorization. The local service must not be exposed to untrusted
clients. No Brief Review Run linkage is implied.

Acceptance: full CI, plus tests for missing documents, empty history, retries,
revision changes, renderer upgrades and process restart. Production identity,
immutable evidence and audit export require a separate contract.
