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
        {
            "source": "paddleocr_adapter",
            "line_count": 3,
            "attested": True,
            "inference_started_at": "2026-11-23T03:00:00+00:00",
            "inference_finished_at": "2026-11-23T03:00:00.125000+00:00",
            "inference_elapsed_ms": 125.0,
        },
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
        assert data["runtime"]["inference_started_at"] == "2026-11-23T03:00:00+00:00"
        assert data["runtime"]["inference_finished_at"] == "2026-11-23T03:00:00.125000+00:00"
        assert data["runtime"]["inference_elapsed_ms"] == 125.0
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
        assert (
            read_again.json()["comparison_fingerprint_sha256"]
            == report["comparison_fingerprint_sha256"]
        )
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
            observations_json, runtime_json = connection.execute(
                "SELECT observations_json, runtime_json FROM design_ocr_runs WHERE operation_id=?",
                (payload["operation_id"],),
            ).fetchone()
            connection.execute(
                "UPDATE design_ocr_runs SET status='UNKNOWN', observations_json=NULL,"
                " runtime_json=NULL, reason='RECOVERED_AFTER_RESTART' WHERE operation_id=?",
                (payload["operation_id"],),
            )
        uncertain = client.get(endpoint, headers=_headers())
        assert uncertain.status_code == 409, uncertain.text
        assert uncertain.json()["error"]["code"] == "OCR_RUN_NOT_EXECUTED"
        assert uncertain.json()["error"]["message"]

        with sqlite3.connect(ocr_paths) as connection:
            connection.execute(
                "UPDATE design_ocr_runs SET status='EXECUTED', observations_json=?, runtime_json=?,"
                " reason=NULL, svg_sha256=? WHERE operation_id=?",
                (observations_json, runtime_json, "0" * 64, payload["operation_id"]),
            )
        wrong_source = client.get(endpoint, headers=_headers())
        assert wrong_source.status_code == 409, wrong_source.text
        assert wrong_source.json()["error"]["code"] == "OCR_SOURCE_MISMATCH"
        assert (
            client.get(
                _url(doc) + "/" + "ocr_" + "f" * 32 + "/comparison-preview",
                headers=_headers(),
            ).status_code
            == 404
        )


def test_journaled_ocr_comparison_blocks_model_and_raster_drift(
    ocr_paths, product_bytes, monkeypatch
):
    monkeypatch.setattr(ocr_runs, "recognize", lambda image, **kwargs: _ocr_lines())
    with TestClient(app) as client:
        doc, payload = _prepare(client, product_bytes)
        assert client.post(_url(doc), json=payload, headers=_headers()).status_code == 201
        endpoint = _url(doc) + "/" + payload["operation_id"] + "/comparison-preview"

        weights = ocr_paths.parent / "local-detector" / "weights.bin"
        original = weights.read_bytes()
        weights.write_bytes(b"corrupted-local-model")
        changed_model = client.get(endpoint, headers=_headers())
        assert changed_model.status_code == 503, changed_model.text
        assert changed_model.json()["error"]["code"] == "OCR_LOCAL_MODEL_PROFILE_UNAVAILABLE"
        weights.write_bytes(original)

        alternate = BytesIO()
        Image.new("RGB", (1024, 1024), (200, 23, 34)).save(alternate, format="PNG")
        monkeypatch.setattr(
            rasterization,
            "_convert_svg_to_png",
            lambda svg, width, height: (alternate.getvalue(), "test-raster/1"),
        )
        changed_raster = client.get(endpoint, headers=_headers())
        assert changed_raster.status_code == 409, changed_raster.text
        assert changed_raster.json()["error"]["code"] == "OCR_SOURCE_MISMATCH"


def test_ocr_evidence_bundle_exports_exact_pixels_and_unattested_receipt(
    ocr_paths, product_bytes, monkeypatch
):
    import base64

    calls = []

    def infer(image, **kwargs):
        calls.append(image.read_bytes())
        return _ocr_lines()

    monkeypatch.setattr(ocr_runs, "recognize", infer)
    with TestClient(app) as client:
        doc, payload = _prepare(client, product_bytes)
        assert client.post(_url(doc), json=payload, headers=_headers()).status_code == 201
        endpoint = _url(doc) + "/" + payload["operation_id"] + "/evidence-bundle"
        assert client.get(endpoint).status_code == 401
        assert client.get(endpoint, headers=_headers(APPROVER)).status_code == 403

        response = client.get(endpoint, headers=_headers())
        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "no-store"
        bundle = response.json()
        assert bundle["schema_version"] == 1
        assert bundle["evidence_kind"] == "LOCAL_OCR_UNATTESTED"
        png = base64.b64decode(bundle["png_base64"], validate=True)
        assert calls == [png]
        assert hashlib.sha256(png).hexdigest() == payload["png_sha256"]
        assert bundle["journal"]["png_sha256"] == payload["png_sha256"]
        assert bundle["comparison"]["svg_sha256"] == payload["svg_sha256"]
        assert bundle["journal"]["operation_id"] == payload["operation_id"]
        assert bundle["journal"]["observations"] == list(_ocr_lines())
        assert bundle["runtime_attested"] is False
        assert bundle["quality_verified"] is False
        assert bundle["release_ready"] is False
        digest = bundle.pop("bundle_sha256")
        canonical = json.dumps(
            bundle, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
        assert hashlib.sha256(canonical.encode("utf-8")).hexdigest() == digest
        assert EXPORTER not in canonical
        assert str(ocr_paths.parent) not in canonical
        repeated = client.get(endpoint, headers=_headers())
        assert repeated.status_code == 200, repeated.text
        assert repeated.json()["bundle_sha256"] == digest
        assert len(calls) == 1

        changed = client.post(
            f"/api/designs/{doc}/commands",
            json={
                "command_id": "cmd-ocr-evidence-bundle-stale",
                "expected_revision": 1,
                "actor": "user",
                "operations": [{"op": "set_text", "node_id": "title", "text": "新版标题"}],
            },
        )
        assert changed.status_code == 200, changed.text
        assert client.get(endpoint, headers=_headers()).status_code == 409
        assert len(calls) == 1


def test_ocr_evidence_bundle_refuses_unknown_operation(ocr_paths, product_bytes, monkeypatch):
    def unavailable(image, **kwargs):
        raise OCRUnavailable("synthetic unavailable")

    monkeypatch.setattr(ocr_runs, "recognize", unavailable)
    with TestClient(app) as client:
        doc, payload = _prepare(client, product_bytes)
        first = client.post(_url(doc), json=payload, headers=_headers())
        assert first.status_code == 201, first.text
        assert first.json()["status"] == "UNAVAILABLE"
        endpoint = _url(doc) + "/" + payload["operation_id"] + "/evidence-bundle"
        response = client.get(endpoint, headers=_headers())
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "OCR_RUN_NOT_EXECUTED"


@pytest.mark.parametrize("drift", ["model", "journal"])
def test_ocr_comparison_rechecks_sources_after_rasterization(
    ocr_paths, product_bytes, monkeypatch, drift
):
    calls = []

    def infer(image, **kwargs):
        calls.append(1)
        return _ocr_lines()

    monkeypatch.setattr(ocr_runs, "recognize", infer)
    with TestClient(app) as client:
        doc, payload = _prepare(client, product_bytes)
        assert client.post(_url(doc), json=payload, headers=_headers()).status_code == 201
        endpoint = _url(doc) + "/" + payload["operation_id"] + "/comparison-preview"
        original_converter = rasterization._convert_svg_to_png

        def drift_after_preflight(svg, width, height):
            if drift == "model":
                weights = ocr_paths.parent / "local-detector" / "weights.bin"
                weights.write_bytes(b"changed after comparison preflight")
            else:
                with sqlite3.connect(ocr_paths) as connection:
                    connection.execute(
                        "UPDATE design_ocr_runs SET observations_json = ? WHERE operation_id = ?",
                        (json.dumps([]), payload["operation_id"]),
                    )
                    runtime = connection.execute(
                        "SELECT runtime_json FROM design_ocr_runs WHERE operation_id = ?",
                        (payload["operation_id"],),
                    ).fetchone()[0]
                    updated_runtime = json.loads(runtime)
                    updated_runtime["line_count"] = 0
                    connection.execute(
                        "UPDATE design_ocr_runs SET runtime_json = ? WHERE operation_id = ?",
                        (json.dumps(updated_runtime), payload["operation_id"]),
                    )
            return original_converter(svg, width, height)

        monkeypatch.setattr(rasterization, "_convert_svg_to_png", drift_after_preflight)
        result = client.get(endpoint, headers=_headers())
        if drift == "model":
            assert result.status_code == 503, result.text
            assert result.json()["error"]["code"] == "OCR_LOCAL_MODEL_PROFILE_UNAVAILABLE"
        else:
            assert result.status_code == 409, result.text
            assert result.json()["error"]["code"] == "OCR_SOURCE_MISMATCH"
        assert calls == [1]


@pytest.mark.parametrize(
    ("price_bbox", "spatial_state", "price_box_state"),
    [
        (None, "BLOCKED", "MISSING_BBOX"),
        ([2, 65, 210, 115], "BOUNDS_ONLY_UNCALIBRATED", "BOX_PRESENT_UNCALIBRATED"),
    ],
)
def test_journaled_comparison_does_not_confuse_text_match_with_spatial_evidence(
    ocr_paths, product_bytes, monkeypatch, price_bbox, spatial_state, price_box_state
):
    lines = _ocr_lines()
    lines[1]["bbox"] = price_bbox
    monkeypatch.setattr(ocr_runs, "recognize", lambda image, **kwargs: lines)

    with TestClient(app) as client:
        doc, payload = _prepare(client, product_bytes)
        executed = client.post(_url(doc), json=payload, headers=_headers())
        assert executed.status_code == 201, executed.text
        assert executed.json()["status"] == "EXECUTED"

        endpoint = _url(doc) + "/" + payload["operation_id"] + "/comparison-preview"
        response = client.get(endpoint, headers=_headers())
        assert response.status_code == 200, response.text
        report = response.json()
        assert report["declared_consistency"] == "MATCHES_EXPECTED"
        assert report["spatial_evidence_status"] == spatial_state
        by_field = {item["field"]: item for item in report["spatial_field_checks"]}
        assert by_field["price"]["status"] == price_box_state
        assert by_field["price"]["bbox"] == (
            price_bbox if spatial_state == "BOUNDS_ONLY_UNCALIBRATED" else None
        )
        assert len(report["journaled_observations_sha256"]) == 64
        assert len(report["spatial_comparison_fingerprint_sha256"]) == 64
        assert report["runtime_attested"] is False
        assert report["quality_verified"] is False
        assert report["release_ready"] is False


def test_journaled_comparison_exposes_non_authoritative_design_node_hints(
    ocr_paths, product_bytes, monkeypatch
):
    monkeypatch.setattr(ocr_runs, "recognize", lambda image, **kwargs: _ocr_lines())
    with TestClient(app) as client:
        doc, payload = _prepare(client, product_bytes)
        executed = client.post(_url(doc), json=payload, headers=_headers())
        assert executed.status_code == 201, executed.text
        response = client.get(
            _url(doc) + "/" + payload["operation_id"] + "/comparison-preview",
            headers=_headers(),
        )
        assert response.status_code == 200, response.text
        report = response.json()
        assert report["design_node_overlap_schema_version"] == 1
        assert len(report["design_node_overlap_checks"]) == 3
        assert {item["field"] for item in report["design_node_overlap_checks"]} == {
            "title",
            "price",
            "cta",
        }
        assert report["design_node_overlap_status"] in {"BLOCKED", "OVERLAP_ONLY_UNCALIBRATED"}
        assert len(report["design_node_overlap_fingerprint_sha256"]) == 64
        assert report["runtime_attested"] is False
        assert report["quality_verified"] is False
        assert report["release_ready"] is False


def test_journaled_comparison_fingerprint_covers_original_bbox(
    ocr_paths, product_bytes, monkeypatch
):
    monkeypatch.setattr(ocr_runs, "recognize", lambda image, **kwargs: _ocr_lines())
    with TestClient(app) as client:
        doc, payload = _prepare(client, product_bytes)
        assert client.post(_url(doc), json=payload, headers=_headers()).status_code == 201
        endpoint = _url(doc) + "/" + payload["operation_id"] + "/comparison-preview"
        before = client.get(endpoint, headers=_headers()).json()

        # Deliberate local journal rewrite models the absence of independent
        # attestation; the new diagnostic digest must bind even box-only edits.
        with sqlite3.connect(ocr_paths) as connection:
            old = connection.execute(
                "SELECT observations_json FROM design_ocr_runs WHERE operation_id = ?",
                (payload["operation_id"],),
            ).fetchone()[0]
            rows = json.loads(old)
            rows[1]["bbox"][0] += 1
            connection.execute(
                "UPDATE design_ocr_runs SET observations_json = ? WHERE operation_id = ?",
                (json.dumps(rows, ensure_ascii=False), payload["operation_id"]),
            )
        after_response = client.get(endpoint, headers=_headers())
        assert after_response.status_code == 200, after_response.text
        after = after_response.json()
        assert before["observations_sha256"] == after["observations_sha256"]
        assert before["journaled_observations_sha256"] != after["journaled_observations_sha256"]
        assert (
            before["spatial_comparison_fingerprint_sha256"]
            != after["spatial_comparison_fingerprint_sha256"]
        )
        assert after["quality_verified"] is False


@pytest.mark.parametrize(
    "corrupted_bbox",
    [
        [True, 65, 210, 115],
        [10, 65, 210, float("nan")],
    ],
)
def test_journaled_comparison_rejects_corrupted_stored_geometry(
    ocr_paths, product_bytes, monkeypatch, corrupted_bbox
):
    monkeypatch.setattr(ocr_runs, "recognize", lambda image, **kwargs: _ocr_lines())
    with TestClient(app) as client:
        doc, payload = _prepare(client, product_bytes)
        assert client.post(_url(doc), json=payload, headers=_headers()).status_code == 201
        with sqlite3.connect(ocr_paths) as connection:
            source = connection.execute(
                "SELECT observations_json FROM design_ocr_runs WHERE operation_id = ?",
                (payload["operation_id"],),
            ).fetchone()[0]
            lines = json.loads(source)
            lines[1]["bbox"] = corrupted_bbox
            connection.execute(
                "UPDATE design_ocr_runs SET observations_json = ? WHERE operation_id = ?",
                (json.dumps(lines, ensure_ascii=False), payload["operation_id"]),
            )
        endpoint = _url(doc) + "/" + payload["operation_id"] + "/comparison-preview"
        response = client.get(endpoint, headers=_headers())
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "OCR_SOURCE_MISMATCH"


@pytest.mark.parametrize("journal_column", ["observations_json", "runtime_json"])
def test_corrupted_ocr_journal_json_fails_closed_on_read(
    ocr_paths, product_bytes, monkeypatch, journal_column
):
    monkeypatch.setattr(ocr_runs, "recognize", lambda image, **kwargs: _ocr_lines())
    with TestClient(app) as client:
        doc, payload = _prepare(client, product_bytes)
        response = client.post(_url(doc), json=payload, headers=_headers())
        assert response.status_code == 201, response.text
        with sqlite3.connect(ocr_paths) as connection:
            connection.execute(
                f"UPDATE design_ocr_runs SET {journal_column} = ? WHERE operation_id = ?",
                ("{bad-json", payload["operation_id"]),
            )
        lookup = client.get(_url(doc) + "/" + payload["operation_id"], headers=_headers())
        assert lookup.status_code == 409, lookup.text
        assert lookup.json()["error"]["code"] == "OCR_SOURCE_MISMATCH"
        preview = client.get(
            _url(doc) + "/" + payload["operation_id"] + "/comparison-preview",
            headers=_headers(),
        )
        assert preview.status_code == 409, preview.text
        assert preview.json()["error"]["code"] == "OCR_SOURCE_MISMATCH"


@pytest.mark.parametrize(
    "invalid_bbox",
    [
        [2, 65, 2, 115],  # zero-width candidate
        [2, 65, 1030, 115],  # beyond the approved 1024px raster
        [2, 65, 210, 1025],  # beyond the approved raster height
        [2, 115, 210, 65],  # inverted vertical extent
    ],
)
def test_unusable_ocr_geometry_is_not_recorded_as_executed(
    ocr_paths, product_bytes, monkeypatch, invalid_bbox
):
    calls = []
    lines = _ocr_lines()
    lines[1]["bbox"] = invalid_bbox

    def invalid_engine_output(image, **kwargs):
        calls.append(image.read_bytes())
        return lines

    monkeypatch.setattr(ocr_runs, "recognize", invalid_engine_output)
    with TestClient(app) as client:
        doc, payload = _prepare(client, product_bytes)
        first = client.post(_url(doc), json=payload, headers=_headers())
        assert first.status_code == 201, first.text
        assert first.json()["status"] == "UNKNOWN"
        assert first.json()["reason"] == "OCR_EXECUTION_AMBIGUOUS"
        assert first.json()["observations"] == []
        assert first.json()["quality_verified"] is False
        assert first.json()["release_ready"] is False

        replay = client.post(_url(doc), json=payload, headers=_headers())
        assert replay.status_code == 200
        assert replay.json()["status"] == "UNKNOWN"
        assert replay.json()["replayed"] is True
        assert len(calls) == 1

    with sqlite3.connect(ocr_paths) as connection:
        row = connection.execute(
            "SELECT status, observations_json FROM design_ocr_runs WHERE operation_id = ?",
            (payload["operation_id"],),
        ).fetchone()
        assert row == ("UNKNOWN", None)


@pytest.mark.parametrize("mutation", ["png_bytes", "rasterizer", "raster_unavailable"])
def test_postflight_ocr_raster_drift_is_unknown_and_cannot_reinvoke(
    ocr_paths, product_bytes, monkeypatch, mutation
):
    """OCR output must match both the originally approved pixels and rasterizer."""
    original_converter = rasterization._convert_svg_to_png
    model_calls = []

    def changed_converter(svg, width, height):
        if mutation == "raster_unavailable":
            raise rasterization.RasterUnavailable("postflight renderer unavailable")
        png, engine = original_converter(svg, width, height)
        if mutation == "rasterizer":
            return png, "test-raster/changed"
        with BytesIO() as buffer:
            Image.new("RGB", (width, height), (22, 33, 44)).save(buffer, "PNG")
            return buffer.getvalue(), engine

    def infer(image, **kwargs):
        model_calls.append(image.read_bytes())
        monkeypatch.setattr(rasterization, "_convert_svg_to_png", changed_converter)
        return _ocr_lines()

    monkeypatch.setattr(ocr_runs, "recognize", infer)
    with TestClient(app) as client:
        doc, payload = _prepare(client, product_bytes)
        first = client.post(_url(doc), json=payload, headers=_headers())
        assert first.status_code == 201, first.text
        assert first.json()["status"] == "UNKNOWN"
        assert first.json()["reason"] == "SOURCE_CHANGED"
        assert first.json()["observations"] == []
        assert first.json()["runtime"] is None
        assert first.json()["quality_verified"] is False
        assert first.json()["release_ready"] is False

        replay = client.post(_url(doc), json=payload, headers=_headers())
        assert replay.status_code == 200, replay.text
        assert replay.json()["status"] == "UNKNOWN"
        assert replay.json()["replayed"] is True
        assert len(model_calls) == 1

    with sqlite3.connect(ocr_paths) as connection:
        row = connection.execute(
            "SELECT status, observations_json, runtime_json FROM design_ocr_runs"
            " WHERE operation_id = ?",
            (payload["operation_id"],),
        ).fetchone()
        assert row == ("UNKNOWN", None, None)


@pytest.mark.parametrize(
    "corruption",
    [
        "observation_extra_field",
        "observation_duplicate_key",
        "observation_non_finite_score",
        "runtime_claims_attestation",
        "runtime_extra_authority",
        "runtime_line_count_mismatch",
        "runtime_timing_partial",
        "runtime_timing_naive",
        "runtime_timing_reversed",
        "runtime_timing_bool",
        "missing_runtime_for_pinned_model",
        "unknown_retains_observations",
        "unavailable_retains_runtime",
    ],
)
def test_corrupt_ocr_journal_is_never_replayed_as_valid_evidence(
    ocr_paths, product_bytes, monkeypatch, corruption
):
    calls = []

    def fake_recognize(path, **kwargs):
        calls.append(path.read_bytes())
        return _ocr_lines()

    monkeypatch.setattr(ocr_runs, "recognize", fake_recognize)
    with TestClient(app) as client:
        doc, payload = _prepare(client, product_bytes)
        created = client.post(_url(doc), json=payload, headers=_headers())
        assert created.status_code == 201, created.text
        assert created.json()["status"] == "EXECUTED"
        assert len(calls) == 1

        with sqlite3.connect(ocr_paths) as connection:
            observations = connection.execute(
                "SELECT observations_json FROM design_ocr_runs WHERE operation_id = ?",
                (payload["operation_id"],),
            ).fetchone()[0]
            runtime = connection.execute(
                "SELECT runtime_json FROM design_ocr_runs WHERE operation_id = ?",
                (payload["operation_id"],),
            ).fetchone()[0]
            runtime_data = json.loads(runtime)

            if corruption == "observation_extra_field":
                connection.execute(
                    "UPDATE design_ocr_runs SET observations_json = ? WHERE operation_id = ?",
                    (
                        json.dumps(
                            [{**json.loads(observations)[0], "release_ready": True}]
                            + json.loads(observations)[1:]
                        ),
                        payload["operation_id"],
                    ),
                )
            elif corruption == "observation_duplicate_key":
                connection.execute(
                    "UPDATE design_ocr_runs SET observations_json = ? WHERE operation_id = ?",
                    (
                        '[{"text":"safe","text":"spoofed","score":0.9,"bbox":null}]',
                        payload["operation_id"],
                    ),
                )
            elif corruption == "observation_non_finite_score":
                connection.execute(
                    "UPDATE design_ocr_runs SET observations_json = ? WHERE operation_id = ?",
                    (
                        '[{"text":"not-finite","score":1e999,"bbox":null}]',
                        payload["operation_id"],
                    ),
                )
            elif corruption == "runtime_claims_attestation":
                runtime_data["attested"] = True
                connection.execute(
                    "UPDATE design_ocr_runs SET runtime_json = ? WHERE operation_id = ?",
                    (json.dumps(runtime_data), payload["operation_id"]),
                )
            elif corruption == "runtime_extra_authority":
                runtime_data["release_ready"] = True
                connection.execute(
                    "UPDATE design_ocr_runs SET runtime_json = ? WHERE operation_id = ?",
                    (json.dumps(runtime_data), payload["operation_id"]),
                )
            elif corruption == "runtime_line_count_mismatch":
                runtime_data["line_count"] = 2
                connection.execute(
                    "UPDATE design_ocr_runs SET runtime_json = ? WHERE operation_id = ?",
                    (json.dumps(runtime_data), payload["operation_id"]),
                )
            elif corruption == "runtime_timing_partial":
                runtime_data.pop("inference_finished_at", None)
                connection.execute(
                    "UPDATE design_ocr_runs SET runtime_json = ? WHERE operation_id = ?",
                    (json.dumps(runtime_data), payload["operation_id"]),
                )
            elif corruption == "runtime_timing_naive":
                runtime_data["inference_started_at"] = "2026-11-23T03:00:00"
                connection.execute(
                    "UPDATE design_ocr_runs SET runtime_json = ? WHERE operation_id = ?",
                    (json.dumps(runtime_data), payload["operation_id"]),
                )
            elif corruption == "runtime_timing_reversed":
                runtime_data["inference_finished_at"] = "2026-11-23T02:59:59+00:00"
                connection.execute(
                    "UPDATE design_ocr_runs SET runtime_json = ? WHERE operation_id = ?",
                    (json.dumps(runtime_data), payload["operation_id"]),
                )
            elif corruption == "runtime_timing_bool":
                runtime_data["inference_elapsed_ms"] = True
                connection.execute(
                    "UPDATE design_ocr_runs SET runtime_json = ? WHERE operation_id = ?",
                    (json.dumps(runtime_data), payload["operation_id"]),
                )
            elif corruption == "missing_runtime_for_pinned_model":
                connection.execute(
                    "UPDATE design_ocr_runs SET runtime_json = NULL WHERE operation_id = ?",
                    (payload["operation_id"],),
                )
            elif corruption == "unknown_retains_observations":
                connection.execute(
                    "UPDATE design_ocr_runs SET status = 'UNKNOWN' WHERE operation_id = ?",
                    (payload["operation_id"],),
                )
            elif corruption == "unavailable_retains_runtime":
                connection.execute(
                    "UPDATE design_ocr_runs SET status = 'UNAVAILABLE' WHERE operation_id = ?",
                    (payload["operation_id"],),
                )

        status = client.get(
            _url(doc) + "/" + payload["operation_id"],
            headers=_headers(),
        )
        expected_code = (
            "OCR_SOURCE_MISMATCH"
            if corruption.startswith("observation_")
            else "OCR_JOURNAL_CORRUPT"
        )
        assert status.status_code == 409, status.text
        assert status.json()["error"]["code"] == expected_code

        replay = client.post(_url(doc), json=payload, headers=_headers())
        assert replay.status_code == 409, replay.text
        assert replay.json()["error"]["code"] == expected_code
        assert len(calls) == 1


def test_legacy_runtime_schema_remains_readable_after_timing_extension(
    ocr_paths, product_bytes, monkeypatch
):
    monkeypatch.setattr(ocr_runs, "recognize", lambda image, **kwargs: _ocr_lines())
    with TestClient(app) as client:
        doc, payload = _prepare(client, product_bytes)
        created = client.post(_url(doc), json=payload, headers=_headers())
        assert created.status_code == 201, created.text
        with sqlite3.connect(ocr_paths) as connection:
            runtime = connection.execute(
                "SELECT runtime_json FROM design_ocr_runs WHERE operation_id = ?",
                (payload["operation_id"],),
            ).fetchone()[0]
            runtime_data = json.loads(runtime)
            for key in (
                "inference_started_at",
                "inference_finished_at",
                "inference_elapsed_ms",
            ):
                runtime_data.pop(key, None)
            connection.execute(
                "UPDATE design_ocr_runs SET runtime_json = ? WHERE operation_id = ?",
                (json.dumps(runtime_data), payload["operation_id"]),
            )
        replay = client.get(_url(doc) + "/" + payload["operation_id"], headers=_headers())
        assert replay.status_code == 200, replay.text
        assert replay.json()["runtime"] == runtime_data


def test_duplicate_model_profile_keys_are_rejected_before_operation_admission(
    ocr_paths, product_bytes, monkeypatch
):
    import os

    raw = os.environ["POSTER_DESIGN_OCR_MODEL_CONFIG_JSON"]
    assert '"schema_version": 1' in raw
    monkeypatch.setenv(
        "POSTER_DESIGN_OCR_MODEL_CONFIG_JSON",
        raw.replace('"schema_version": 1', '"schema_version": 0, "schema_version": 1', 1),
    )
    calls = []
    monkeypatch.setattr(
        ocr_runs, "recognize", lambda path, **kwargs: calls.append(1) or _ocr_lines()
    )

    with TestClient(app) as client:
        doc, payload = _prepare(client, product_bytes)
        response = client.post(_url(doc), json=payload, headers=_headers())
        assert response.status_code == 503, response.text
        assert response.json()["error"]["code"] == "OCR_LOCAL_MODEL_PROFILE_UNAVAILABLE"
        assert calls == []
        with sqlite3.connect(ocr_paths) as connection:
            count = connection.execute(
                "SELECT count(*) FROM design_ocr_runs WHERE operation_id = ?",
                (payload["operation_id"],),
            ).fetchone()[0]
        assert count == 0


@pytest.mark.parametrize(
    "mutation", ["none", "png", "journal", "trust_flag", "report", "duplicate", "anchor"]
)
def test_offline_evidence_verifier_rechecks_bundle(
    ocr_paths, product_bytes, monkeypatch, tmp_path, capsys, mutation
):
    from scripts.verify_ocr_evidence_bundle import main

    monkeypatch.setattr(ocr_runs, "recognize", lambda image, **kwargs: _ocr_lines())
    with TestClient(app) as client:
        doc, payload = _prepare(client, product_bytes)
        assert client.post(_url(doc), json=payload, headers=_headers()).status_code == 201
        endpoint = _url(doc) + "/" + payload["operation_id"] + "/evidence-bundle"
        response = client.get(endpoint, headers=_headers())
        assert response.status_code == 200, response.text
        bundle = response.json()

    # A recomputed outer digest cannot conceal inconsistent internal fields.
    if mutation == "png":
        bundle["png_base64"] = "YWJj"
    elif mutation == "journal":
        bundle["journal"]["observations"][0]["text"] = "篡改标题"
    elif mutation == "trust_flag":
        bundle["comparison"]["quality_verified"] = True
    elif mutation == "report":
        bundle["comparison"]["field_checks"][0]["matched_line_index"] = 1

    bundle.pop("bundle_sha256")
    canonical = json.dumps(
        bundle, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    bundle["bundle_sha256"] = digest
    raw = json.dumps(bundle, ensure_ascii=False)
    if mutation == "duplicate":
        raw = '{"schema_version":1,' + raw[1:]
    path = tmp_path / "bundle.json"
    path.write_text(raw, encoding="utf-8")
    expected = "0" * 64 if mutation == "anchor" else digest
    code = main(["--bundle", str(path), "--expected-sha256", expected])
    captured = capsys.readouterr()
    if mutation == "none":
        assert code == 0
        result = json.loads(captured.out)
        assert result["status"] == "CONSISTENT_LOCAL_EVIDENCE_ONLY"
        assert result["bundle_sha256"] == digest
        assert result["runtime_attested"] is False
        assert result["quality_verified"] is False
        assert result["release_ready"] is False
    else:
        assert code == 2
        assert captured.out == ""
