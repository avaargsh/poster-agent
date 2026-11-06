"""Pinned local PaddleOCR model inputs must fail closed before model loading."""

import hashlib
import json
import sys
from types import SimpleNamespace

import pytest

from app import ocr_provider
from app.designs.ocr_model_policy import (
    OCRModelProfileError,
    load_model_profile,
    model_directory_sha256,
)


def _model_directories(tmp_path):
    detector = tmp_path / "detector"
    recognizer = tmp_path / "recognizer"
    for folder in (detector, recognizer):
        folder.mkdir()
        (folder / "inference.json").write_text('{"arch":"local"}', encoding="utf-8")
        (folder / "weights.bin").write_bytes(b"fake-pinned-model-weights")
    return detector, recognizer


def _config(detector, recognizer):
    return json.dumps(
        {
            "schema_version": 1,
            "text_detection_model_dir": str(detector),
            "text_recognition_model_dir": str(recognizer),
            "text_detection_sha256": model_directory_sha256(detector),
            "text_recognition_sha256": model_directory_sha256(recognizer),
        }
    )


def test_profile_pins_two_explicit_local_model_directories(tmp_path):
    detector, recognizer = _model_directories(tmp_path)
    raw = _config(detector, recognizer)
    profile = load_model_profile(raw)
    assert profile.detector_dir == str(detector)
    assert profile.recognizer_dir == str(recognizer)
    assert len(profile.fingerprint) == 64
    assert (
        profile.fingerprint
        == hashlib.sha256(
            json.dumps(
                json.loads(raw), sort_keys=True, separators=(",", ":"), ensure_ascii=False
            ).encode("utf-8")
        ).hexdigest()
    )
    assert load_model_profile(raw) == profile


def test_profile_rejects_missing_unknown_or_modified_local_files(tmp_path):
    detector, recognizer = _model_directories(tmp_path)
    assert load_model_profile(_config(detector, recognizer))
    with pytest.raises(OCRModelProfileError):
        load_model_profile(None)
    with pytest.raises(OCRModelProfileError):
        load_model_profile("{}")
    config = json.loads(_config(detector, recognizer))
    with pytest.raises(OCRModelProfileError):
        load_model_profile(json.dumps({**config, "allow_network": True}))
    (detector / "weights.bin").write_bytes(b"tampered")
    with pytest.raises(OCRModelProfileError):
        load_model_profile(json.dumps(config))
    (detector / "weights.bin").unlink()
    with pytest.raises(OCRModelProfileError):
        load_model_profile(json.dumps(config))


def test_profile_rejects_symlink_or_empty_model_directory(tmp_path):
    detector, recognizer = _model_directories(tmp_path)
    (detector / "weights.bin").unlink()
    (detector / "weights.bin").symlink_to(recognizer / "weights.bin")
    with pytest.raises(OCRModelProfileError):
        model_directory_sha256(detector)
    (detector / "weights.bin").unlink()
    (detector / "inference.json").unlink()
    with pytest.raises(OCRModelProfileError):
        model_directory_sha256(detector)


def test_adapter_passes_only_explicit_directories_for_pinned_design_ocr(monkeypatch):
    created = []

    class FakeOCR:
        def __init__(self, **kwargs):
            created.append(kwargs)

    monkeypatch.setitem(sys.modules, "paddleocr", SimpleNamespace(PaddleOCR=FakeOCR))
    ocr_provider._local_engine.cache_clear()
    try:
        first = ocr_provider._local_engine("/a/det", "/a/rec", "a" * 64)
        assert first is ocr_provider._local_engine("/a/det", "/a/rec", "a" * 64)
        assert len(created) == 1
        assert created[0]["text_detection_model_dir"] == "/a/det"
        assert created[0]["text_recognition_model_dir"] == "/a/rec"
        assert created[0]["use_doc_orientation_classify"] is False
        assert created[0]["use_doc_unwarping"] is False
        assert created[0]["use_textline_orientation"] is False
    finally:
        ocr_provider._local_engine.cache_clear()
