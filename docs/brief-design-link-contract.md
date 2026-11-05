# Brief Review Run → DesignDocument: local provenance v0.1

## Authority and link semantics

`POST /api/designs/{document_id}/brief-link` accepts
`{"brief_run_id": "…"}` only when the authoritative LangGraph Brief
checkpoint currently reports `APPROVED`, has no pending approval, and
contains a parseable `PosterSpec`. The server hashes the normalized confirmed
specification, then writes a one-time link to an existing **revision 1**
DesignDocument. Exact replays return 200; conflicting run IDs or digests fail
409. A design edited before first binding cannot be retroactively linked.

`GET /api/designs/{document_id}/brief-link` returns the persisted run ID,
SHA-256 digest of normalized confirmed PosterSpec, original linked revision,
and server timestamp. Later design revisions retain provenance but **do not**
automatically inherit Brief approval or exact semantic equivalence.

The Brief checkpoint lives in LangGraph SQLite and the DesignDocument in
another SQLite database. These are **not one atomic cross-database transaction**.
A checkpoint read followed by a design write is a best-effort local provenance
link, not a cryptographically signed, cross-store attestation. The approved
Brief checkpoint is immutable under the current review API, but external DB
tampering or administrative replacement is out of scope.

## Credential boundary

Both endpoints fail closed with HTTP 401 unless
`POSTER_BRIEF_LINK_TOKEN` is set to a secret of at least 32 characters and
the request sends `Authorization: Bearer <secret>`. The token is checked with
constant-time comparison. Keep it out of frontend bundles and logs. This is a
**single shared local operator credential**, not human identity authentication,
RBAC, OIDC, mTLS, approval attribution or multi-tenant authorization. The
existing loopback-only HTTP middleware remains mandatory.

## What this does NOT authorize

A Brief link does not approve a DesignDocument, does not grant approved SVG
export, and does not certify OCR correctness. Revision-bound design approval
and asset-byte verification remain separate gates. Existing approval history
records are local evidence, not tamper-evident audit logs.

## Next acceptance contracts (not implemented by this PR)

1. **Identity and audit**: authenticated reviewer subject, roles, authorization
   policy version, signed append-only evidence receipt, correlation ID,
   idempotency key and storage/restore proof; never accept arbitrary
   `X-Actor` headers as identity.
2. **OCR**: render the exact approved revision; freeze SVG/PNG output digest,
   OCR engine/version, extracted text boxes, expected business fields and
   confidence thresholds; require price/title/CTA match, record false-positive
   and false-negative samples, fail closed on missing/unreadable evidence.
3. **Release gate**: bind OCR acceptance and reviewer evidence to the same
   revision, renderer version and asset manifest digest, then revalidate before
   publish/export. UNKNOWN external side effects require reconciliation,
   never blind retries.
4. **Cross-store integrity**: use a single transactional authority or a
   durable outbox/inbox protocol with reconciliation; detect stale or missing
   checkpoints and record the exact verification time.

These are future gates; they must not be inferred from a passing unit test.
