"""Control-flow tests for the explicit DesignDocument OCR smoke runner.

These tests exercise the real local HTTP routes with a synthetic OCR adapter;
they never download or execute PaddleOCR models.
"""

from __future__ import annotations

import hashlib
import json
from io import BytesIO
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

import app.designs.ocr_runs as ocr_runs
import app.designs.rasterization as rasterization
import scripts.run_design_ocr_smoke as smoke
from app.designs.ocr_model_policy import model_directory_sha256
from app.main import app
from app.ocr_provider import OCRLines, OCRUnavailable

APPROVER = "smoke-approver-" + "A" * 48
EXPORTER = "smoke-exporter-" + "B" * 48


@pytest.fixture
def smoke_environment(tmp_path, monkeypatch):
    """Isolate every persistent store and pin synthetic, non-production model files."""
    for variable, name in (
        ("POSTER_DATA_DIR", "posters"),
        ("POSTER_EXECUTION_DB", "executions.sqlite3"),
        ("POSTER_CHECKPOINT_DB", "checkpoints.sqlite3"),
        ("POSTER_DESIGN_DB", "designs.sqlite3"),
        ("POSTER_DESIGN_ASSET_DIR", "design-assets"),
    ):
        monkeypatch.setenv(variable, str(tmp_path / name))
    monkeypatch.setenv("POSTER_DESIGN_OCR_RUNS_ENABLED", "1")

    detector = tmp_path / "detector"
    recognizer = tmp_path / "recognizer"
    for directory in (detector, recognizer):
        directory.mkdir()
        (directory / "inference.json").write_text('{"synthetic":true}', encoding="utf-8")
        (directory / "weights.bin").write_bytes(b"synthetic-test-only-model")

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
                "smoke-approver": {
                    "token_sha256": hashlib.sha256(APPROVER.encode()).hexdigest(),
                    "scopes": ["approve"],
                },
                "smoke-exporter": {
                    "token_sha256": hashlib.sha256(EXPORTER.encode()).hexdigest(),
                    "scopes": ["export"],
                },
            }
        ),
    )

    buffer = BytesIO()
    Image.new("RGB", (1024, 1024), (12, 23, 34)).save(buffer, format="PNG")
    approved_png = buffer.getvalue()
    monkeypatch.setattr(
        rasterization,
        "_convert_svg_to_png",
        lambda svg, width, height: (approved_png, "synthetic-test-raster/1"),
    )
    return tmp_path


def _observations() -> OCRLines:
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
            "inference_started_at": "2026-11-24T03:00:00+00:00",
            "inference_finished_at": "2026-11-24T03:00:00.125000+00:00",
            "inference_elapsed_ms": 125.0,
        },
    )


def _run(client, root: Path, *, keep_approved: bool = False) -> tuple[Path, Path]:
    output = root / "private-evidence"
    anchor = root / "separate-anchor" / "bundle.sha256"
    anchor.parent.mkdir()
    smoke.run_smoke(client, output, anchor, APPROVER, EXPORTER, keep_approved=keep_approved)
    return output, anchor


def test_smoke_runs_one_design_document_operation_and_verifies_bundle(
    smoke_environment, monkeypatch
):
    calls = []

    def recognize(path, **kwargs):
        calls.append(path.read_bytes())
        return _observations()

    monkeypatch.setattr(ocr_runs, "recognize", recognize)
    with TestClient(app) as client:
        output, anchor = _run(client, smoke_environment)

    outcome = json.loads((output / "outcome.json").read_text(encoding="utf-8"))
    journal = json.loads((output / "journal.json").read_text(encoding="utf-8"))
    assert outcome["status"] == "LOCAL_SMOKE_CONSISTENT_UNATTESTED"
    assert outcome["execution_state"] == "EXECUTED"
    assert outcome["stale_revision_rejected"] is True
    assert outcome["runtime_attested"] is False
    assert outcome["quality_verified"] is False
    assert outcome["release_ready"] is False
    assert journal["status"] == "EXECUTED"
    assert journal["principal_id"] == "smoke-exporter"
    assert len(journal["observations"]) == 3
    assert (output / "evidence.json").is_file()
    assert (output / "stale-rejection.json").is_file()
    assert anchor.read_text(encoding="utf-8").strip() == outcome["bundle_sha256"]
    assert len(calls) == 1
    assert Path(output).stat().st_mode & 0o777 == 0o700


def test_smoke_maps_unavailable_engine_without_replay_or_success_digest(
    smoke_environment, monkeypatch
):
    calls = []

    def unavailable(path, **kwargs):
        calls.append(path.read_bytes())
        raise OCRUnavailable("synthetic engine unavailable")

    monkeypatch.setattr(ocr_runs, "recognize", unavailable)
    output = smoke_environment / "private-evidence"
    anchor = smoke_environment / "separate-anchor" / "bundle.sha256"
    anchor.parent.mkdir()

    with TestClient(app) as client, pytest.raises(smoke.SmokeError):
        smoke.run_smoke(client, output, anchor, APPROVER, EXPORTER)

    outcome = json.loads((output / "outcome.json").read_text(encoding="utf-8"))
    journal = json.loads((output / "journal.json").read_text(encoding="utf-8"))
    assert outcome == {
        "status": "INCOMPLETE",
        "stage": "submit",
        "operation_id": journal["operation_id"],
        "execution_state": "UNAVAILABLE",
        "runtime_attested": False,
        "quality_verified": False,
        "release_ready": False,
    }
    assert journal["status"] == "UNAVAILABLE"
    assert journal["reason"] == "MODEL_UNAVAILABLE"
    assert not (output / "evidence.json").exists()
    assert not anchor.exists()
    assert len(calls) == 1


def test_smoke_ack_loss_preserves_unknown_and_never_reposts_operation(
    smoke_environment, monkeypatch
):
    calls = []
    submit_paths = []
    real_request = smoke._request

    def recognize(path, **kwargs):
        calls.append(path.read_bytes())
        return _observations()

    def lose_submit_ack(client, method, path, expected, **kwargs):
        result = real_request(client, method, path, expected, **kwargs)
        if method == "POST" and path.endswith("/ocr-runs"):
            submit_paths.append(path)
            raise httpx.ReadTimeout("synthetic response loss after server commit")
        return result

    monkeypatch.setattr(ocr_runs, "recognize", recognize)
    monkeypatch.setattr(smoke, "_request", lose_submit_ack)
    output = smoke_environment / "private-evidence"
    anchor = smoke_environment / "separate-anchor" / "bundle.sha256"
    anchor.parent.mkdir()

    with TestClient(app) as client:
        with pytest.raises(smoke.SmokeError):
            smoke.run_smoke(client, output, anchor, APPROVER, EXPORTER)

        outcome = json.loads((output / "outcome.json").read_text(encoding="utf-8"))
        source = json.loads((output / "source.json").read_text(encoding="utf-8"))
        request = json.loads((output / "operation-request.json").read_text(encoding="utf-8"))
        endpoint = (
            f"/api/designs/{source['document_id']}/revisions/{source['revision']}"
            f"/ocr-runs/{request['operation_id']}"
        )
        observed = client.get(endpoint, headers={"Authorization": "Bearer " + EXPORTER})

    assert observed.status_code == 200, observed.text
    assert observed.json()["status"] == "EXECUTED"
    assert outcome["status"] == "INCOMPLETE"
    assert outcome["stage"] == "submit"
    assert outcome["execution_state"] == "UNKNOWN"
    assert outcome["runtime_attested"] is False
    assert outcome["quality_verified"] is False
    assert outcome["release_ready"] is False
    assert len(submit_paths) == 1
    assert len(calls) == 1
    assert not (output / "journal.json").exists()
    assert not (output / "evidence.json").exists()
    assert not anchor.exists()
