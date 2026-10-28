"""PaddleOCR 3.x spatial evidence parsing, with no model or network calls."""

from __future__ import annotations

import pytest

from scripts.capture_m6_paddle_ocr import OCREvidenceError, _ocr_lines


class OCRResult:
    """Represents the documented PaddleOCR Result.json wrapped view."""

    def __init__(self, data):
        self.json = {"res": data}


class ArrayLike:
    """A minimal stand-in for PaddleOCR's numpy.ndarray bounding-box row."""

    def __init__(self, values):
        self.values = values

    def tolist(self):
        return self.values


def test_paddleocr_json_result_preserves_bounding_boxes():
    output = [
        OCRResult(
            {
                "rec_texts": ["¥129", "促销"],
                "rec_scores": [0.98, 0.83],
                "rec_boxes": [ArrayLike([65, 668, 224, 732]), ArrayLike([50, 100, 300, 164])],
            }
        )
    ]
    assert _ocr_lines(output, size=(1024, 1024)) == [
        {"text": "¥129", "confidence": 0.98, "bbox": [65, 668, 224, 732]},
        {"text": "促销", "confidence": 0.83, "bbox": [50, 100, 300, 164]},
    ]


def test_flat_result_without_boxes_marks_spatial_information_missing():
    assert _ocr_lines(
        [{"rec_texts": ["¥129"], "rec_scores": [0.99]}],
        size=(1080, 1350),
    ) == [{"text": "¥129", "confidence": 0.99, "bbox": None}]


@pytest.mark.parametrize(
    "boxes",
    [
        [[-1, 10, 100, 100]],
        [[0, 10, 1081, 100]],
        [[0, 10, 100, 1351]],
        [[100, 10, 90, 100]],
        [[10, 120, 100, 99]],
        [[0, 0, float("nan"), 100]],
        [[0, 0, True, 100]],
        [[10, 10, 20]],
    ],
)
def test_invalid_geometry_fails_closed(boxes):
    with pytest.raises(OCREvidenceError, match="检测框|坐标"):
        _ocr_lines(
            [{"rec_texts": ["¥129"], "rec_scores": [0.98], "rec_boxes": boxes}],
            size=(1080, 1350),
        )


def test_mismatched_box_and_text_counts_rejected():
    with pytest.raises(OCREvidenceError, match="数量"):
        _ocr_lines(
            [
                {
                    "rec_texts": ["one", "two"],
                    "rec_scores": [0.98, 0.95],
                    "rec_boxes": [[1, 1, 10, 10]],
                }
            ],
            size=(1024, 1024),
        )


def test_unparseable_ocr_result_is_validation_error():
    with pytest.raises(OCREvidenceError, match="结构"):
        _ocr_lines([object()], size=(1024, 1024))
