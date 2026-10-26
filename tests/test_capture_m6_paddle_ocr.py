"""Synthetic regression coverage only: never invokes a real PaddleOCR model."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from scripts.capture_m6_paddle_ocr import OCREvidenceError, REVIEW_FIELDS, capture


@pytest.fixture(autouse=True)
def stub_numpy(monkeypatch):
    # Existing baseline CI does not install optional PaddleOCR or NumPy.
    monkeypatch.setitem(sys.modules, "numpy", SimpleNamespace(
        asarray=lambda image: SimpleNamespace(shape=(image.height, image.width, 3))
    ))


VARIANTS = {
    "square": (1024, 1024, "square"),
    "feed_4x5_native": (1080, 1350, "native-4x5"),
}


class PredictStub:
    def __init__(self, **options):
        assert options["device"] == "cpu"
        for setting in ("use_doc_orientation_classify", "use_doc_unwarping",
                        "use_textline_orientation"):
            assert options[setting] is False
        assert Path(options["text_detection_model_dir"]).is_dir()
        assert Path(options["text_recognition_model_dir"]).is_dir()

    def predict(self, image):
        assert image.shape in ((1024, 1024, 3), (1350, 1080, 3))
        return [{"rec_texts": ["标题", "¥129"], "rec_scores": [0.93, 0.88]}]


def package_fixture(tmp_path):
    package = tmp_path / "m6-package"
    (package / "images").mkdir(parents=True)
    rows = []
    for sku in ("sku-one", "sku-two"):
        for variant, (width, height, suffix) in VARIANTS.items():
            output = io.BytesIO()
            Image.new("RGB", (width, height), "white").save(output, "PNG")
            payload = output.getvalue()
            name = f"images/{sku}-{suffix}.png"
            (package / name).write_bytes(payload)
            rows.append({
                **dict.fromkeys(REVIEW_FIELDS, "UNVERIFIED"),
                "cohort_id": "m6-demo",
                "sku_id": sku,
                "poster_id": "e" * 32,
                "source_version": "1",
                "variant_id": variant,
                "output_image_path": name,
                "output_image_sha256": hashlib.sha256(payload).hexdigest(),
                "source_product_sha256": "a" * 64,
                "request_sha256": "b" * 64,
                "spec_sha256": "c" * 64,
                "source_square_image_sha256": "d" * 64,
            })
    save_rows(package, rows)
    det = tmp_path / "detection"
    rec = tmp_path / "recognition"
    det.mkdir()
    rec.mkdir()
    (det / "inference.json").write_text('{"model":"det"}', encoding="utf-8")
    (rec / "inference.json").write_text('{"model":"rec"}', encoding="utf-8")
    return package, det, rec, rows


def save_rows(package, rows):
    with (package / "m6_output_reviews.csv").open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=REVIEW_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def test_capture_records_immutable_inputs_but_never_claims_real_ocr_pass(tmp_path):
    package, det, rec, _ = package_fixture(tmp_path)
    report = capture(package, det, rec, engine_factory=PredictStub)
    path = package / "m6_paddle_ocr_evidence.json"
    assert json.loads(path.read_text(encoding="utf-8")) == report
    assert report["captured_variant_count"] == 4
    assert report["sku_count"] == 2
    assert report["real_ocr_inference_attempted"] is False
    assert report["human_review_performed"] is False
    assert report["automatically_accepted"] is False
    assert report["min_20_real_skus_proven"] is False
    assert report["source_authorizations_verified"] is False
    assert all(row["status"] == "CAPTURED_REVIEW_REQUIRED"
               for row in report["observations"])
    assert all(row["recognized_line_count"] == 2 for row in report["observations"])
    assert len(report["model_digests"]["detection"]["sha256"]) == 64
    assert report["paddleocr_version"] == "test-double-not-real-ocr"
    assert report["elapsed_seconds"] >= 0


def test_detect_changed_png_and_no_partial_file(tmp_path):
    package, det, rec, rows = package_fixture(tmp_path)
    (package / rows[0]["output_image_path"]).write_bytes(b"changed")
    with pytest.raises(OCREvidenceError, match="PNG 已被修改"):
        capture(package, det, rec, engine_factory=PredictStub)
    assert not (package / "m6_paddle_ocr_evidence.json").exists()


def test_reject_symlinked_png_even_if_hash_matches(tmp_path):
    package, det, rec, rows = package_fixture(tmp_path)
    source = package / rows[0]["output_image_path"]
    outside = tmp_path / "outside.png"
    outside.write_bytes(source.read_bytes())
    source.unlink()
    source.symlink_to(outside)
    with pytest.raises(OCREvidenceError, match="安全读取"):
        capture(package, det, rec, engine_factory=PredictStub)


def test_reject_traversal_and_missing_variant(tmp_path):
    package, det, rec, rows = package_fixture(tmp_path)
    rows[0]["output_image_path"] = "../outside.png"
    save_rows(package, rows)
    with pytest.raises(OCREvidenceError, match="路径"):
        capture(package, det, rec, engine_factory=PredictStub)
    rows[0]["output_image_path"] = "images/sku-one-square.png"
    save_rows(package, rows[:-1])
    with pytest.raises(OCREvidenceError, match="缺少完整"):
        capture(package, det, rec, engine_factory=PredictStub)


def test_reject_unavailable_or_symlinked_model_data(tmp_path):
    package, det, rec, _ = package_fixture(tmp_path)
    (det / "inference.json").unlink()
    with pytest.raises(OCREvidenceError, match="模型文件数量"):
        capture(package, det, rec, engine_factory=PredictStub)
    (det / "inference.json").symlink_to(rec / "inference.json")
    with pytest.raises(OCREvidenceError, match="符号链接"):
        capture(package, det, rec, engine_factory=PredictStub)


def test_prediction_crash_or_bad_structure_never_creates_evidence(tmp_path):
    package, det, rec, _ = package_fixture(tmp_path)

    class Crash(PredictStub):
        def predict(self, image):
            raise RuntimeError("ocr failed")

    with pytest.raises(RuntimeError, match="ocr failed"):
        capture(package, det, rec, engine_factory=Crash)
    assert not (package / "m6_paddle_ocr_evidence.json").exists()

    class Wrong(PredictStub):
        def predict(self, image):
            return [{"wrong_schema": []}]

    with pytest.raises(OCREvidenceError, match="结果结构"):
        capture(package, det, rec, engine_factory=Wrong)
    assert not (package / "m6_paddle_ocr_evidence.json").exists()


def test_invalid_ocr_confidence_is_not_accepted(tmp_path):
    package, det, rec, _ = package_fixture(tmp_path)

    class BrokenScore(PredictStub):
        def predict(self, image):
            return [{"rec_texts": ["hello"], "rec_scores": [float("nan")]}]

    with pytest.raises(OCREvidenceError, match="置信度"):
        capture(package, det, rec, engine_factory=BrokenScore)


def test_wrong_dimensions_rejected_even_with_updated_hash(tmp_path):
    package, det, rec, rows = package_fixture(tmp_path)
    image = io.BytesIO()
    Image.new("RGB", (10, 10)).save(image, "PNG")
    bad_png = image.getvalue()
    (package / rows[0]["output_image_path"]).write_bytes(bad_png)
    rows[0]["output_image_sha256"] = hashlib.sha256(bad_png).hexdigest()
    save_rows(package, rows)
    with pytest.raises(OCREvidenceError, match="尺寸"):
        capture(package, det, rec, engine_factory=PredictStub)


def test_duplicate_or_mismatched_source_versions_rejected(tmp_path):
    package, det, rec, rows = package_fixture(tmp_path)
    rows[1]["request_sha256"] = "f" * 64
    save_rows(package, rows)
    with pytest.raises(OCREvidenceError, match="来源凭据不一致"):
        capture(package, det, rec, engine_factory=PredictStub)
    rows[1]["request_sha256"] = "b" * 64
    save_rows(package, rows + [rows[0]])
    with pytest.raises(OCREvidenceError, match="重复"):
        capture(package, det, rec, engine_factory=PredictStub)


def test_invalid_header_or_existing_evidence_rejected(tmp_path):
    package, det, rec, _ = package_fixture(tmp_path)
    csv_path = package / "m6_output_reviews.csv"
    csv_path.write_text(csv_path.read_text().replace("reviewed_at_utc", "unexpected"))
    with pytest.raises(OCREvidenceError, match="官方"):
        capture(package, det, rec, engine_factory=PredictStub)
    csv_path.write_text(csv_path.read_text().replace("unexpected", "reviewed_at_utc"))
    report_path = package / "m6_paddle_ocr_evidence.json"
    report_path.write_bytes(b"prior reviewer data")
    with pytest.raises(OCREvidenceError, match="已存在"):
        capture(package, det, rec, engine_factory=PredictStub)
    assert report_path.read_bytes() == b"prior reviewer data"


def test_serialization_failure_never_publishes_partial_evidence(tmp_path, monkeypatch):
    package, det, rec, _ = package_fixture(tmp_path)
    import scripts.capture_m6_paddle_ocr as runner

    def refuse_serialize(*args, **kwargs):
        raise RuntimeError("serialize failed")

    monkeypatch.setattr(runner.json, "dumps", refuse_serialize)
    with pytest.raises(RuntimeError, match="serialize failed"):
        capture(package, det, rec, engine_factory=PredictStub)
    assert not (package / "m6_paddle_ocr_evidence.json").exists()
    assert not list(package.glob(".m6-ocr-*.tmp"))
