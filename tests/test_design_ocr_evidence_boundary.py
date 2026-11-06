"""M6 content-consistency contract: never upgrade caller-supplied OCR to evidence."""

from copy import deepcopy

import pytest

from app.designs.ocr_observations import (
    OCRObservationRequest,
    assess_unattested,
    replay_consistent_unattested,
)


def _target():
    return {
        "document_id": "doc_demo",
        "revision": 1,
        "approval_id": "approval_demo",
        "spec_digest": "a" * 64,
        "asset_manifest_digest": "b" * 64,
        "renderer_version": "design-svg-v1",
        "svg_sha256": "c" * 64,
        "expected_fields": {
            "title": "双十一牙刷",
            "price": "¥199.90",
            "cta": "立即抢购",
        },
        "missing_fields": [],
        "ocr_status": "NOT_RUN",
        "runtime_attested": False,
        "quality_verified": False,
        "release_ready": False,
    }


def _observations():
    return OCRObservationRequest(
        lines=[
            {"text": "双十一牙刷", "score": 0.99},
            {"text": "原价 ¥1990.00", "score": 0.98},
            {"text": "活动价 ¥199.90元", "score": 0.97},
            {"text": "立即抢购", "score": 0.96},
        ]
    )


def test_v1_golden_fingerprint_vector_is_fixed_and_replay_consistent():
    target = _target()
    observations = _observations()
    result = assess_unattested(target, observations)

    # Literal independent vectors catch accidental changes to JSON serialization.
    assert (
        result["observations_sha256"]
        == "1ddefe10f542fe2ffcd2684c74b7f6c09a96e00538b104d740e1d9746b07466c"
    )
    assert (
        result["comparison_fingerprint_sha256"]
        == "4f2f879ecedd139afc819a108c07022960c5f83d066caf8771dd177815524e7a"
    )
    assert replay_consistent_unattested(target, observations, result)
    assert replay_consistent_unattested(target, observations, dict(reversed(list(result.items()))))


@pytest.mark.parametrize(
    ("field", "changed_value"),
    [
        ("document_id", "another_doc"),
        ("revision", 2),
        ("approval_id", "another_approval"),
        ("spec_digest", "0" * 64),
        ("asset_manifest_digest", "1" * 64),
        ("renderer_version", "design-svg-v2"),
        ("svg_sha256", "2" * 64),
        ("missing_fields", ["cta"]),
        ("expected_fields", {"title": "新标题", "price": "¥199.90", "cta": "立即抢购"}),
    ],
)
def test_v1_comparison_fingerprint_is_sensitive_to_authoritative_target(field, changed_value):
    target = _target()
    observations = _observations()
    baseline = assess_unattested(target, observations)
    changed_target = deepcopy(target)
    changed_target[field] = changed_value

    updated = assess_unattested(changed_target, observations)

    assert updated["observations_sha256"] == baseline["observations_sha256"]
    assert updated["comparison_fingerprint_sha256"] != baseline["comparison_fingerprint_sha256"]
    assert not replay_consistent_unattested(changed_target, observations, baseline)


@pytest.mark.parametrize(
    ("field", "changed_value"),
    [
        ("ocr_status", "EXECUTED"),
        ("runtime_attested", True),
        ("quality_verified", True),
        ("release_ready", True),
        ("declared_consistency", "BLOCKED"),
        ("comparison_schema_version", 2),
        ("comparison_fingerprint_sha256", "0" * 64),
        ("observations_sha256", "0" * 64),
        ("revision", True),  # Python's True == 1 must not bypass JSON type checks.
    ],
)
def test_replay_consistency_refuses_modified_payloads(field, changed_value):
    target = _target()
    observations = _observations()
    actual = assess_unattested(target, observations)
    forged = deepcopy(actual)
    forged[field] = changed_value

    assert not replay_consistent_unattested(target, observations, forged)


def test_replay_consistency_refuses_modified_line_provenance_or_extra_claims():
    target = _target()
    observations = _observations()
    actual = assess_unattested(target, observations)
    forged = deepcopy(actual)
    forged["field_checks"][0]["matched_line_index"] = 2
    assert not replay_consistent_unattested(target, observations, forged)

    forged = deepcopy(actual)
    forged["signed_by"] = "pretend-ocr-runtime"
    assert not replay_consistent_unattested(target, observations, forged)

    changed = observations.model_copy(deep=True)
    changed.lines[0].score = 0.90
    assert not replay_consistent_unattested(target, changed, actual)
    assert not replay_consistent_unattested(target, observations, {"release_ready": False})


def test_global_eligible_lines_do_not_prove_field_level_visual_mismatch():
    target = _target()
    lines = OCRObservationRequest(
        lines=[
            {"text": "双十一牙刷", "score": 0.3999},
            {"text": "¥199.90", "score": 0.40},
            {"text": "立即抢购", "score": 0.99},
        ]
    )

    report = assess_unattested(target, lines)
    checks = {item["field"]: item for item in report["field_checks"]}
    # A mismatched field means 'not found among globally eligible lines',
    # not that an independently executed OCR proved the pixels are wrong.
    assert checks["title"] == {
        "field": "title",
        "status": "MISMATCH",
        "matched_line_index": None,
    }
    assert checks["price"]["matched_line_index"] == 1
    assert checks["cta"]["matched_line_index"] == 2
    assert report["declared_consistency"] == "BLOCKED"
    assert report["ocr_status"] == "OBSERVATIONS_UNATTESTED"
    assert report["release_ready"] is False
    assert replay_consistent_unattested(target, lines, report)


def test_original_index_skips_low_scores_and_selects_first_eligible_duplicate():
    target = _target()
    observations = OCRObservationRequest(
        lines=[
            {"text": "双十一牙刷", "score": 0.39},
            {"text": "双十一牙刷", "score": 0.40},
            {"text": "双十一牙刷", "score": 0.95},
            {"text": "¥199.90", "score": 0.93},
            {"text": "立即抢购", "score": 0.98},
        ]
    )
    report = assess_unattested(target, observations)
    assert report["field_checks"][0]["matched_line_index"] == 1
    assert report["field_checks"][1]["matched_line_index"] == 3
    assert report["field_checks"][2]["matched_line_index"] == 4
    assert report["runtime_attested"] is False
    assert report["quality_verified"] is False
    assert report["release_ready"] is False
