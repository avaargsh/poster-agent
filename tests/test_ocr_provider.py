"""Verify PaddleOCR 3.x parsing without loading its large model weights."""

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
    class FakeOCR:
        def predict(self, path):
            assert Path(path).exists()
            return [{"res": {"rec_texts": ["标题"], "rec_scores": [0.99], "rec_boxes": [[1, 2, 20, 30]]}}]

    monkeypatch.setattr(ocr_provider, "_engine", lambda: FakeOCR())
    image = tmp_path / "approved.png"
    image.write_bytes(b"placeholder")
    result = ocr_provider.recognize(image)

    started = result.runtime["inference_started_at"]
    finished = result.runtime["inference_finished_at"]
    elapsed = result.runtime["inference_elapsed_ms"]
    from datetime import datetime

    start_time = datetime.fromisoformat(started)
    finish_time = datetime.fromisoformat(finished)
    assert start_time.tzinfo is not None
    assert finish_time.tzinfo is not None
    assert finish_time >= start_time
    assert isinstance(elapsed, (int, float))
    assert not isinstance(elapsed, bool)
    assert 0 <= elapsed <= 24 * 60 * 60 * 1000
