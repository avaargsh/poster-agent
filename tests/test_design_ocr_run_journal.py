"""Durable, opt-in DesignDocument OCR execution journal (never release authority)."""

import hashlib
import json
import sqlite3
import uuid
from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from PIL import Image

import app.designs.ocr_runs as ocr_runs
import app.designs.rasterization as rasterization
from app.designs.ocr_model_policy import model_directory_sha256
from app.main import app
from app.ocr_provider import OCRLines, OCRUnavailable

APPROVER = "ocr-approver-" + "A" * 48
EXPORTER = "ocr-exporter-" + "B" * 48


@pytest.fixture
def ocr_paths(tmp_path, monkeypatch):
    monkeypatch.setenv("POSTER_DATA_DIR", str(tmp_path / "posters"))
    monkeypatch.setenv("POSTER_EXECUTION_DB", str(tmp_path / "executions.sqlite3"))
    monkeypatch.setenv("POSTER_CHECKPOINT_DB", str(tmp_path / "checkpoints.sqlite3"))
    monkeypatch.setenv("POSTER_DESIGN_DB", str(tmp_path / "designs.sqlite3"))
    monkeypatch.setenv("POSTER_DESIGN_ASSET_DIR", str(tmp_path / "design-assets"))
    monkeypatch.setenv("POSTER_DESIGN_OCR_RUNS_ENABLED", "1")
    detector = tmp_path / "local-detector"
    recognizer = tmp_path / "local-recognizer"
    for root in (detector, recognizer):
        root.mkdir()
        (root / "inference.json").write_text('{"fake":"model"}', encoding="utf-8")
        (root / "weights.bin").write_bytes(b"synthetic-not-real-ocr-model")
    monkeypatch.setenv(
        "POSTER_DESIGN_OCR_MODEL_CONFIG_JSON",
        json.dumps(
            {
                "schema_version": 1,
                "text_detection_model_dir": str(detector),
                "text_recognition_model_dir": str(recognizer),
                "text_detection_sha256": model_directory_sha256(detector),
                "text_recognition_sha256": model_directory_sha256(recognizer),
            }
        ),
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
    raster_bytes = BytesIO()
    Image.new("RGB", (1024, 1024), (12, 23, 34)).save(raster_bytes, format="PNG")
    monkeypatch.setattr(
        rasterization,
        "_convert_svg_to_png",
        lambda svg, width, height: (raster_bytes.getvalue(), "test-raster/1"),
    )
    return tmp_path / "designs.sqlite3"


def _headers(token=EXPORTER):
    return {"Authorization": "Bearer " + token}


def _prepare(client, product_bytes, *, cta="立即抢购"):
    data = {"title": "双十一牙刷", "price": "199.90"}
    if cta is not None:
        data["cta"] = cta
    created = client.post(
        "/api/designs",
        data=data,
        files={"product": ("product.png", product_bytes, "image/png")},
    )
    assert created.status_code == 201, created.text
    doc = created.json()["document"]["document_id"]
    target = client.get(f"/api/designs/{doc}/approval-target").json()
    approved = client.post(
        f"/api/designs/{doc}/approvals",
        json={
            "approval_id": "approval-" + uuid.uuid4().hex,
            "expected_revision": target["revision"],
            "spec_digest": target["spec_digest"],
            "asset_manifest_digest": target["asset_manifest_digest"],
        },
        headers=_headers(APPROVER),
    )
    assert approved.status_code == 201, approved.text
    raster = client.get(f"/api/designs/{doc}/revisions/1/approved.png", headers=_headers())
    assert raster.status_code == 200, raster.text
    return doc, {
        "operation_id": "ocr_" + uuid.uuid4().hex,
        "svg_sha256": raster.headers["x-approved-svg-sha256"],
        "png_sha256": raster.headers["x-raster-png-sha256"],
    }


def _url(doc):
    return f"/api/designs/{doc}/revisions/1/ocr-runs"


def _ocr_lines():
    return OCRLines(
        [
            {"text": "双十一牙刷", "score": 0.99, "bbox": [2, 4, 210, 55]},
            {"text": "¥199.90", "score": 0.98, "bbox": [2, 65, 210, 115]},
            {"text": "立即抢购", "score": 0.97, "bbox": [2, 125, 210, 170]},
        ],
        {"source": "paddleocr_adapter", "line_count": 3, "attested": True},
    )


def test_explicit_ocr_run_is_journaled_idempotent_and_survives_restart(
    ocr_paths, product_bytes, monkeypatch
):
    seen = []

    def fake_recognize(path, **kwargs):
        seen.append(path.read_bytes())
        return _ocr_lines()

    monkeypatch.setattr(ocr_runs, "recognize", fake_recognize)
    with TestClient(app) as client:
        doc, payload = _prepare(client, product_bytes)
        response = client.post(_url(doc), json=payload, headers=_headers())
        assert response.status_code == 201, response.text
        data = response.json()
        assert data["status"] == "EXECUTED"
        assert data["png_sha256"] == payload["png_sha256"]
        assert data["svg_sha256"] == payload["svg_sha256"]
        assert data["approval_id"].startswith("approval-")
        assert data["principal_id"] == "exporter"
        assert len(data["model_profile_sha256"]) == 64
        assert len(data["observations"]) == 3
        assert data["runtime_attested"] is False
        assert data["quality_verified"] is False
        assert data["release_ready"] is False
        assert data["runtime"]["attested"] is False
        assert data["replayed"] is False
        assert len(seen) == 1
        replay = client.post(_url(doc), json=payload, headers=_headers())
        assert replay.status_code == 200, replay.text
        assert replay.json()["replayed"] is True
        assert len(seen) == 1
        changed = client.post(
            _url(doc), json={**payload, "png_sha256": "0" * 64}, headers=_headers()
        )
        assert changed.status_code == 409
        assert changed.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
        assert len(seen) == 1

    with TestClient(app) as restarted:
        lookup = restarted.get(_url(doc) + "/" + payload["operation_id"], headers=_headers())
        assert lookup.status_code == 200, lookup.text
        assert lookup.json()["status"] == "EXECUTED"
        assert lookup.json()["runtime_attested"] is False
        replay = restarted.post(_url(doc), json=payload, headers=_headers())
        assert replay.status_code == 200
        assert replay.json()["replayed"] is True
        assert len(seen) == 1


def test_disabled_or_missing_export_identity_never_invokes_model(
    ocr_paths, product_bytes, monkeypatch
):
    invoked = []

    def model(path, **kwargs):
        invoked.append(1)
        return _ocr_lines()

    monkeypatch.setattr(ocr_runs, "recognize", model)
    with TestClient(app) as client:
        doc, payload = _prepare(client, product_bytes)
        assert client.post(_url(doc), json=payload).status_code == 401
        assert client.post(_url(doc), json=payload, headers=_headers(APPROVER)).status_code == 403
        assert invoked == []

    monkeypatch.setenv("POSTER_DESIGN_OCR_RUNS_ENABLED", "0")
    with TestClient(app) as disabled:
        blocked = disabled.post(_url(doc), json=payload, headers=_headers())
        assert blocked.status_code == 403
        assert blocked.json()["error"]["code"] == "OCR_EXECUTION_DISABLED"
        assert invoked == []


def test_change_during_ocr_preserves_unknown_without_retry(ocr_paths, product_bytes, monkeypatch):
    with TestClient(app) as client:
        doc, payload = _prepare(client, product_bytes)
        seen = []

        def racing_recognize(path, **kwargs):
            seen.append(1)
            changed = client.post(
                f"/api/designs/{doc}/commands",
                json={
                    "command_id": "ocr-inference-race-0001",
                    "expected_revision": 1,
                    "actor": "user",
                    "operations": [{"op": "set_text", "node_id": "title", "text": "变更后标题"}],
                },
            )
            assert changed.status_code == 200, changed.text
            return _ocr_lines()

        monkeypatch.setattr(ocr_runs, "recognize", racing_recognize)
        first = client.post(_url(doc), json=payload, headers=_headers())
        assert first.status_code == 201, first.text
        assert first.json()["status"] == "UNKNOWN"
        assert first.json()["reason"] == "SOURCE_CHANGED"
        assert first.json()["observations"] == []
        assert first.json()["release_ready"] is False
        replay = client.post(_url(doc), json=payload, headers=_headers())
        assert replay.status_code == 200
        assert replay.json()["status"] == "UNKNOWN"
        assert len(seen) == 1


def test_abandoned_executing_is_unknown_after_restart(ocr_paths, product_bytes, monkeypatch):
    monkeypatch.setattr(ocr_runs, "recognize", lambda path, **kwargs: _ocr_lines())
    with TestClient(app) as client:
        doc, payload = _prepare(client, product_bytes)
        assert client.post(_url(doc), json=payload, headers=_headers()).status_code == 201

    # Simulate a process termination after durable admission, before the
    # terminal receipt. The next lifespan must NOT silently rerun OCR.
    with sqlite3.connect(ocr_paths) as connection:
        connection.execute(
            "UPDATE design_ocr_runs SET status='EXECUTING', finished_at=NULL WHERE operation_id=?",
            (payload["operation_id"],),
        )
    model_calls = []
    monkeypatch.setattr(ocr_runs, "recognize", lambda path, **kwargs: model_calls.append(1))
    with TestClient(app) as restarted:
        lookup = restarted.get(_url(doc) + "/" + payload["operation_id"], headers=_headers())
        assert lookup.status_code == 200
        assert lookup.json()["status"] == "UNKNOWN"
        assert lookup.json()["reason"] == "RECOVERED_AFTER_RESTART"
        replay = restarted.post(_url(doc), json=payload, headers=_headers())
        assert replay.status_code == 200
        assert replay.json()["status"] == "UNKNOWN"
        assert model_calls == []


def test_missing_target_or_missing_engine_never_fabricates_execution(
    ocr_paths, product_bytes, monkeypatch
):
    with TestClient(app) as client:
        doc, payload = _prepare(client, product_bytes, cta=None)
        no_cta = client.post(_url(doc), json=payload, headers=_headers())
        assert no_cta.status_code == 409
        assert no_cta.json()["error"]["code"] == "OCR_TARGET_INCOMPLETE"

        complete_doc, valid_payload = _prepare(client, product_bytes)

        def unavailable(path, **kwargs):
            raise OCRUnavailable("model unavailable")

        monkeypatch.setattr(ocr_runs, "recognize", unavailable)
        response = client.post(_url(complete_doc), json=valid_payload, headers=_headers())
        assert response.status_code == 201
        assert response.json()["status"] == "UNAVAILABLE"
        assert response.json()["runtime_attested"] is False
        assert response.json()["observations"] == []


def test_invalid_model_result_is_unknown_not_verified(ocr_paths, product_bytes, monkeypatch):
    monkeypatch.setattr(
        ocr_runs,
        "recognize",
        lambda path, **kwargs: OCRLines(
            [{"text": "spoofed", "score": None, "bbox": [0, 0, 20, 20]}],
            {"attested": True},
        ),
    )
    with TestClient(app) as client:
        doc, payload = _prepare(client, product_bytes)
        response = client.post(_url(doc), json=payload, headers=_headers())
        assert response.status_code == 201
        assert response.json()["status"] == "UNKNOWN"
        assert response.json()["observations"] == []
        assert response.json()["release_ready"] is False


def test_missing_model_profile_rejects_before_journal_admission(
    ocr_paths, product_bytes, monkeypatch
):
    # Configuration is frozen at lifespan startup, not mutable per request.
    monkeypatch.delenv("POSTER_DESIGN_OCR_MODEL_CONFIG_JSON")
    calls = []
    monkeypatch.setattr(ocr_runs, "recognize", lambda path, **kwargs: calls.append(1))
    with TestClient(app) as client:
        doc, payload = _prepare(client, product_bytes)
        rejected = client.post(_url(doc), json=payload, headers=_headers())
        assert rejected.status_code == 503
        assert rejected.json()["error"]["code"] == "OCR_LOCAL_MODEL_PROFILE_UNAVAILABLE"
        assert calls == []
    with sqlite3.connect(ocr_paths) as conn:
        assert conn.execute("SELECT COUNT(*) FROM design_ocr_runs").fetchone()[0] == 0


def test_changed_local_model_is_not_used_or_journaled(ocr_paths, product_bytes, monkeypatch):
    calls = []
    monkeypatch.setattr(ocr_runs, "recognize", lambda path, **kwargs: calls.append(1))
    with TestClient(app) as client:
        doc, payload = _prepare(client, product_bytes)
        (ocr_paths.parent / "local-detector" / "weights.bin").write_bytes(b"tampered!")
        rejected = client.post(_url(doc), json=payload, headers=_headers())
        assert rejected.status_code == 503
        assert rejected.json()["error"]["code"] == "OCR_LOCAL_MODEL_PROFILE_UNAVAILABLE"
        assert calls == []
    with sqlite3.connect(ocr_paths) as conn:
        assert conn.execute("SELECT COUNT(*) FROM design_ocr_runs").fetchone()[0] == 0


def test_model_mutation_during_inference_remains_unknown(ocr_paths, product_bytes, monkeypatch):
    received_model_dirs = []

    def mutate_model_after_admission(path, **kwargs):
        received_model_dirs.append(kwargs["model_dirs"])
        (ocr_paths.parent / "local-recognizer" / "weights.bin").write_bytes(b"mutated-in-flight")
        return _ocr_lines()

    monkeypatch.setattr(ocr_runs, "recognize", mutate_model_after_admission)
    with TestClient(app) as client:
        doc, payload = _prepare(client, product_bytes)
        result = client.post(_url(doc), json=payload, headers=_headers())
        assert result.status_code == 201, result.text
        assert result.json()["status"] == "UNKNOWN"
        assert result.json()["reason"] == "MODEL_PROFILE_CHANGED"
        assert result.json()["observations"] == []
        assert result.json()["release_ready"] is False
        assert len(received_model_dirs) == 1
        assert received_model_dirs[0][0].endswith("local-detector")
        assert received_model_dirs[0][1].endswith("local-recognizer")


def test_old_sqlite_journal_without_model_column_migrates_in_place(
    ocr_paths, product_bytes, monkeypatch
):
    monkeypatch.setattr(ocr_runs, "recognize", lambda path, **kwargs: _ocr_lines())
    with TestClient(app) as client:
        doc, payload = _prepare(client, product_bytes)
        created = client.post(_url(doc), json=payload, headers=_headers())
        assert created.status_code == 201, created.text
        assert created.json()["status"] == "EXECUTED"

    with sqlite3.connect(ocr_paths) as conn:
        conn.execute("ALTER TABLE design_ocr_runs DROP COLUMN model_profile_sha256")
    with TestClient(app) as restarted:
        recorded = restarted.get(_url(doc) + "/" + payload["operation_id"], headers=_headers())
        assert recorded.status_code == 200, recorded.text
        assert recorded.json()["status"] == "EXECUTED"
        assert recorded.json()["model_profile_sha256"] is None
        assert recorded.json()["release_ready"] is False


def test_journaled_ocr_comparison_revalidates_exact_run_without_reexecuting(
    ocr_paths, product_bytes, monkeypatch
):
    calls = []
    def predict(image, **kwargs):
        calls.append(image.read_bytes())
        return _ocr_lines()

    monkeypatch.setattr(ocr_runs, "recognize", predict)
    with TestClient(app) as client:
        doc, payload = _prepare(client, product_bytes)
        initial = client.post(_url(doc), json=payload, headers=_headers())
        assert initial.status_code == 201, initial.text
        assert initial.json()["status"] == "EXECUTED"
        comparison_url = _url(doc) + "/" + payload["operation_id"] + "/comparison-preview"
        compared = client.get(comparison_url, headers=_headers())
        assert compared.status_code == 200, compared.text
        report = compared.json()
        assert report["operation_id"] == payload["operation_id"]
        assert report["journal_status"] == "EXECUTED"
        assert report["source_state"] == "CURRENT_EXECUTED_UNATTESTED"
        assert report["svg_sha256"] == payload["svg_sha256"]
        assert report["png_sha256"] == payload["png_sha256"]
        assert report["declared_consistency"] == "MATCHES_EXPECTED"
        assert all(item["status"] == "MATCHED" for item in report["field_checks"])
        assert report["ocr_status"] == "OBSERVATIONS_UNATTESTED"
        assert report["runtime_attested"] is False
        assert report["quality_verified"] is False
        assert report["release_ready"] is False
        assert compared.headers["cache-control"] == "no-store"
        assert len(calls) == 1
        assert client.get(comparison_url).status_code == 401
        assert client.get(comparison_url, headers=_headers(APPROVER)).status_code == 403

    with TestClient(app) as restarted:
        read_again = restarted.get(comparison_url, headers=_headers())
        assert read_again.status_code == 200, read_again.text
        assert read_again.json()["comparison_fingerprint_sha256"] == report[
            "comparison_fingerprint_sha256"
        ]
        assert len(calls) == 1

        changed = restarted.post(
            f"/api/designs/{doc}/commands",
            json={
                "command_id": "cmd-journaled-ocr-change-0001",
                "expected_revision": 1,
                "actor": "user",
                "operations": [{"op": "set_text", "node_id": "title", "text": "新版标题"}],
            },
        )
        assert changed.status_code == 200, changed.text
        stale = restarted.get(comparison_url, headers=_headers())
        assert stale.status_code == 409, stale.text
        assert stale.json()["error"]["code"] == "REVISION_CONFLICT"
        assert len(calls) == 1


def test_journaled_ocr_comparison_never_promotes_unknown_or_corrupt_source(
    ocr_paths, product_bytes, monkeypatch
):
    monkeypatch.setattr(ocr_runs, "recognize", lambda image, **kwargs: _ocr_lines())
    with TestClient(app) as client:
        doc, payload = _prepare(client, product_bytes)
        assert client.post(_url(doc), json=payload, headers=_headers()).status_code == 201
        endpoint = _url(doc) + "/" + payload["operation_id"] + "/comparison-preview"

        with sqlite3.connect(ocr_paths) as connection:
            connection.execute(
                "UPDATE design_ocr_runs SET status='UNKNOWN', reason='RECOVERED_AFTER_RESTART'"
                " WHERE operation_id=?",
                (payload["operation_id"],),
            )
        uncertain = client.get(endpoint, headers=_headers())
        assert uncertain.status_code == 409, uncertain.text
        assert uncertain.json()["error"]["code"] == "OCR_RUN_NOT_EXECUTED"
        assert uncertain.json()["error"]["message"]

        with sqlite3.connect(ocr_paths) as connection:
            connection.execute(
                "UPDATE design_ocr_runs SET status='EXECUTED', svg_sha256=?"
                " WHERE operation_id=?",
                ("0" * 64, payload["operation_id"]),
            )
        wrong_source = client.get(endpoint, headers=_headers())
        assert wrong_source.status_code == 409, wrong_source.text
        assert wrong_source.json()["error"]["code"] == "OCR_SOURCE_MISMATCH"
        assert client.get(
            _url(doc) + "/" + "ocr_" + "f" * 32 + "/comparison-preview",
            headers=_headers(),
        ).status_code == 404
