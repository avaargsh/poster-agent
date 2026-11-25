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
        **source,
        "status": "EXECUTED",
        "replayed": False,
        "runtime_attested": False,
        "quality_verified": False,
        "release_ready": False,
    }
    return source, request, outcome, journal


@pytest.mark.parametrize("status", ["EXECUTED", "UNKNOWN", "EXECUTING", "UNAVAILABLE", "FAILED"])
@pytest.mark.parametrize("include_expected_revision", [False, True])
def test_reconcile_reads_only_original_operation(status, include_expected_revision):
    source, request, outcome, journal = _saved_request()
    if include_expected_revision:
        source["expected_revision"] = source["revision"]
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
    "invalid",
    ["operation", "revision", "outcome", "approval_revision", "bool_approval_revision"],
)
def test_reconcile_rejects_invalid_saved_identity_before_network(invalid):
    source, request, outcome, _ = _saved_request()
    if invalid == "operation":
        request["operation_id"] = "../other"
    elif invalid == "revision":
        source["revision"] = True
    elif invalid == "approval_revision":
        source["expected_revision"] = 2
    elif invalid == "bool_approval_revision":
        source["expected_revision"] = True
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


@pytest.mark.parametrize("filename", ["source.json", "operation-request.json", "outcome.json"])
def test_cli_rejects_symlinked_saved_record(tmp_path, monkeypatch, capsys, filename):
    import json

    from scripts import reconcile_ocr_smoke as cli

    source, request, outcome, _ = _saved_request()
    for name, record in (
        ("source.json", source),
        ("operation-request.json", request),
        ("outcome.json", outcome),
    ):
        path = tmp_path / name
        path.write_text(json.dumps(record), encoding="utf-8")
    selected = tmp_path / filename
    backing = tmp_path / "backing.json"
    selected.rename(backing)
    selected.symlink_to(backing)
    output = tmp_path / "report.json"

    def unexpected_client(*args, **kwargs):
        pytest.fail("unsafe saved record reached HTTP client construction")

    monkeypatch.setattr(cli.httpx, "Client", unexpected_client)
    with pytest.raises(SystemExit) as exc:
        cli.main(
            [
                "--confirm-isolated-server",
                "--smoke-dir",
                str(tmp_path),
                "--output",
                str(output),
            ]
        )
    assert exc.value.code == 2
    assert "retain UNKNOWN and do not retry" in capsys.readouterr().err
    assert not output.exists()
