"""Read-only source drift probe for a previously captured local OCR bundle."""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path

import httpx

from scripts.run_design_ocr_smoke import SmokeError, _check, _object, _request
from scripts.verify_ocr_evidence_bundle import _MAX_FILE_BYTES, _canonical, _verify

_EXPECTED = {
    "model": (503, "OCR_LOCAL_MODEL_PROFILE_UNAVAILABLE"),
    "asset": (409, "ASSET_INVALID"),
}


def probe_drift(client, baseline: bytes, expected_sha256: str, exporter: str, drift: str) -> dict:
    """Read an existing journal and require explicit rejection of both artifacts."""
    try:
        _check(drift in _EXPECTED, "unsupported drift type")
        _check(32 <= len(exporter) <= 512, "named export token required")
        _check(len(baseline) <= _MAX_FILE_BYTES, "baseline exceeds limit")
        bundle = _object(baseline)
        verified = _verify(bundle, expected_sha256)
        journal = bundle["journal"]
        doc = journal["document_id"]
        _check(
            re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", doc) is not None,
            "invalid design identity",
        )
        revision = journal["revision"]
        operation = journal["operation_id"]
        endpoint = f"/api/designs/{doc}/revisions/{revision}/ocr-runs/{operation}"
        headers = {"Authorization": "Bearer " + exporter}

        def check_journal() -> None:
            raw, _ = _request(client, "GET", endpoint, 200, headers=headers)
            _check(_canonical(_object(raw)) == _canonical(journal), "journal changed")

        check_journal()
        status, code = _EXPECTED[drift]
        for suffix in ("/comparison-preview", "/evidence-bundle"):
            raw, _ = _request(client, "GET", endpoint + suffix, status, headers=headers)
            _check(_object(raw)["error"]["code"] == code, "unexpected refusal code")
        check_journal()
        return {
            **verified,
            "status": "SOURCE_DRIFT_REJECTED_UNATTESTED",
            "drift_type": drift,
            "http_status": status,
            "error_code": code,
            "journal_unchanged": True,
            "source_mutation_independently_verified": False,
        }
    except Exception:
        # The probe must not echo a server body, credential, or local path.
        raise SmokeError("source drift probe incomplete; inspect isolated setup") from None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm-isolated-server", action="store_true")
    parser.add_argument("--origin", default="http://127.0.0.1:8000")
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--expected-sha256", required=True)
    parser.add_argument("--drift", choices=tuple(_EXPECTED), required=True)
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
        with args.bundle.open("rb") as stream:
            baseline = stream.read(_MAX_FILE_BYTES + 1)
        with httpx.Client(
            base_url=args.origin,
            timeout=args.timeout,
            trust_env=False,
            follow_redirects=False,
            transport=httpx.HTTPTransport(retries=0),
        ) as client:
            result = probe_drift(
                client,
                baseline,
                args.expected_sha256,
                os.environ.get("POSTER_SMOKE_EXPORTER_TOKEN", ""),
                args.drift,
            )
        with os.fdopen(
            os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w"
        ) as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
    except (SmokeError, OSError):
        parser.exit(2, "Source drift probe refused; inspect isolated setup.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
