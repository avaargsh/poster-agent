# Local-only deployment boundary

## Supported launch

Poster Agent currently has no authentication, resource-level authorization, or multi-tenant isolation. The supported server entry point is a local-only launcher:

```bash
uv run --locked python -m app.run_local --port 8000
# For local development only:
uv run --locked python -m app.run_local --reload --port 8000
```

The launcher accepts only numeric loopback addresses such as `127.0.0.1` and `::1`; it rejects `0.0.0.0`, non-loopback addresses, and hostnames. It disables Uvicorn proxy-header trust. As defense in depth, application middleware rejects non-loopback peers and non-local `Host` values, and rejects cross-origin or cross-site browser requests, including reads that carry browser origin metadata. Open the UI at <http://127.0.0.1:8000>. These checks reduce accidental exposure and browser-based request risks, but they are not authentication.

## Exclusive worker ownership

The FastAPI lifespan acquires three nonblocking POSIX advisory file locks **before** calling `execution_store.recover_incomplete()`. The locks identify the resolved `POSTER_EXECUTION_DB`, `POSTER_CHECKPOINT_DB`, and `POSTER_DATA_DIR` paths. A second process sharing any of these paths fails startup instead of changing the first worker's live `RUNNING` tasks or concurrently writing workflow checkpoints.

- Supported runtime: macOS and Linux with a local filesystem that honors `fcntl.flock`. Other platforms fail closed at application startup.
- The worker retains all three locks until its lifespan exits. An ordinary shutdown releases them. The lock files remain on disk; **do not delete them to resolve a conflict**.
- If startup reports `WorkerLockUnavailable`, stop the other Poster Agent process and check that the execution, checkpoint, and poster paths point to the intended data. Then restart through `app.run_local`. Do not point a second process to a different ledger to bypass the limit.
- An unclean process exit releases OS locks, but any in-flight paid call may have incurred charges. The next single worker runs conservative recovery; unmatched outputs become `UNKNOWN`, not automatic paid retries.

This guard serializes cooperating processes on one local filesystem. It does not protect network filesystems with unreliable advisory locking, external programs editing files, direct calls that bypass the application lifespan, or processes deliberately deleting and replacing lock files. A `--reload` transition can fail closed if an old worker still owns the files; finish stopping that process before restarting.

See [paid-call idempotency](paid-call-idempotency.md) for `UNKNOWN` handling and [storage recovery](storage-recovery.md) for file-level consistency.

## Explicitly unsupported

- Do not bind the unauthenticated application to a LAN or public interface.
- Do not run `uvicorn app.main:app --host 0.0.0.0` or another raw Uvicorn command as a substitute for the launcher. Direct Uvicorn invocation bypasses launcher bind-address validation, although the application-level peer check still rejects ordinary non-loopback direct requests.
- Do not put the app behind a reverse proxy, expose it through container port publishing, or treat a firewall as a replacement for application authentication.
- Do not process customer data unless you have authorization and an appropriate security boundary.

The launcher is a guardrail against accidental exposure through the documented startup path, not a complete security boundary. A caller who deliberately uses a different ASGI server invocation can bypass it. M7 remains open until authentication, resource authorization, rate/concurrency limits, and supported persistence/worker semantics are designed and tested.

## Validation

The unit tests cover accepted loopback addresses, rejected network and hostname binds, port validation, Uvicorn arguments, and refusal before the server starts. They do not constitute a penetration test or production security certification.
