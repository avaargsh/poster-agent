"""Verify PaddleOCR 3.x parsing without loading its large model weights."""

from datetime import datetime
from pathlib import Path

from app import ocr_provider


def test_paddle_3x_result_conversion(monkeypatch, tmp_path):
    class FakeOCR:
        def predict(self, path):
            assert Path(path).exists()
            return [
                {
                    "res": {
                        "rec_texts": ["¥199", "立即抢购"],
                        "rec_scores": [0.99, 0.86],
                        "rec_boxes": [[60, 660, 280, 770], [74, 866, 410, 935]],
                    }
                }
            ]

    monkeypatch.setattr(ocr_provider, "_engine", lambda: FakeOCR())
    path = tmp_path / "placeholder.png"
    path.write_bytes(b"unused fake bytes")
    out = ocr_provider.recognize(path)
    assert out[0] == {"text": "¥199", "score": 0.99, "bbox": [60, 660, 280, 770]}
    assert out[1]["text"] == "立即抢购"


def test_real_adapter_records_wall_clock_and_monotonic_predict_timing(monkeypatch, tmp_path):
    clock = {"ns": 0}

    def fake_clock():
        return clock["ns"]

    class FakeOCR:
        def predict(self, path):
            assert Path(path).exists()

            def deferred_result():
                # PaddleOCR can return a lazy iterator; inference happens while
                # the adapter consumes it, not merely when predict() returns.
                clock["ns"] = 5_000_000
                yield {
                    "res": {
                        "rec_texts": ["标题"],
                        "rec_scores": [0.99],
                        "rec_boxes": [[1, 2, 20, 30]],
                    }
                }

            return deferred_result()

    monkeypatch.setattr(ocr_provider, "_engine", lambda: FakeOCR())
    monkeypatch.setattr(ocr_provider, "perf_counter_ns", fake_clock)
    image = tmp_path / "approved.png"
    image.write_bytes(b"placeholder")
    result = ocr_provider.recognize(image)

    started = result.runtime["inference_started_at"]
    finished = result.runtime["inference_finished_at"]
    elapsed = result.runtime["inference_elapsed_ms"]
    start_time = datetime.fromisoformat(started)
    finish_time = datetime.fromisoformat(finished)
    assert start_time.tzinfo is not None
    assert finish_time.tzinfo is not None
    assert finish_time >= start_time
    assert elapsed == 5.0
    assert result.runtime["attested"] is False


def test_real_adapter_reports_gpu_paddle_distribution_version(monkeypatch, tmp_path):
    class FakeOCR:
        def predict(self, path):
            assert Path(path).exists()
            return []

    versions = {
        "paddleocr": "3.3.0",
        "paddlepaddle": None,
        "paddlepaddle-gpu": "3.2.0",
    }
    monkeypatch.setattr(ocr_provider, "_engine", lambda: FakeOCR())
    monkeypatch.setattr(ocr_provider, "_package_version", lambda name: versions.get(name))
    image = tmp_path / "approved.png"
    image.write_bytes(b"placeholder")

    result = ocr_provider.recognize(image)

    assert result.runtime["paddlepaddle_package_version"] == "3.2.0"
