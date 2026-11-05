# Qwen Brief request admission and recovery

Scope: local single-worker M2 Brief extraction only. This does not change
PosterSpec approval semantics, image generation, or the DesignDocument store.

## Contract

- `POST /api/briefs/runs` requires `Idempotency-Key` for `compiler=qwen` (HTTP 428 before dispatch). Demo mode keeps its former optional-key behavior.
- Keys are 8–128 ASCII letters, digits, underscores or hyphens. Invalid keys return HTTP 422.
- `brief_request_claims` lives in the existing `POSTER_CHECKPOINT_DB`. It atomically binds an idempotency key to the canonical request digest and one stable `run_id` **before** invoking LangGraph.
- The same key and same request can return the original checkpointed draft (including after a lost HTTP reply). The same key with changed input returns HTTP 409. A replay does not call Qwen.
- If no complete draft checkpoint exists, a replay returns HTTP 409 and retains `UNKNOWN`; it never retries the provider just because a client timed out.
- The request ledger marks `COMPLETED` only after the existing graph checkpoint can be read. The ordinary graph approval flow remains separate.
- After acquiring the single-worker filesystem lock, startup changes abandoned `RUNNING` claims to `UNKNOWN`. This does not prove provider failure, refund, or cancellation.

`GET /api/briefs/runs/by-key/{key}` returns the original `run_id`, claim
status and timestamps without performing any model operation. If a checkpoint
was fully committed, replaying the same POST can reconcile to `COMPLETED`.
An unresolved `UNKNOWN` requires manual investigation; **do not create a
new key for the same potentially billed operation**.

The local React controller preserves the Qwen request key between timeouts and
page reloads via browser localStorage. It queries the original claim and
checkpoint before reporting a failed network request as terminal. A completed,
intentional new submit may allocate a fresh key.

## Limitations

This is a conservative **at-most-one application dispatch per admitted key**,
not proof of exactly-once provider billing. The account is not authenticated;
localhost controls remain the security boundary. SQLite and advisory locks are
single-host assumptions, not distributed coordination. The request ledger
stores hashes and stable IDs, not prompts, model credentials, or images.

It also does not remove the current process-global review lock around Qwen
execution; shorter lock scope and task-specific concurrency remain separate
work.

## Verification

```bash
uv lock --check
uv sync --locked
uv run --locked python -m pytest tests/test_brief_request_store.py tests/test_brief_request_idempotency.py tests/test_review_workflow.py -q
uv run --locked python -m pytest -q
uv run --locked ruff check app tests scripts evals
uv run --locked ruff format --check app tests scripts evals
```

Relevant failure injection: concurrent same-key requests; response lost after
checkpoint commit; missing checkpoint after claim; process restart and UNKNOWN;
key conflict; repeat approval without changing the confirmed spec.

This branch does **not** change LangGraph/Checkpoint versions. A separate
compatibility PR must replay real old-format checkpoints on the proposed
upgrade before modifying `uv.lock`.
