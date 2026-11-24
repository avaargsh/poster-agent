"""Observe the original OCR journal after an ambiguous smoke submission."""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path

import httpx

from scripts.run_design_ocr_smoke import (
    _FLAGS,
    SmokeError,
    _check,
    _no_authority,
    _object,
    _request,
    _sha,
)

_MAX_SAVED_BYTES = 16 * 1024


def reconcile(client, source: dict, request: dict, outcome: dict, exporter: str) -> dict:
    """Observe one authorized journal without changing the original UNKNOWN outcome."""
    try:
        doc, revision = source["document_id"], source["revision"]
        operation = request["operation_id"]
        _check(
            isinstance(doc, str)
            and re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", doc) is not None
            and type(revision) is int
            and revision >= 1
            and isinstance(operation, str)
            and re.fullmatch(r"ocr_[0-9a-f]{32}", operation) is not None,
            "invalid saved identity",
        )
        _check(
            outcome["status"] == "INCOMPLETE"
            and outcome["stage"] == "submit"
            and outcome["execution_state"] == "UNKNOWN"
            and outcome["operation_id"] == operation
            and _no_authority(outcome),
            "not an ambiguous submission",
        )
        _check(
            set(request) == {"operation_id", "svg_sha256", "png_sha256"}
            and all(_sha(request[key]) for key in ("svg_sha256", "png_sha256"))
            and all(_sha(source[key]) for key in ("spec_digest", "asset_manifest_digest"))
            and all(
                isinstance(source[key], str) and 0 < len(source[key]) <= 256
                for key in ("approval_id", "rasterizer")
            )
            and 32 <= len(exporter) <= 512,
            "invalid saved source or export token",
        )
        endpoint = f"/api/designs/{doc}/revisions/{revision}/ocr-runs/{operation}"
        raw, _ = _request(
            client, "GET", endpoint, 200, headers={"Authorization": "Bearer " + exporter}
        )
        journal = _object(raw)
        _check(
            type(journal.get("revision")) is int
            and all(journal.get(key) == value for key, value in request.items())
            and all(
                journal.get(key) == source[key]
                for key in (
                    "document_id",
                    "revision",
                    "approval_id",
                    "spec_digest",
                    "asset_manifest_digest",
                    "rasterizer",
                )
            )
            and journal.get("replayed") is False
            and _no_authority(journal),
            "journal identity or authority mismatch",
        )
        status = journal.get("status")
        _check(
            status in ("EXECUTING", "EXECUTED", "UNKNOWN", "UNAVAILABLE", "FAILED"),
            "unexpected journal status",
        )
        return {
            "status": "JOURNAL_OBSERVED_UNATTESTED",
            "document_id": doc,
            "revision": revision,
            "operation_id": operation,
            "original_execution_state": "UNKNOWN",
            "observed_journal_status": status,
            "retry_authorized": False,
            **_FLAGS,
        }
    except (KeyError, TypeError, ValueError, OSError, RecursionError, httpx.HTTPError):
        raise SmokeError("journal reconciliation incomplete; retain UNKNOWN") from None


def _read_saved(path: Path) -> dict:
    with path.open("rb") as stream:
        raw = stream.read(_MAX_SAVED_BYTES + 1)
    _check(len(raw) <= _MAX_SAVED_BYTES, "saved record exceeds limit")
    return _object(raw)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm-isolated-server", action="store_true")
    parser.add_argument("--origin", default="http://127.0.0.1:8000")
    parser.add_argument("--smoke-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args(argv)
    match = re.fullmatch(r"http://127\.0\.0\.1:([0-9]{1,5})", args.origin)
    if (
        not args.confirm_isolated_server
        or match is None
        or not 1 <= int(match[1]) <= 65535
        or not 10 <= args.timeout <= 600
    ):
        parser.error("isolated numeric loopback origin and timeout of 10–600 seconds required")
    try:
        _check(not args.output.exists(), "output already exists")
        source = _read_saved(args.smoke_dir / "source.json")
        request = _read_saved(args.smoke_dir / "operation-request.json")
        outcome = _read_saved(args.smoke_dir / "outcome.json")
        with httpx.Client(
            base_url=args.origin,
            timeout=args.timeout,
            trust_env=False,
            follow_redirects=False,
            transport=httpx.HTTPTransport(retries=0),
        ) as client:
            result = reconcile(
                client, source, request, outcome, os.environ.get("POSTER_SMOKE_EXPORTER_TOKEN", "")
            )
        with os.fdopen(
            os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w"
        ) as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
    except (ValueError, OSError, RecursionError):
        parser.exit(2, "Reconciliation incomplete; retain UNKNOWN and do not retry.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
