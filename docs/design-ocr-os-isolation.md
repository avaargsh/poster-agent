# Linux OS isolation observation for DesignDocument OCR smoke

This is a read-only observer for the manual OCR smoke in [Issue #57](https://github.com/avaargsh/poster-agent/issues/57). It does not create namespaces, configure firewall rules, mount model directories, start Uvicorn, or invoke PaddleOCR.

## Operator prerequisites

Run the server directly as one Uvicorn process in the already prepared isolated Linux network and mount namespaces. The command line must explicitly include:

```sh
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

The process must have no child processes. The network namespace must expose only `lo`, with no default route and no route through a non-loopback interface. Both exact model directories configured in `POSTER_DESIGN_OCR_MODEL_CONFIG_JSON` must exist in the server's mount namespace and be covered by read-only mounts. Prepare these OS controls before starting the server; this observer will not change them.

Record the PID of the Uvicorn process itself, not a shell, `uv`, systemd or other launcher PID. Run from the clean repository root and use an existing private output parent outside the checkout:

```sh
uv run --no-sync python -m scripts.verify_ocr_os_isolation \
  --server-pid "$SERVER_PID" \
  --detection-model-dir /models/detection \
  --recognition-model-dir /models/recognition \
  --repo "$PWD" \
  --output /private/ocr-os-isolation.json
```

Use the exact absolute model paths visible inside the server's mount namespace. The two paths must be distinct, non-nested, canonical directories. The output file must not already exist. It is created with mode `0600`; it contains local model paths and namespace identifiers, so retain it privately and do not commit it.

## What is inspected

The observer reads `/proc/<pid>/cmdline`, `status`, every thread's `task/<tid>/children`, namespace links, `environ`, `net/dev`, IPv4/IPv6 route tables and `mountinfo` for the target process. It parses the server's unique `POSTER_DESIGN_OCR_MODEL_CONFIG_JSON`, requires the two CLI paths to match the configured paths exactly, and records the configured model-tree SHA-256 pins and canonical model-profile fingerprint. It does not rehash model files; the application model-profile loader and the separate runtime manifest collector perform those checks. It requires:

- the exact `uvicorn app.main:app` command, explicit `--host 127.0.0.1`, explicit `--workers 1`, and no child process;
- only the loopback interface, no IPv4 or IPv6 default route, and no non-loopback route;
- both configured model directories to exist in the target process root and resolve without symlink traversal;
- the CLI model paths to match the server process environment's exact pinned profile and valid tree digests;
- the nearest covering mount and every nested mount within each model directory to have the per-mount `ro` option.

A refusal is fail-closed. It does not print process arguments, model paths, mount details or other private operator input to stdout.

## Evidence boundary

A successful report is `LOCAL_OS_ISOLATION_OBSERVED_UNATTESTED`. It is a local procfs observation, not a signed or independently authenticated receipt. In particular:

- `egress_policy_observed=LOOPBACK_ONLY_NO_DEFAULT_ROUTE` describes the inspected namespace's current interfaces and routes; it does not configure or attest the host firewall, cgroups, inherited file descriptors or process history.
- Read-only mount flags are observed from the target's mount table; this does not attest who created the mount or prevent a privileged host operator from changing it later.
- The command line is observed, not proof of the executable image or server code loaded into memory.
- `server_process_verified`, `egress_denied_verified`, `runtime_attested`, `quality_verified` and `release_ready` remain false by design.

The tests use synthetic procfs fixtures. They validate parsing and refusal behavior only; they are not evidence of an actual isolated server or real OCR inference. Retain this report privately alongside the environment manifest, OCR evidence bundle, separately retained bundle digest, source-drift probe outputs and actual pinned-model smoke result. Issue #57 remains open until the operator acceptance is completed.
