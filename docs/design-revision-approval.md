# DesignDocument exact-revision approval (local gate)

This capability is a **local explicit operator acknowledgement** bound to
the persisted design document, **not** an authenticated approval identity,
remote authorization, EngageGuard Permit, or proof of human identity. The
service must remain loopback-only / trusted local. It does not make the
legacy poster generation endpoint or draft preview a protected output path.

## State model

`DesignRepository` is the single authoritative writer of revisions and
approval evidence. A new additive `design_approvals` table records one
immutable approval per `(document_id, revision, renderer_version)`, with a caller-provided
stable `approval_id` for idempotency.

An approval contains four binding fields:

- **document_id**: the authoritative design document, never inferred from
  client-supplied content.
- **revision**: the exact current SQLite head (optimistic CAS under
  `BEGIN IMMEDIATE`); undo/redo create new revisions.
- **spec_digest**: SHA-256 of deterministic JSON of all design fields other
  than asset references, plus the current `RENDERER_VERSION`. Includes
  `document_id` and revision, price facts, nodes, page, fonts and source.
- **asset_manifest_digest**: SHA-256 of deterministic JSON of sorted
  `asset_refs`, `document_id` and revision. Asset bytes are independently
  checked against their SHA-256 index on approval and on export.

Approval and design command mutations serialize in the same repository SQLite
transaction. Editing does **not** erase historical approval records but
the current head is unapproved until explicitly reviewed again. Even an undo
that restores visually identical content does not resurrect a prior approval.
If the renderer version changes, the old row remains as historical evidence,
but a fresh approval ID can authorize the same revision under the new renderer
version; the old approval ID cannot be recycled.

## HTTP workflow

1. Create or edit `DesignDocument` using existing APIs.
2. Read `GET /api/designs/{document_id}/approval-target`. This returns
   `revision`, the two server-computed digests, renderer version,
   `approved` and an optional current `approval_id`.
3. After reviewing the exact content, explicitly POST
   `/api/designs/{document_id}/approvals` with `approval_id`,
   `expected_revision`, `spec_digest`, `asset_manifest_digest` from
   that same target. The first accepted request returns HTTP 201;
   an exact retry returns HTTP 200 with `replayed=true`.
4. Export via
   `GET /api/designs/{document_id}/revisions/{revision}/approved.svg`.
   This endpoint serves only a currently approved head with fresh asset
   integrity verification. Historical and unapproved revisions return 409.
   `preview.svg` remains a draft/untrusted preview and is not an approval.

These new routes are API-only; no frontend approval UI or authenticated
reviewer identity is included in this PR.

## Negative cases

- `REVISION_CONFLICT` (409): stale approval or export revision.
- `APPROVAL_DIGEST_CONFLICT` (409): submitted content/manifest digest is
  not the currently stored and validated target.
- `IDEMPOTENCY_CONFLICT` (409): approval ID reused for other input.
- `APPROVAL_CONFLICT` (409): another approval ID already owns this revision.
- `APPROVAL_REQUIRED` (409): no current matching approval for export.
- `ASSET_INVALID` (409): missing, mismatched or corrupt referenced bytes.
- `NOT_FOUND` (404): unknown or malformed document ID.
- Pydantic rejects missing fields, short IDs, invalid hashes and
  non-integer revisions with 422 before repository writes.

The SQLite transaction prevents a revision change from racing an approval or
approved export. Filesystem actors outside the exclusive service can still
change files between requests, so every protected export revalidates bytes.

## Acceptance evidence and limits

```bash
uv run --locked ruff format --check app tests scripts evals
uv run --locked ruff check app tests scripts evals
uv run --locked python -m pytest tests/test_design_approval_binding.py -q
uv run --locked python -m pytest -q
```

Do not use this in a multi-tenant or externally exposed service without
authenticated principals, authorization policies, immutable evidence and
protected action admission. The existing Brief review run and
`DesignDocument` evidence remain distinct; this PR **does not** forge a
cross-service binding between them. A subsequent integration can attach
`review_run_id` only through an explicit, versioned contract.
