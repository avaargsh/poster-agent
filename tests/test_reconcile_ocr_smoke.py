import httpx
import pytest

from scripts.reconcile_ocr_smoke import reconcile
from scripts.run_design_ocr_smoke import SmokeError


def _saved_request():
    request = {
        "operation_id": "ocr_" + "a" * 32,
        "svg_sha256": "b" * 64,
        "png_sha256": "c" * 64,
    }
    source = {
        "document_id": "design-1",
        "revision": 1,
        "expected_revision": 1,
        "approval_id": "approval-1",
        "spec_digest": "d" * 64,
        "asset_manifest_digest": "e" * 64,
        "rasterizer": "synthetic-raster-v1",
    }
    outcome = {
        "status": "INCOMPLETE",
        "stage": "submit",
        "operation_id": request["operation_id"],
        "execution_state": "UNKNOWN",
        "runtime_attested": False,
        "quality_verified": False,
        "release_ready": False,
    }
    journal = {
        **request,
        **{key: value for key, value in source.items() if key != "expected_revision"},
        "status": "EXECUTED",
        "replayed": False,
        "runtime_attested": False,
        "quality_verified": False,
        "release_ready": False,
    }
    return source, request, outcome, journal


@pytest.mark.parametrize("status", ["EXECUTED", "UNKNOWN", "EXECUTING", "UNAVAILABLE", "FAILED"])
def test_reconcile_reads_only_original_operation(status):
    source, request, outcome, journal = _saved_request()
    journal["status"] = status
    seen = []

    def handle(http_request):
        seen.append((http_request.method, http_request.url.path))
        return httpx.Response(200, json=journal)

    with httpx.Client(
        base_url="http://127.0.0.1:8000", transport=httpx.MockTransport(handle)
    ) as client:
        report = reconcile(client, source, request, outcome, "x" * 32)
    assert seen == [
        ("GET", "/api/designs/design-1/revisions/1/ocr-runs/" + request["operation_id"])
    ]
    assert report["observed_journal_status"] == status
    assert report["original_execution_state"] == "UNKNOWN"
    assert report["retry_authorized"] is False
    assert report["release_ready"] is False
    assert outcome["execution_state"] == "UNKNOWN"


@pytest.mark.parametrize("failure", ["404", "identity", "authority", "bad_status"])
def test_reconcile_refuses_untrusted_or_missing_journal(failure):
    source, request, outcome, journal = _saved_request()
    seen = []

    def handle(http_request):
        seen.append(http_request.method)
        if failure == "404":
            return httpx.Response(404)
        if failure == "identity":
            journal["png_sha256"] = "0" * 64
        elif failure == "authority":
            journal["release_ready"] = True
        else:
            journal["status"] = "SUCCESS"
        return httpx.Response(200, json=journal)

    with (
        httpx.Client(
            base_url="http://127.0.0.1:8000", transport=httpx.MockTransport(handle)
        ) as client,
        pytest.raises(SmokeError),
    ):
        reconcile(client, source, request, outcome, "x" * 32)
    assert seen == ["GET"]


@pytest.mark.parametrize(
    "invalid", ["operation", "revision", "outcome", "missing_approval_revision", "approval_revision"]
)
def test_reconcile_rejects_invalid_saved_identity_before_network(invalid):
    source, request, outcome, _ = _saved_request()
    if invalid == "operation":
        request["operation_id"] = "../other"
    elif invalid == "revision":
        source["revision"] = True
    elif invalid == "missing_approval_revision":
        del source["expected_revision"]
    elif invalid == "approval_revision":
        source["expected_revision"] = 2
    else:
        outcome["operation_id"] = "ocr_" + "f" * 32

    def handle(http_request):
        pytest.fail("invalid saved identity reached network")

    with (
        httpx.Client(
            base_url="http://127.0.0.1:8000", transport=httpx.MockTransport(handle)
        ) as client,
        pytest.raises(SmokeError),
    ):
        reconcile(client, source, request, outcome, "x" * 32)
