"""Read-only control-flow tests for local OCR source drift probes."""

from __future__ import annotations

import json

import httpx
import pytest

import scripts.probe_ocr_source_drift as probe
from scripts.run_design_ocr_smoke import SmokeError

EXPECTED_DIGEST = "a" * 64
EXPORTER = "drift-exporter-" + "X" * 40


def _baseline() -> tuple[bytes, dict, str]:
    journal = {
        "document_id": "design-1",
        "revision": 3,
        "operation_id": "ocr_" + "b" * 32,
        "status": "EXECUTED",
        "png_sha256": "c" * 64,
    }
    endpoint = (
        f"/api/designs/{journal['document_id']}/revisions/{journal['revision']}"
        f"/ocr-runs/{journal['operation_id']}"
    )
    return json.dumps({"journal": journal}).encode("utf-8"), journal, endpoint


@pytest.mark.parametrize(
    ("drift", "expected_status", "expected_code"),
    [
        ("model", 503, "OCR_LOCAL_MODEL_PROFILE_UNAVAILABLE"),
        ("asset", 409, "ASSET_INVALID"),
    ],
)
def test_probe_requires_both_read_only_endpoints_to_reject_source_drift(
    monkeypatch, drift, expected_status, expected_code
):
    baseline, journal, endpoint = _baseline()
    monkeypatch.setattr(
        probe,
        "_verify",
        lambda bundle, expected: {
            "status": "CONSISTENT_LOCAL_EVIDENCE_ONLY",
            "bundle_sha256": expected,
        },
    )
    seen = []

    def handle(request):
        seen.append((request.method, request.url.path))
        if request.url.path == endpoint:
            return httpx.Response(200, json=journal)
        if request.url.path in (
            endpoint + "/comparison-preview",
            endpoint + "/evidence-bundle",
        ):
            return httpx.Response(expected_status, json={"error": {"code": expected_code}})
        pytest.fail(f"unexpected request: {request.method} {request.url.path}")

    with httpx.Client(
        base_url="http://127.0.0.1:8000",
        transport=httpx.MockTransport(handle),
    ) as client:
        report = probe.probe_drift(client, baseline, EXPECTED_DIGEST, EXPORTER, drift)

    assert seen == [
        ("GET", endpoint),
        ("GET", endpoint + "/comparison-preview"),
        ("GET", endpoint + "/evidence-bundle"),
        ("GET", endpoint),
    ]
    assert all(method == "GET" for method, _ in seen)
    assert report["status"] == "SOURCE_DRIFT_REJECTED_UNATTESTED"
    assert report["drift_type"] == drift
    assert report["http_status"] == expected_status
    assert report["error_code"] == expected_code
    assert report["journal_unchanged"] is True
    assert report["source_mutation_independently_verified"] is False
    assert report["runtime_attested"] is False
    assert report["quality_verified"] is False
    assert report["release_ready"] is False


def test_probe_refuses_journal_change_between_initial_and_final_read(monkeypatch):
    baseline, journal, endpoint = _baseline()
    monkeypatch.setattr(
        probe,
        "_verify",
        lambda bundle, expected: {"bundle_sha256": expected},
    )
    changed = {**journal, "status": "UNKNOWN"}
    journal_reads = 0
    seen = []

    def handle(request):
        nonlocal journal_reads
        seen.append((request.method, request.url.path))
        if request.url.path == endpoint:
            journal_reads += 1
            return httpx.Response(200, json=journal if journal_reads == 1 else changed)
        if request.url.path.endswith("/comparison-preview"):
            return httpx.Response(
                503, json={"error": {"code": "OCR_LOCAL_MODEL_PROFILE_UNAVAILABLE"}}
            )
        if request.url.path.endswith("/evidence-bundle"):
            return httpx.Response(
                503, json={"error": {"code": "OCR_LOCAL_MODEL_PROFILE_UNAVAILABLE"}}
            )
        pytest.fail(f"unexpected request: {request.method} {request.url.path}")

    with (
        httpx.Client(
            base_url="http://127.0.0.1:8000",
            transport=httpx.MockTransport(handle),
        ) as client,
        pytest.raises(SmokeError, match="source drift probe incomplete"),
    ):
        probe.probe_drift(client, baseline, EXPECTED_DIGEST, EXPORTER, "model")

    assert journal_reads == 2
    assert len(seen) == 4
    assert all(method == "GET" for method, _ in seen)


def test_probe_rejects_success_or_wrong_error_code_as_drift_evidence(monkeypatch):
    baseline, journal, endpoint = _baseline()
    monkeypatch.setattr(probe, "_verify", lambda bundle, expected: {"bundle_sha256": expected})
    seen = []

    def handle(request):
        seen.append((request.method, request.url.path))
        if request.url.path == endpoint:
            return httpx.Response(200, json=journal)
        if request.url.path.endswith("/comparison-preview"):
            return httpx.Response(503, json={"error": {"code": "UNEXPECTED_PROVIDER_FAILURE"}})
        pytest.fail(f"probe continued after an invalid refusal: {request.url.path}")

    with (
        httpx.Client(
            base_url="http://127.0.0.1:8000",
            transport=httpx.MockTransport(handle),
        ) as client,
        pytest.raises(SmokeError, match="source drift probe incomplete"),
    ):
        probe.probe_drift(client, baseline, EXPECTED_DIGEST, EXPORTER, "model")

    assert seen == [
        ("GET", endpoint),
        ("GET", endpoint + "/comparison-preview"),
    ]
