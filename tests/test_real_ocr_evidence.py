"""Tests of evidence validation, never proof of genuine PaddleOCR inference.

Mocked rows and environments intentionally exercise the failure-closed contract.
Only the separate real-ocr GitHub Actions run can verify a real model process.
"""

from __future__ import annotations

import json

import pytest
from PIL import Image

from evals import ocr_runtime
from evals.image_fixtures import generate_fixture_set
from evals.image_runner import evaluate_image_manifest
from scripts.verify_real_ocr_evidence import EvidenceInvalid, verify_batch, verify_probe


def _fake_env():
    return {
        "platform": "Linux",
        "machine": "x86_64",
        "python": "3.12.5",
        "paddlepaddle_distribution": "3.2.0",
        "paddleocr_distribution": "3.3.0",
        "paddle_module_detected": True,
        "paddleocr_module_detected": True,
    }


def _make_probe(tmp_path, monkeypatch):
    monkeypatch.setattr(ocr_runtime, "environment", _fake_env)
    image_file = tmp_path / "sample.png"
    Image.new("RGB", (100, 100), "white").save(image_file)
    result = ocr_runtime.probe(
        image_file,
        infer=True,
        include_text=True,
        runner=lambda _: [{"text": "¥199", "score": 0.98, "bbox": [2, 3, 30, 25]}],
    )
    path = tmp_path / "probe.json"
    ocr_runtime.save_report(result, path)
    return path, image_file


def test_valid_probe_records_image_hash_and_recognition_boxes(tmp_path, monkeypatch):
    report, image = _make_probe(tmp_path, monkeypatch)
    proof = verify_probe(report, image, "Linux")
    assert proof["ocr_lines"] == 1
    assert proof["valid_bboxes"] == 1
    assert proof["paddleocr"] == "3.3.0"
    assert len(proof["probe_sha256"]) == 64


def test_not_run_and_bad_platform_rejected(tmp_path, monkeypatch):
    report, image = _make_probe(tmp_path, monkeypatch)
    with pytest.raises(EvidenceInvalid, match="平台"):
        verify_probe(report, image, "Darwin")
    data = json.loads(report.read_text())
    data["status"] = "NOT_RUN"
    report.write_text(json.dumps(data))
    with pytest.raises(EvidenceInvalid, match="成功的真实推理"):
        verify_probe(report, image, "Linux")


def test_cannot_accept_missing_original_ocr_lines_or_no_boxes(tmp_path, monkeypatch):
    report, image = _make_probe(tmp_path, monkeypatch)
    data = json.loads(report.read_text())
    data.pop("ocr_lines")
    report.write_text(json.dumps(data))
    with pytest.raises(EvidenceInvalid, match="原始识别行"):
        verify_probe(report, image, "Linux")
    data["ocr_lines"] = [{"text": "¥199", "score": 0.99, "bbox": None}]
    report.write_text(json.dumps(data))
    with pytest.raises(EvidenceInvalid, match="坐标框"):
        verify_probe(report, image, "Linux")


@pytest.mark.parametrize(
    "invalid_box",
    [
        [2, 3, 101, 25],  # right edge exceeds the 100px raster
        [2, 3, 30, 101],  # bottom edge exceeds the 100px raster
    ],
)
def test_probe_rejects_boxes_outside_actual_image(tmp_path, monkeypatch, invalid_box):
    report, image = _make_probe(tmp_path, monkeypatch)
    data = json.loads(report.read_text())
    data["ocr_lines"][0]["bbox"] = invalid_box
    report.write_text(json.dumps(data))
    with pytest.raises(EvidenceInvalid, match="坐标框"):
        verify_probe(report, image, "Linux")


def test_probe_rejects_partial_spatial_evidence(tmp_path, monkeypatch):
    report, image = _make_probe(tmp_path, monkeypatch)
    data = json.loads(report.read_text())
    data["ocr_lines"].append({"text": "立即购买", "score": 0.8, "bbox": None})
    data["line_count"] = 2
    report.write_text(json.dumps(data, ensure_ascii=False))
    with pytest.raises(EvidenceInvalid, match="坐标框"):
        verify_probe(report, image, "Linux")


def test_probe_rejects_reported_pixel_dimensions_different_from_actual_png(tmp_path, monkeypatch):
    report, image = _make_probe(tmp_path, monkeypatch)
    data = json.loads(report.read_text())
    data["image"]["pixel_size"] = [200, 100]
    report.write_text(json.dumps(data))
    with pytest.raises(EvidenceInvalid, match="尺寸"):
        verify_probe(report, image, "Linux")


def test_tampered_input_and_missing_installed_versions_rejected(tmp_path, monkeypatch):
    report, image = _make_probe(tmp_path, monkeypatch)
    image.write_bytes(image.read_bytes() + b"tampered")
    with pytest.raises(EvidenceInvalid, match="SHA-256"):
        verify_probe(report, image, "Linux")
    _, image = _make_probe(tmp_path, monkeypatch)
    data = json.loads(report.read_text())
    data["environment"]["paddleocr_distribution"] = None
    report.write_text(json.dumps(data))
    with pytest.raises(EvidenceInvalid, match="版本"):
        verify_probe(report, image, "Linux")


def test_batch_manifest_observations_and_hashes_are_checked(tmp_path, monkeypatch):
    manifest = generate_fixture_set(tmp_path / "images")
    monkeypatch.setattr("evals.image_runner.metadata.version", lambda name: "3.3.0")
    monkeypatch.setattr(
        "evals.image_runner.recognize",
        lambda path: [{"text": "¥199", "score": 0.90, "bbox": [10, 10, 90, 30]}],
    )
    base = tmp_path / "bench"
    evaluate_image_manifest(manifest, base, "paddle")
    # The replay runner expects manifest under batch_dir/images, so copy the
    # programmatic fixtures into this standard layout for the test.
    import shutil

    shutil.copytree(manifest.parent, base / "images")
    outcome = verify_batch(base)
    assert outcome["fixture_count"] == 20
    assert outcome["observation_records"] == 20

    file = base / "images" / "F01.png"
    file.write_bytes(file.read_bytes() + b"changed")
    with pytest.raises(EvidenceInvalid, match="SHA-256"):
        verify_batch(base)


def test_batch_rejects_fake_not_run_summary(tmp_path, monkeypatch):
    manifest = generate_fixture_set(tmp_path / "images")
    base = tmp_path / "bench"
    evaluate_image_manifest(manifest, base, "none")
    import shutil

    shutil.copytree(manifest.parent, base / "images")
    with pytest.raises(EvidenceInvalid, match="PaddleOCR"):
        verify_batch(base)


@pytest.mark.parametrize(
    "invalid_duration",
    [True, False, float("nan"), float("inf"), -float("inf"), "5", 10**400],
)
def test_probe_rejects_non_finite_or_non_numeric_elapsed_ms(
    tmp_path, monkeypatch, invalid_duration
):
    report, image = _make_probe(tmp_path, monkeypatch)
    data = json.loads(report.read_text(encoding="utf-8"))
    data["elapsed_ms"] = invalid_duration
    report.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(EvidenceInvalid, match="JSON|耗时"):
        verify_probe(report, image, "Linux")


def test_probe_rejects_duplicate_json_keys_even_when_last_value_is_valid(tmp_path, monkeypatch):
    report, image = _make_probe(tmp_path, monkeypatch)
    raw = report.read_text(encoding="utf-8")
    assert '"status": "OK"' in raw
    report.write_text(
        raw.replace('"status": "OK"', '"status": "NOT_RUN", "status": "OK"', 1),
        encoding="utf-8",
    )
    with pytest.raises(EvidenceInvalid, match="JSON"):
        verify_probe(report, image, "Linux")


def test_probe_rejects_non_finite_values_in_unconsumed_fields(tmp_path, monkeypatch):
    report, image = _make_probe(tmp_path, monkeypatch)
    raw = report.read_text(encoding="utf-8").rstrip()
    assert raw.endswith("}")
    report.write_text(raw[:-1] + ', "untrusted_extra": NaN}', encoding="utf-8")
    with pytest.raises(EvidenceInvalid, match="JSON"):
        verify_probe(report, image, "Linux")


def _make_valid_batch(tmp_path, monkeypatch):
    import shutil

    manifest = generate_fixture_set(tmp_path / "source")
    monkeypatch.setattr("evals.image_runner.metadata.version", lambda name: "3.3.0")
    monkeypatch.setattr(
        "evals.image_runner.recognize",
        lambda path: [{"text": "¥199", "score": 0.90, "bbox": [10, 10, 90, 30]}],
    )
    base = tmp_path / "bench"
    evaluate_image_manifest(manifest, base, "paddle")
    shutil.copytree(manifest.parent, base / "images")
    return base


def test_batch_rejects_duplicate_jsonl_keys(tmp_path, monkeypatch):
    base = _make_valid_batch(tmp_path, monkeypatch)
    path = base / "ocr_observations.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    assert '"case_id": "F01"' in lines[0]
    needle = '"case_id": "F01"'
    replacement = '"case_id": "spoofed", "case_id": "F01"'
    lines[0] = lines[0].replace(needle, replacement, 1)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(EvidenceInvalid, match="JSON"):
        verify_batch(base)


def test_batch_rejects_invalid_ocr_observation_rows(tmp_path, monkeypatch):
    import shutil

    base = _make_valid_batch(tmp_path, monkeypatch)
    cases = [
        (lambda row: row.update(bbox=None), "坐标框"),
        (lambda row: row.update(score=1.01), "score"),
        (lambda row: row.update(score=10**400), "score"),
        (lambda row: row.update(score=float("nan")), "JSON"),
        (lambda row: row.update(text=""), "text"),
        (lambda row: row.update(release_ready=True), "schema"),
    ]
    for index, (mutation, message) in enumerate(cases):
        case_dir = tmp_path / f"invalid-row-{index}"
        shutil.copytree(base, case_dir)
        path = case_dir / "ocr_observations.jsonl"
        lines = path.read_text(encoding="utf-8").splitlines()
        observation = json.loads(lines[0])
        mutation(observation["ocr_lines"][0])
        lines[0] = json.dumps(observation, ensure_ascii=False, allow_nan=True)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        with pytest.raises(EvidenceInvalid, match=message):
            verify_batch(case_dir)


def test_probe_rejects_overflowing_exponent_in_unconsumed_fields(tmp_path, monkeypatch):
    report, image = _make_probe(tmp_path, monkeypatch)
    raw = report.read_text(encoding="utf-8").rstrip()
    assert raw.endswith("}")
    report.write_text(raw[:-1] + ', "untrusted_extra": 1e999}', encoding="utf-8")
    with pytest.raises(EvidenceInvalid, match="JSON"):
        verify_probe(report, image, "Linux")
