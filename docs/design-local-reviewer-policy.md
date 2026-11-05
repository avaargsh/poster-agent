# Local reviewer attribution v0.1 (opt-in)

This is a **single-process loopback-only** defense for the existing
DesignDocument approval workflow. It is not OIDC, a verified human identity,
an EngageGuard Permit, RBAC for all document operations, or production security.

## Configuration

At application startup, set `POSTER_DESIGN_REVIEWERS_JSON` to a JSON object
mapping stable local principal IDs to SHA-256 digests of **independently
generated, high-entropy bearer tokens** and their allowed scopes:

```json
{
  "reviewer-one": {
    "token_sha256": "<64 lowercase hex digits>",
    "scopes": ["approve"]
  },
  "exporter-one": {
    "token_sha256": "<different 64 lowercase hex digits>",
    "scopes": ["export"]
  },
  "auditor-one": {
    "token_sha256": "<third 64 lowercase hex digits>",
    "scopes": ["audit.read"]
  }
}
```

Replace every placeholder with the SHA-256 digest of a *different*, random
token generated securely. Pass the corresponding **raw token** in
`Authorization: Bearer <token>`. Do not send a token digest as the bearer,
store bearer plaintext in the policy, or check credentials into Git.

The policy is snapshotted at server lifespan start. A malformed **present**
environment variable returns `503 DESIGN_REVIEWER_CONFIG_INVALID` on
protected routes. If the variable is **absent** at startup, historical
loopback-only approval behavior remains available for compatibility, with no
reviewer attribution. This is an **explicit opt-in**, not fail-closed
authentication for an externally exposed deployment. Removing it and
restarting weakens the local API security boundary.

The parser requires a nonempty mapping of unique principal IDs, unique token
hashes and nonempty explicit scope lists. Unknown scopes, malformed digests
and duplicate policy object keys fail closed.

## Protected operations when enabled

| Scope | Route |
| --- | --- |
| `approve` | `POST /api/designs/{document_id}/approvals` |
| `export` | `GET /api/designs/{document_id}/revisions/{revision}/approved.svg` |
| `audit.read` | `GET /api/designs/{document_id}/approvals` |

Unknown/missing bearer => 401. A valid principal without the requested scope
=> 403. `X-Actor` headers are never treated as authenticated identity.

Preview, create, edit, approval-target and the Brief linkage API retain their
separate existing local security boundaries; this is **not full-document
access control**.

## Durable evidence and revocation

An accepted authenticated approval inserts a
`design_approval_attributions` row in the **same SQLite transaction** as its
revision-bound approval: `document_id`, `approval_id`, `principal_id`,
`policy_digest`, and `recorded_at`. Attribution is derived exclusively from
the server-configured bearer policy, not from request JSON.

Idempotent replay requires the **same principal and same exact policy
digest**. Historical anonymous approvals cannot be retroactively claimed as
authenticated by replay. Approval-history includes `principal_id` and
`policy_digest` for evidence inspection, with nulls on historical local rows.

Authenticated SVG export requires the matching recorded policy digest as
well as all pre-existing revision, renderer and asset-byte checks. Policy
rotation changes its digest and blocks export until a fresh approved
revision is created. Already attributed approvals also refuse fallback
anonymous export if the opt-in environment variable is later removed.

**Limitations:** The SQLite administrator can modify or delete the
attribution table, so it is not cryptographically immutable, externally
anchored or independently attestable. Credential sharing is indistinguishable
from true reviewer identity. No authorization decision history is recorded for
denied requests. An administrator disabling the opt-in policy may create
future anonymous approvals; do not treat this as a production security
boundary. There is no OCI/OCR acceptance gate in this increment.

## Verification

```bash
uv run --locked ruff format --check app tests scripts evals
uv run --locked ruff check app tests scripts evals
uv run --locked python -m pytest tests/test_design_reviewer_attribution.py -q
uv run --locked python -m pytest -q
```
