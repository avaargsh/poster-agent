"""Explicit local VLM execution never overrides the design/approval/repair gate."""

import hashlib
import json
import sqlite3
import uuid
from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from PIL import Image

import app.designs.rasterization as rasterization
import app.designs.visual_runs as visual_runs
from app.main import app

APPROVER = "vlm-approver-" + "A" * 48
EXPORTER = "vlm-exporter-" + "B" * 48
MODEL_DIGEST = "sha256:" + "d" * 64


@pytest.fixture
def local_vlm(tmp_path, monkeypatch):
    monkeypatch.setenv("POSTER_DATA_DIR", str(tmp_path / "posters"))
    monkeypatch.setenv("POSTER_EXECUTION_DB", str(tmp_path / "executions.sqlite3"))
    monkeypatch.setenv("POSTER_CHECKPOINT_DB", str(tmp_path / "checkpoints.sqlite3"))
    monkeypatch.setenv("POSTER_DESIGN_DB", str(tmp_path / "designs.sqlite3"))
    monkeypatch.setenv("POSTER_DESIGN_ASSET_DIR", str(tmp_path / "design-assets"))
    monkeypatch.setenv("POSTER_DESIGN_VLM_RUNS_ENABLED", "1")
    monkeypatch.setenv(
        "POSTER_DESIGN_VLM_MODEL_CONFIG_JSON",
        json.dumps({"schema_version": 1, "model": "llava:latest", "digest": MODEL_DIGEST}),
    )
    monkeypatch.setenv(
        "POSTER_DESIGN_REVIEWERS_JSON",
        json.dumps(
            {
                "approver": {
                    "token_sha256": hashlib.sha256(APPROVER.encode()).hexdigest(),
                    "scopes": ["approve"],
                },
                "exporter": {
                    "token_sha256": hashlib.sha256(EXPORTER.encode()).hexdigest(),
                    "scopes": ["export"],
                },
            }
        ),
    )
    with BytesIO() as buffer:
        Image.new("RGB", (1024, 1024), (20, 30, 40)).save(buffer, "PNG")
        image_bytes = buffer.getvalue()
    monkeypatch.setattr(
        rasterization,
        "_convert_svg_to_png",
        lambda svg, width, height: (image_bytes, "test-raster/1"),
    )
    return tmp_path / "designs.sqlite3"


def _headers(token=EXPORTER):
    return {"Authorization": f"Bearer {token}"}


def _prepare(client, product_bytes):
    created = client.post(
        "/api/designs",
        data={"title": "双十一牙刷", "price": "199.90", "cta": "立即抢购"},
        files={"product": ("product.png", product_bytes, "image/png")},
    )
    assert created.status_code == 201, created.text
    doc = created.json()["document"]["document_id"]
    target = client.get(f"/api/designs/{doc}/approval-target").json()
    approved = client.post(
        f"/api/designs/{doc}/approvals",
        json={
            "approval_id": "vlm-approval-" + uuid.uuid4().hex,
            "expected_revision": 1,
            "spec_digest": target["spec_digest"],
            "asset_manifest_digest": target["asset_manifest_digest"],
        },
        headers=_headers(APPROVER),
    )
    assert approved.status_code == 201, approved.text
    raster = client.get(f"/api/designs/{doc}/revisions/1/approved.png", headers=_headers())
    assert raster.status_code == 200, raster.text
    return doc, {
        "operation_id": "vlm_" + uuid.uuid4().hex,
        "svg_sha256": raster.headers["x-approved-svg-sha256"],
        "png_sha256": raster.headers["x-raster-png-sha256"],
    }


def _url(doc):
    return f"/api/designs/{doc}/revisions/1/visual-runs"


def _findings():
    return [
        {
            "finding_id": "contrast-1",
            "category": "contrast",
            "severity": "minor",
            "confidence": 0.7,
            "explanation": "标题与背景对比度不足",
            "suggested_action": "REVIEW_STYLE",
            "region": {"x": 0.1, "y": 0.1, "width": 0.3, "height": 0.2},
        }
    ]


def test_shadow_run_is_journaled_exact_once_and_never_grants_release(
    local_vlm, product_bytes, monkeypatch
):
    calls = []
    monkeypatch.setattr(visual_runs, "verify_ollama_model", lambda profile: None)

    def fake_inspect(png, profile):
        calls.append((png, profile.model))
        return _findings()

    monkeypatch.setattr(visual_runs, "inspect_approved_png", fake_inspect)
    with TestClient(app) as client:
        doc, payload = _prepare(client, product_bytes)
        reply = client.post(_url(doc), json=payload, headers=_headers())
        assert reply.status_code == 201, reply.text
        report = reply.json()
        assert report["status"] == "EXECUTED"
        assert report["principal_id"] == "exporter"
        assert report["model_name"] == "llava:latest"
        assert len(report["model_profile_sha256"]) == 64
        assert report["findings"] == _findings()
        assert report["replayed"] is False
        assert report["model_execution_attested"] is False
        assert report["quality_verified"] is False
        assert report["repair_authorized"] is False
        assert report["release_ready"] is False
        assert len(calls) == 1
        replay = client.post(_url(doc), json=payload, headers=_headers())
        assert replay.status_code == 200
        assert replay.json()["replayed"] is True
        assert len(calls) == 1
        changed = client.post(
            _url(doc), json={**payload, "png_sha256": "f" * 64}, headers=_headers()
        )
        assert changed.status_code == 409
        assert changed.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
        assert len(calls) == 1
        found = client.get(_url(doc) + "/" + payload["operation_id"], headers=_headers())
        assert found.status_code == 200
        assert found.json()["status"] == "EXECUTED"
        assert client.get(f"/api/designs/{doc}").json()["document"]["revision"] == 1

    with TestClient(app) as restarted:
        result = restarted.get(_url(doc) + "/" + payload["operation_id"], headers=_headers())
        assert result.status_code == 200
        assert result.json()["status"] == "EXECUTED"
        assert len(calls) == 1


def test_default_disabled_named_exporter_and_validated_source(
    local_vlm, product_bytes, monkeypatch
):
    monkeypatch.setattr(visual_runs, "verify_ollama_model", lambda profile: None)
    calls = []
    monkeypatch.setattr(
        visual_runs, "inspect_approved_png", lambda png, profile: calls.append(1) or []
    )
    with TestClient(app) as client:
        doc, payload = _prepare(client, product_bytes)
        url = _url(doc)
        assert client.post(url, json=payload).status_code == 401
        assert client.post(url, json=payload, headers=_headers(APPROVER)).status_code == 403
        wrong = client.post(url, json={**payload, "svg_sha256": "f" * 64}, headers=_headers())
        assert wrong.status_code == 409
        assert wrong.json()["error"]["code"] == "VISUAL_SOURCE_MISMATCH"
        assert calls == []
        changed = client.post(
            f"/api/designs/{doc}/commands",
            json={
                "command_id": "vlm-stale-revision-0001",
                "actor": "user",
                "expected_revision": 1,
                "operations": [{"op": "set_text", "node_id": "title", "text": "新标题"}],
            },
        )
        assert changed.status_code == 200
        stale = client.post(url, json=payload, headers=_headers())
        assert stale.status_code == 409
        assert calls == []

    monkeypatch.setenv("POSTER_DESIGN_VLM_RUNS_ENABLED", "0")
    with TestClient(app) as disabled:
        denied = disabled.post(url, json=payload, headers=_headers())
        assert denied.status_code == 403
        assert denied.json()["error"]["code"] == "VLM_EXECUTION_DISABLED"


def test_unknown_on_provider_or_source_ambiguity_no_reinvocation(
    local_vlm, product_bytes, monkeypatch
):
    monkeypatch.setattr(visual_runs, "verify_ollama_model", lambda profile: None)
    called = []

    def ambiguous(png, profile):
        called.append(1)
        raise RuntimeError("reply may have been lost")

    monkeypatch.setattr(visual_runs, "inspect_approved_png", ambiguous)
    with TestClient(app) as client:
        doc, payload = _prepare(client, product_bytes)
        response = client.post(_url(doc), json=payload, headers=_headers())
        assert response.status_code == 201, response.text
        assert response.json()["status"] == "UNKNOWN"
        assert response.json()["findings"] == []
        replay = client.post(_url(doc), json=payload, headers=_headers())
        assert replay.status_code == 200
        assert replay.json()["status"] == "UNKNOWN"
        assert len(called) == 1


def test_abandoned_executing_journal_recovers_unknown_without_retry(
    local_vlm, product_bytes, monkeypatch
):
    monkeypatch.setattr(visual_runs, "verify_ollama_model", lambda profile: None)
    monkeypatch.setattr(visual_runs, "inspect_approved_png", lambda png, profile: [])
    with TestClient(app) as client:
        doc, payload = _prepare(client, product_bytes)
        completed = client.post(_url(doc), json=payload, headers=_headers())
        assert completed.status_code == 201
        with sqlite3.connect(local_vlm) as connection:
            connection.execute(
                "UPDATE design_vlm_runs SET status = 'EXECUTING', findings_json = NULL,"
                " finished_at = NULL WHERE operation_id = ?",
                (payload["operation_id"],),
            )
    with TestClient(app) as restarted:
        recovered = restarted.get(_url(doc) + "/" + payload["operation_id"], headers=_headers())
        assert recovered.status_code == 200
        assert recovered.json()["status"] == "UNKNOWN"
        assert recovered.json()["reason"] == "RECOVERED_AFTER_RESTART"


def test_inflight_revision_change_quarantines_results(local_vlm, product_bytes, monkeypatch):
    monkeypatch.setattr(visual_runs, "verify_ollama_model", lambda profile: None)
    with TestClient(app) as client:
        doc, payload = _prepare(client, product_bytes)

        def race(png, profile):
            changed = client.post(
                f"/api/designs/{doc}/commands",
                json={
                    "command_id": "vlm-inference-race-0001",
                    "actor": "user",
                    "expected_revision": 1,
                    "operations": [{"op": "set_text", "node_id": "title", "text": "并发修改"}],
                },
            )
            assert changed.status_code == 200
            return _findings()

        monkeypatch.setattr(visual_runs, "inspect_approved_png", race)
        result = client.post(_url(doc), json=payload, headers=_headers())
        assert result.status_code == 201, result.text
        assert result.json()["status"] == "UNKNOWN"
        assert result.json()["reason"] == "SOURCE_CHANGED"
        assert result.json()["findings"] == []


def test_config_requires_exact_local_digest_and_unsafe_model_output_fails_closed():
    from app.designs.vlm_local import VLMModelProfileError, load_vlm_profile, parse_findings

    cfg = {"schema_version": 1, "model": "llava:latest", "digest": MODEL_DIGEST}
    profile = load_vlm_profile(json.dumps(cfg))
    assert profile.model == "llava:latest"
    assert len(profile.fingerprint) == 64
    for invalid in (
        None,
        "{}",
        json.dumps({**cfg, "endpoint": "https://example.com"}),
        json.dumps({**cfg, "digest": "f" * 64}),
        json.dumps({**cfg, "model": "llava/../../etc"}),
        json.dumps({**cfg, "schema_version": True}),
    ):
        with pytest.raises(VLMModelProfileError):
            load_vlm_profile(invalid)
    assert parse_findings(json.dumps({"findings": _findings()})) == _findings()
    for invalid in (
        '{"findings":[], "release_ready":true}',
        '{"findings":[{"category":"price"}]}',
        '{"findings":[]}' + " " * 34000,
        '{"findings":[{"finding_id":"a","category":"contrast","severity":"minor",'
        '"confidence":1,"explanation":"good\\nnot allowed"}]}',
    ):
        with pytest.raises(ValueError):
            parse_findings(invalid)


@pytest.mark.parametrize(
    "mutation",
    [
        "malformed_findings",
        "wrong_findings_shape",
        "injected_release_authority",
        "oversized_findings",
        "malformed_runtime",
        "spoofed_runtime_attestation",
        "unknown_with_stale_findings",
    ],
)
def test_corrupted_vlm_journal_fails_closed_on_status_and_replay(
    local_vlm, product_bytes, monkeypatch, mutation
):
    calls = []
    monkeypatch.setattr(visual_runs, "verify_ollama_model", lambda profile: None)

    def inspect(png, profile):
        calls.append(1)
        return _findings()

    monkeypatch.setattr(visual_runs, "inspect_approved_png", inspect)
    with TestClient(app) as client:
        doc, payload = _prepare(client, product_bytes)
        created = client.post(_url(doc), json=payload, headers=_headers())
        assert created.status_code == 201, created.text
        assert created.json()["status"] == "EXECUTED"
        assert len(calls) == 1

        with sqlite3.connect(local_vlm) as db:
            if mutation == "malformed_findings":
                changes = {"findings_json": "{broken"}
            elif mutation == "wrong_findings_shape":
                changes = {"findings_json": "{}"}
            elif mutation == "injected_release_authority":
                changes = {
                    "findings_json": json.dumps(
                        [{**_findings()[0], "release_ready": True}], ensure_ascii=False
                    )
                }
            elif mutation == "oversized_findings":
                changes = {"findings_json": "[" + " " * 33000 + "]"}
            elif mutation == "malformed_runtime":
                changes = {"runtime_json": "{broken"}
            elif mutation == "spoofed_runtime_attestation":
                changes = {
                    "runtime_json": json.dumps(
                        {
                            "source": "local_ollama",
                            "local_model_digest_claim": MODEL_DIGEST,
                            "execution_evidence": "OPERATOR_LOCAL_NOT_ATTESTED",
                            "attested": True,
                        }
                    )
                }
            else:
                changes = {"status": "UNKNOWN"}
            for column, value in changes.items():
                db.execute(
                    f"UPDATE design_vlm_runs SET {column} = ? WHERE operation_id = ?",
                    (value, payload["operation_id"]),
                )

        lookup = client.get(
            _url(doc) + "/" + payload["operation_id"], headers=_headers()
        )
        assert lookup.status_code == 409, lookup.text
        assert lookup.json()["error"]["code"] == "VLM_JOURNAL_CORRUPT"

        replay = client.post(_url(doc), json=payload, headers=_headers())
        assert replay.status_code == 409, replay.text
        assert replay.json()["error"]["code"] == "VLM_JOURNAL_CORRUPT"
        assert len(calls) == 1
