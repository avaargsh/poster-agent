"""Explicit, single-operation smoke against an operator-isolated local backend.

This client never provisions models or starts the server. Results are local
observations, not independent runtime attestation or quality acceptance.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from pathlib import Path
from uuid import uuid4

import httpx

from scripts.design_demo import _programmatic_product_png
from scripts.verify_ocr_evidence_bundle import verify_bundle

_FLAGS = dict.fromkeys(("runtime_attested", "quality_verified", "release_ready"), False)
_MAX_RESPONSE = 36 * 1024 * 1024
_SHA = re.compile(r"[0-9a-f]{64}")


class SmokeError(ValueError):
    """The operator must inspect retained evidence before another operation."""


def _check(condition: bool, message: str) -> None:
    if not condition:
        raise SmokeError(message)


def _sha(value: object) -> bool:
    return isinstance(value, str) and _SHA.fullmatch(value) is not None


def _json_file(path: Path, data: dict) -> None:
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )


def _request(client, method: str, path: str, expected: int, **kwargs):
    with client.stream(method, path, **kwargs) as response:
        _check(response.status_code == expected, f"unexpected HTTP {response.status_code}")
        data = bytearray()
        for chunk in response.iter_bytes():
            _check(len(data) + len(chunk) <= _MAX_RESPONSE, "response exceeds limit")
            data.extend(chunk)
        return bytes(data), response.headers


def _object(raw: bytes) -> dict:
    result = json.loads(raw)
    _check(isinstance(result, dict), "expected JSON object")
    return result


def _no_authority(data: dict) -> bool:
    return all(data.get(key) is False for key in _FLAGS)


def run_smoke(client, output: Path, anchor: Path, approver: str, exporter: str) -> dict:
    """Perform one explicit operation, preserving UNKNOWN on ambiguous submission."""
    output, anchor = output.resolve(), anchor.resolve()
    _check(not anchor.is_relative_to(output), "digest path must be outside evidence directory")
    _check(not anchor.exists(), "digest path already exists")
    _check(
        approver != exporter and all(32 <= len(value) <= 512 for value in (approver, exporter)),
        "separate named reviewer tokens required",
    )
    output.mkdir(mode=0o700, parents=False, exist_ok=False)
    approve = {"Authorization": "Bearer " + approver}
    export = {"Authorization": "Bearer " + exporter}
    stage = "create"
    execution = "NOT_SUBMITTED"
    operation_id = "ocr_" + uuid4().hex
    try:
        raw, _ = _request(
            client,
            "POST",
            "/api/designs",
            201,
            data={"title": "双十一牙刷", "price": "199.90", "cta": "立即抢购"},
            files={"product": ("synthetic.png", _programmatic_product_png(), "image/png")},
        )
        document = _object(raw)["document"]
        doc, revision = document["document_id"], document["revision"]
        _check(
            isinstance(doc, str)
            and re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", doc) is not None
            and type(revision) is int
            and revision == 1,
            "unexpected created design identity",
        )
        root = f"/api/designs/{doc}"
        stage = "approve"
        raw, _ = _request(client, "GET", root + "/approval-target", 200)
        target = _object(raw)
        _check(target["revision"] == revision, "approval target changed")
        approval_id = "approval-" + uuid4().hex
        approval = {
            "approval_id": approval_id,
            "expected_revision": revision,
            "spec_digest": target["spec_digest"],
            "asset_manifest_digest": target["asset_manifest_digest"],
        }
        _request(client, "POST", root + "/approvals", 201, json=approval, headers=approve)
        stage = "raster"
        revision_root = f"{root}/revisions/{revision}"
        png, headers = _request(client, "GET", revision_root + "/approved.png", 200, headers=export)
        svg_hash = headers.get("x-approved-svg-sha256")
        png_hash = headers.get("x-raster-png-sha256")
        _check(
            _sha(svg_hash) and _sha(png_hash) and hashlib.sha256(png).hexdigest() == png_hash,
            "approved raster hash mismatch",
        )
        (output / "approved.png").write_bytes(png)
        request = {
            "operation_id": operation_id,
            "svg_sha256": svg_hash,
            "png_sha256": png_hash,
        }
        _json_file(output / "operation-request.json", request)
        _json_file(
            output / "source.json",
            {
                "document_id": doc,
                "revision": revision,
                **approval,
                "rasterizer": headers.get("x-design-rasterizer"),
            },
        )
        stage = "submit"
        execution = "UNKNOWN"
        endpoint = revision_root + "/ocr-runs"
        raw, _ = _request(client, "POST", endpoint, 201, json=request, headers=export)
        journal = _object(raw)
        _json_file(output / "journal.json", journal)
        _check(
            journal.get("operation_id") == operation_id
            and journal.get("document_id") == doc
            and journal.get("revision") == revision
            and journal.get("approval_id") == approval_id
            and journal.get("png_sha256") == png_hash
            and journal.get("svg_sha256") == svg_hash
            and journal.get("replayed") is False
            and _no_authority(journal),
            "operation identity or authority mismatch",
        )
        status = journal.get("status")
        _check(status in ("EXECUTED", "UNKNOWN", "UNAVAILABLE", "FAILED"), "invalid terminal state")
        execution = status
        _check(execution == "EXECUTED", "OCR did not complete")
        observations, runtime = journal["observations"], journal["runtime"]
        _check(
            isinstance(observations, list)
            and bool(observations)
            and any(line.get("bbox") is not None for line in observations)
            and isinstance(runtime, dict)
            and runtime.get("attested") is False
            and all(
                runtime.get(key) is not None
                for key in ("inference_started_at", "inference_finished_at", "inference_elapsed_ms")
            ),
            "missing smoke observations or timing",
        )

        # A successful run may be replayed once with the exact same key.
        # No replay is attempted after a lost or non-successful response.
        stage = "replay"
        raw, _ = _request(client, "POST", endpoint, 200, json=request, headers=export)
        replay = _object(raw)
        _check(replay == {**journal, "replayed": True}, "idempotent replay mismatch")
        stage = "export"
        raw, _ = _request(
            client, "GET", f"{endpoint}/{operation_id}/evidence-bundle", 200, headers=export
        )
        evidence = _object(raw)
        digest = evidence["bundle_sha256"]
        _check(_sha(digest), "invalid bundle digest")
        _check(evidence["journal"] == journal, "exported journal differs from executed operation")
        evidence_path = output / "evidence.json"
        evidence_path.write_bytes(raw)
        stage = "verify"
        # This is a separately retained *local* digest, not an independent signer.
        verified = verify_bundle(evidence_path, digest)
        _check(verified["png_sha256"] == png_hash, "verified raster differs from submitted input")
        # Invalidate only the synthetic design created by this smoke. An old
        # evidence bundle remains offline-verifiable but must not be exportable
        # again as current. This negative probe never invokes the OCR POST.
        stage = "stale_revision_probe"
        _request(
            client,
            "POST",
            root + "/commands",
            200,
            json={
                "command_id": "smoke-stale-" + uuid4().hex,
                "expected_revision": revision,
                "actor": "user",
                "operations": [{"op": "set_text", "node_id": "title", "text": "已变更的合成标题"}],
            },
        )
        raw, _ = _request(
            client, "GET", f"{endpoint}/{operation_id}/evidence-bundle", 409, headers=export
        )
        _check(
            _object(raw)["error"]["code"] == "REVISION_CONFLICT",
            "stale revision was not explicitly rejected",
        )
        _json_file(
            output / "stale-rejection.json", {"http_status": 409, "code": "REVISION_CONFLICT"}
        )
        stage = "retain_digest"
        with os.fdopen(os.open(anchor, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as stream:
            stream.write(digest + "\n")
        report = {
            **verified,
            "status": "LOCAL_SMOKE_CONSISTENT_UNATTESTED",
            "execution_state": execution,
            "synthetic_asset": True,
            "stale_revision_rejected": True,
            "independent_anchor_verified": False,
        }
        _json_file(output / "outcome.json", report)
        return report
    except (ValueError, OSError, KeyError, TypeError, httpx.HTTPError):
        _json_file(
            output / "outcome.json",
            {
                "status": "INCOMPLETE",
                "stage": stage,
                "operation_id": operation_id,
                "execution_state": execution,
                **_FLAGS,
            },
        )
        # Never echo server response bodies, HTTP headers or reviewer tokens.
        raise SmokeError(f"smoke incomplete at {stage}; inspect local evidence") from None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-model", action="store_true")
    parser.add_argument("--confirm-isolated-server", action="store_true")
    parser.add_argument("--origin", default="http://127.0.0.1:8000")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--digest-file", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args(argv)
    if not args.run_model or not args.confirm_isolated_server:
        parser.error("explicit model execution and isolated server confirmation required")
    match = re.fullmatch(r"http://127\.0\.0\.1:([0-9]{1,5})", args.origin)
    if match is None or not 1 <= int(match[1]) <= 65535 or not 10 <= args.timeout <= 600:
        parser.error("numeric loopback origin and timeout of 10–600 seconds required")
    try:
        with httpx.Client(
            base_url=args.origin,
            timeout=args.timeout,
            trust_env=False,
            follow_redirects=False,
            transport=httpx.HTTPTransport(retries=0),
        ) as client:
            result = run_smoke(
                client,
                args.output_dir,
                args.digest_file,
                os.environ.get("POSTER_SMOKE_APPROVER_TOKEN", ""),
                os.environ.get("POSTER_SMOKE_EXPORTER_TOKEN", ""),
            )
    except (SmokeError, OSError):
        parser.exit(2, "Smoke incomplete; inspect private evidence; never retry an ambiguous run.\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
