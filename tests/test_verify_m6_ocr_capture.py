"""M6 OCR capture verifier: synthetic JSON only, not a model attestation."""
from __future__ import annotations

import copy
import json

import pytest

from scripts.verify_m6_ocr_capture import OCRCaptureEvidenceError, verify_ocr_capture

SHA = "a" * 64


def _fixture(tmp_path):
    rows = []
    observations = []
    for sku in ("sku-one", "sku-two"):
        for variant in ("square", "feed_4x5_native"):
            rows.append({
                "cohort_id": "m6-fixture",
                "sku_id": sku,
                "variant_id": variant,
                "poster_id": "b" * 32,
                "output_image_sha256": SHA,
            })
            observations.append({
                "sku_id": sku,
                "variant_id": variant,
                "poster_id": "b" * 32,
                "image_sha256": SHA,
                "recognized_lines": [{"text": "¥99", "confidence": 0.98}],
                "recognized_line_count": 1,
                "status": "CAPTURED_REVIEW_REQUIRED",
            })
    data = {
        "schema_version": "m6-paddle-ocr-evidence-1",
        "cohort_id": "m6-fixture",
        "reviews_csv_sha256": "f" * 64,
        "sku_count": 2,
        "expected_variant_count": 4,
        "captured_variant_count": 4,
        "real_ocr_inference_attempted": True,
        "paddleocr_version": "3.3.0",
        "paddlepaddle_version": "3.2.0",
        "model_digests": {
            "detection": {"sha256": "c" * 64, "file_count": 1, "total_bytes": 12},
            "recognition": {"sha256": "d" * 64, "file_count": 2, "total_bytes": 15},
        },
        "captured_at_utc": "2026-10-26T05:00:00+00:00",
        "elapsed_seconds": 1.25,
        "observations": observations,
        "automatically_accepted": False,
        "human_review_performed": False,
        "source_authorizations_verified": False,
        "min_20_real_skus_proven": False,
    }
    path = tmp_path / "m6_paddle_ocr_evidence.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path, data, rows


def _verify(path, rows):
    return verify_ocr_capture(
        path, rows, cohort_id="m6-fixture", reviews_sha256="f" * 64,
    )


def test_capture_consistency_is_not_runtime_attestation(tmp_path):
    path, _, rows = _fixture(tmp_path)
    result = _verify(path, rows)
    assert result["status"] == "MATCHED_DECLARED_REAL"
    assert result["observation_count"] == 4
    assert result["runtime_attested"] is False
    assert result["quality_verified"] is False
    assert result["authorization_verified"] is False
    assert len(result["evidence_sha256"]) == 64


@pytest.mark.parametrize(("field", "value"), [
    ("real_ocr_inference_attempted", False),
    ("paddleocr_version", "test-double-not-real-ocr"),
    ("automatically_accepted", True),
    ("reviews_csv_sha256", "0" * 64),
    ("cohort_id", "wrong-cohort"),
    ("captured_variant_count", 3),
])
def test_block_unverified_or_stale_capture_metadata(tmp_path, field, value):
    path, data, rows = _fixture(tmp_path)
    data[field] = value
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(OCRCaptureEvidenceError):
        _verify(path, rows)


@pytest.mark.parametrize(("mutator", "reason"), [
    (lambda data: data["observations"].pop(), "缺少"),
    (lambda data: data["observations"].append(copy.deepcopy(data["observations"][0])), "重复"),
    (lambda data: data["observations"][0].update(image_sha256="0" * 64), "不一致"),
    (lambda data: data["observations"][0].update(poster_id="0" * 32), "不一致"),
    (lambda data: data["observations"][0].update(status="PASS"), "状态"),
    (lambda data: data["observations"][0].update(recognized_line_count=2), "数量"),
    (lambda data: data["observations"][0]["recognized_lines"][0].update(confidence=float("nan")), "置信度"),
])
def test_block_missing_duplicate_or_corrupt_observations(tmp_path, mutator, reason):
    path, data, rows = _fixture(tmp_path)
    mutator(data)
    path.write_text(json.dumps(data, allow_nan=True), encoding="utf-8")
    with pytest.raises(OCRCaptureEvidenceError, match=reason):
        _verify(path, rows)


def test_reject_symlink_and_oversized_capture_without_following(tmp_path):
    path, _, rows = _fixture(tmp_path)
    outside = tmp_path / "outside.json"
    outside.write_bytes(path.read_bytes())
    path.unlink()
    path.symlink_to(outside)
    with pytest.raises(OCRCaptureEvidenceError, match="符号链接"):
        _verify(path, rows)
    path.unlink()
    path.write_bytes(b"x" * (8 * 1024 * 1024 + 1))
    with pytest.raises(OCRCaptureEvidenceError, match="8 MiB"):
        _verify(path, rows)


def test_reject_duplicate_expected_review_rows(tmp_path):
    path, _, rows = _fixture(tmp_path)
    with pytest.raises(OCRCaptureEvidenceError, match="重复"):
        _verify(path, rows + [rows[0]])


def test_reject_invalid_model_fingerprint(tmp_path):
    path, data, rows = _fixture(tmp_path)
    data["model_digests"]["detection"]["sha256"] = "synthetic"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(OCRCaptureEvidenceError, match="模型"):
        _verify(path, rows)


def test_reject_unhashable_or_nonobject_ocr_rows_as_validation_error(tmp_path):
    path, data, rows = _fixture(tmp_path)
    data["observations"][0]["sku_id"] = ["sku-one"]
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(OCRCaptureEvidenceError, match="SKU/版式"):
        _verify(path, rows)
    data["observations"][0] = ["not-an-object"]
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(OCRCaptureEvidenceError, match="对象"):
        _verify(path, rows)
