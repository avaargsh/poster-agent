"""Journaled OCR bounding-box diagnostics never imply spatial approval."""

import copy

import pytest

from app.designs.ocr_spatial_evidence import assess_spatial_evidence


@pytest.fixture
def observations():
    return [
        {"text": "双十一牙刷", "score": 0.99, "bbox": [10, 10, 210, 55]},
        {"text": "¥199.90", "score": 0.98, "bbox": [10, 65, 210, 115]},
        {"text": "立即抢购", "score": 0.97, "bbox": [10, 125, 210, 170]},
    ]


@pytest.fixture
def source_binding():
    return {
        "document_id": "doc-ocr-1",
        "revision": 1,
        "approval_id": "approved-1",
        "svg_sha256": "a" * 64,
        "png_sha256": "b" * 64,
        "model_profile_sha256": "c" * 64,
        "operation_id": "ocr_" + "d" * 32,
    }


@pytest.fixture
def field_checks():
    return [
        {"field": "title", "status": "MATCHED", "matched_line_index": 0},
        {"field": "price", "status": "MATCHED", "matched_line_index": 1},
        {"field": "cta", "status": "MATCHED", "matched_line_index": 2},
    ]


def test_matching_text_and_rectangles_remain_uncalibrated(observations, field_checks, source_binding):
    result = assess_spatial_evidence(observations, field_checks, width=1024, height=1024, source_binding=source_binding)
    assert result["spatial_evidence_status"] == "BOUNDS_ONLY_UNCALIBRATED"
    assert all(
        row["status"] == "BOX_PRESENT_UNCALIBRATED"
        for row in result["spatial_field_checks"]
    )
    assert result["spatial_field_checks"][1]["bbox"] == [10, 65, 210, 115]
    assert len(result["journaled_observations_sha256"]) == 64
    assert len(result["spatial_comparison_fingerprint_sha256"]) == 64


@pytest.mark.parametrize(
    ("box", "status"),
    [
        (None, "MISSING_BBOX"),
        ([10, 65, 10, 115], "INVALID_BBOX"),
        ([10, 65, 1030, 115], "INVALID_BBOX"),
        ([True, 65, 210, 115], "INVALID_BBOX"),
        ([-1, 65, 210, 115], "INVALID_BBOX"),
        ([10, 65, 210], "INVALID_BBOX"),
    ],
)
def test_missing_or_invalid_price_box_blocks_diagnostic(
    observations, field_checks, box, status, source_binding
):
    observations[1]["bbox"] = box
    result = assess_spatial_evidence(observations, field_checks, width=1024, height=1024, source_binding=source_binding)
    assert result["spatial_evidence_status"] == "BLOCKED"
    assert result["spatial_field_checks"][1]["status"] == status
    assert result["spatial_field_checks"][1]["bbox"] is None


def test_missing_text_match_blocks_geometry(observations, field_checks, source_binding):
    field_checks[1]["status"] = "MISMATCH"
    field_checks[1]["matched_line_index"] = None
    result = assess_spatial_evidence(observations, field_checks, width=1024, height=1024, source_binding=source_binding)
    assert result["spatial_evidence_status"] == "BLOCKED"
    assert result["spatial_field_checks"][1]["status"] == "TEXT_NOT_MATCHED"


def test_bbox_change_rebinds_both_fingerprints(observations, field_checks, source_binding):
    before = assess_spatial_evidence(observations, field_checks, width=1024, height=1024, source_binding=source_binding)
    changed = copy.deepcopy(observations)
    changed[1]["bbox"] = [11, 65, 210, 115]
    after = assess_spatial_evidence(changed, field_checks, width=1024, height=1024, source_binding=source_binding)
    assert before["journaled_observations_sha256"] != after["journaled_observations_sha256"]
    assert (
        before["spatial_comparison_fingerprint_sha256"]
        != after["spatial_comparison_fingerprint_sha256"]
    )


def test_canvas_dimensions_are_part_of_spatial_fingerprint(observations, field_checks, source_binding):
    before = assess_spatial_evidence(observations, field_checks, width=1024, height=1024, source_binding=source_binding)
    after = assess_spatial_evidence(observations, field_checks, width=900, height=1024, source_binding=source_binding)
    assert before["journaled_observations_sha256"] == after["journaled_observations_sha256"]
    assert (
        before["spatial_comparison_fingerprint_sha256"]
        != after["spatial_comparison_fingerprint_sha256"]
    )


@pytest.mark.parametrize(
    ("identity_key", "modified_value"),
    [
        ("approval_id", "approved-2"),
        ("png_sha256", "e" * 64),
        ("model_profile_sha256", "f" * 64),
        ("operation_id", "ocr_" + "f" * 32),
    ],
)
def test_same_ocr_geometry_on_different_approved_sources_is_not_same_evidence(
    observations, field_checks, source_binding, identity_key, modified_value
):
    before = assess_spatial_evidence(
        observations, field_checks, width=1024, height=1024,
        source_binding=source_binding,
    )
    changed = {**source_binding, identity_key: modified_value}
    after = assess_spatial_evidence(
        observations, field_checks, width=1024, height=1024,
        source_binding=changed,
    )
    assert before["journaled_observations_sha256"] == after["journaled_observations_sha256"]
    assert before["spatial_source_sha256"] != after["spatial_source_sha256"]
    assert (
        before["spatial_comparison_fingerprint_sha256"]
        != after["spatial_comparison_fingerprint_sha256"]
    )
