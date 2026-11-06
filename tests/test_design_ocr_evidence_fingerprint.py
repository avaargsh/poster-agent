"""OCR preview fingerprints bind declared observations to a revision without attesting execution."""

import hashlib
import json

from app.designs.ocr_observations import OCRObservationRequest, assess_unattested


def _approved_target():
    return {
        "document_id": "doc_demo",
        "revision": 1,
        "approval_id": "approval_demo",
        "spec_digest": "a" * 64,
        "asset_manifest_digest": "b" * 64,
        "renderer_version": "design-svg-v1",
        "svg_sha256": "c" * 64,
        "expected_fields": {"title": "双十一牙刷", "price": "¥199.90", "cta": "立即抢购"},
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


def _canonical_digest(payload):
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def test_fingerprint_covers_exact_declared_inputs_and_match_provenance():
    target = _approved_target()
    observations = _observations()
    result = assess_unattested(target, observations)

    assert result["field_checks"] == [
        {"field": "title", "status": "MATCHED", "matched_line_index": 0},
        {"field": "price", "status": "MATCHED", "matched_line_index": 2},
        {"field": "cta", "status": "MATCHED", "matched_line_index": 3},
    ]
    assert result["comparison_schema_version"] == 1
    assert result["observations_sha256"] == _canonical_digest(observations.model_dump(mode="json"))
    assert result["comparison_fingerprint_sha256"] == _canonical_digest(
        {
            "schema_version": 1,
            "target": target,
            "observations": observations.model_dump(mode="json"),
            "field_checks": result["field_checks"],
            "declared_consistency": "MATCHES_EXPECTED",
        }
    )
    assert result["ocr_status"] == "OBSERVATIONS_UNATTESTED"
    assert result["runtime_attested"] is False
    assert result["quality_verified"] is False
    assert result["release_ready"] is False


def test_fingerprints_are_deterministic_and_change_with_inputs():
    target = _approved_target()
    observations = _observations()
    baseline = assess_unattested(target, observations)
    assert assess_unattested(dict(reversed(list(target.items()))), observations) == baseline

    altered_score = observations.model_copy(deep=True)
    altered_score.lines[0].score = 0.92
    compared = assess_unattested(target, altered_score)
    assert compared["field_checks"] == baseline["field_checks"]
    assert compared["observations_sha256"] != baseline["observations_sha256"]
    assert compared["comparison_fingerprint_sha256"] != baseline["comparison_fingerprint_sha256"]

    altered_target = {**target, "svg_sha256": "d" * 64}
    changed = assess_unattested(altered_target, observations)
    assert changed["observations_sha256"] == baseline["observations_sha256"]
    assert changed["comparison_fingerprint_sha256"] != baseline["comparison_fingerprint_sha256"]


def test_no_match_or_missing_design_never_claims_an_observation_index():
    target = _approved_target()
    target["expected_fields"].pop("cta")
    target["missing_fields"] = ["cta"]
    report = assess_unattested(
        target,
        OCRObservationRequest(
            lines=[
                {"text": "双十一牙刷", "score": 0.30},
                {"text": "¥1990.00", "score": 0.98},
            ]
        ),
    )
    assert report["field_checks"] == [
        {"field": "title", "status": "MISMATCH", "matched_line_index": None},
        {"field": "price", "status": "MISMATCH", "matched_line_index": None},
        {"field": "cta", "status": "MISSING_IN_DESIGN", "matched_line_index": None},
    ]
    assert report["declared_consistency"] == "BLOCKED"
    assert report["release_ready"] is False
