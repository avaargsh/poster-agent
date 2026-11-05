# Review concurrency: run-scoped locking

The review API retains the **single-worker, local SQLite deployment model**.
This change removes a process-wide head-of-line blocking problem: a 40-second
Qwen Brief extraction previously held the same `RLock` as unrelated
`GET /api/briefs/runs/{run_id}` and approval decisions.

## Lock ownership

- `create_run` first executes the durable SQLite admission transaction
  (`Idempotency-Key` -> request digest + stable `run_id`); this does **not**
  hold a process-global lock over the potentially billable model request.
- `create_run`, `get_run`, and `decide_run` serialize only on their
  **canonical `run_id`**. An unrelated run can read or advance while Qwen is
  waiting. Each graph call keeps its independent SQLite connection.
- The in-memory lock registry is protected by a brief metadata mutex.
  Reference counts include waiters and holders; entries are released after
  the last operation, avoiding unbounded per-run lock retention.
- A concurrent same-key replay resolves to the same run and waits on its
  run lock before checking the completed Checkpoint. It cannot re-dispatch
  Qwen. Different inputs with the same key still return conflict.
- Concurrent conflicting approvals of the same thread remain serialized:
  only the first matching terminal transition may succeed.

## Boundaries

The **single-worker startup file lock** remains mandatory. In-process locks
are not a substitute for distributed ownership and the SQLite checkpointer
is not a multi-worker store. Unknown outcomes still require reconciliation
against the original operation; they must never be retried by inventing a
new run ID. The global authorization and human-approval model is unchanged.

SQLite still serializes brief transactions and Checkpoint writes on disk.
This removes long application-level serialization during network I/O, not
all contention or latency. Synthetic concurrency tests do not measure
production throughput or prove exactly-once billing.

## Regression tests

```bash
uv lock --check
uv sync --locked
uv run --locked ruff check app tests scripts evals
uv run --locked ruff format --check app tests scripts evals
uv run --locked python -m pytest tests/test_review_concurrency.py \
  tests/test_brief_request_idempotency.py tests/test_review_workflow.py -q
uv run --locked python -m pytest -q
```

The concurrency tests use real SQLite `SqliteSaver` and LangGraph state,
a controlled Qwen stub, concurrent worker threads, and bounded waits. They
cover independent reads/approval while another Qwen request is blocked,
parallel different-key Qwen compilers, same-key replay, and conflicting
same-run approval. No external provider or model is called.
