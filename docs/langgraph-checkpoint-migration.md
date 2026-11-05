# LangGraph 1.2.16 → 1.2.19: Checkpoint migration gate

**Scope:** This is an isolated compatibility gate for the existing local
FastAPI + SqliteSaver review graph. It is not an Agent framework rewrite.
The original `extract` and `approval` node names, state fields,
`thread_id`, `interrupt` and `Command(resume=...)` must remain stable.

## Pinned baseline and proposed target

| Distribution | Baseline main lock | Candidate |
| --- | --- | --- |
| `langgraph` | 1.2.16 | 1.2.19 |
| `langgraph-checkpoint` | 4.3.0 | 4.3.1 |
| `langgraph-checkpoint-sqlite` | 3.1.2 | 3.1.3 |

Upstream:
- https://github.com/langchain-ai/langgraph/releases
- https://docs.langchain.com/oss/python/langgraph/interrupts
- https://docs.langchain.com/oss/python/langgraph/backward-compatibility

The candidate restores real old-version SQLite checkpoints rather than
recreating checkpoints in the upgraded environment. The old-version runner
reads `uv.lock` from the **current PR base SHA**, preventing the upgrade
commit from quietly overwriting the original fixture environment.

## Required migration evidence

Use the explicit `ci:compat` label. The isolated workflow will:

1. Install the base-commit `uv.lock` and assert that baseline versions are
   exactly correct; produce real SQLite checkpoints with `SqliteSaver`.
2. Persist four independent review threads: pending human approval,
   already approved, rejected, and missing fields; bind a stable idempotency
   key to the pending thread.
3. Invoke `uv lock --upgrade-package` for the three target distributions,
   emit the **uv-generated lock diff** in CI logs, and check the lock.
4. Reinstall the upgraded dependencies on the same runner. Read the original
   SQLite file, resume the pending human approval, verify terminal histories,
   idempotent duplicate decisions, conflicting decisions, and no reexecution
   of the `extract` node.
5. Execute existing review + Brief idempotency tests under the target versions.

The `uv.lock` changes must be committed from the **generated uv output**
and verified with `uv lock --check`, rather than editing package hashes or
URLs manually. Keep this upgrade separate from concurrency refactors.

## Deployment and rollback notes

- Back up the checkpoint SQLite database and WAL/SHM files safely while
  the service is stopped before deploying the new lock.
- Do not rename graph nodes, change checkpoint serializer, or tighten the
  persisted `ReviewState` schema in this upgrade.
- Pending approval resume must never trigger a new Qwen request. Every
  external operation remains subject to the request ledger and UNKNOWN
  reconciliation. An interrupted `approval` node must remain side-effect-free.
- A passing synthetic/local Checkpoint migration gate does not prove
  persistence under actual process crashes or production network storage.
- Rollback after writing checkpoints with new-version code has not been
  validated and must **not** be assumed safe without an explicit reverse
  compatibility test or a verified pre-upgrade snapshot.

This phase deliberately does not add `DeltaChannel`, tool-level interrupts,
parallel ToolNode execution, or agent tool invocation because the current
review graph has two sequential nodes and no tool-based side effects.
