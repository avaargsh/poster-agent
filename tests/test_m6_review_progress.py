"""M6 in-browser review-progress preview, using only synthetic CSV metadata."""
from __future__ import annotations

import csv
import io

from fastapi.testclient import TestClient

from app.main import app
from scripts.validate_m6_cohort import REGISTRY_FIELDS, REVIEW_FIELDS

client = TestClient(app)
SHA = "a" * 64
COHORT = "m6-example0001"


def _csv_bytes(fields: tuple[str, ...], rows: list[dict]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=fields)
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode("utf-8")


def _fixture() -> tuple[list[dict], list[dict]]:
    registry = []
    reviews = []
    for sku in ("sku-one", "sku-two"):
        registry.append({
            **dict.fromkeys(REGISTRY_FIELDS, ""),
            "cohort_id": COHORT,
            "sku_id": sku,
            "source_asset_authorization_status": "AUTHORIZED",
            "authorization_evidence_ref": "operator-attestation",
            "logo_authorization_status": "NOT_APPLICABLE",
            "distinct_sku_verified": "YES",
            "duplicate_source_image_reviewed": "YES",
            "reviewer_alias": "operator",
        })
        for variant in ("square", "feed_4x5_native"):
            reviews.append({
                **dict.fromkeys(REVIEW_FIELDS, "PASS"),
                "cohort_id": COHORT,
                "sku_id": sku,
                "variant_id": variant,
                "output_image_path": "images/review.png",
                "output_image_sha256": SHA,
                "reviewer_alias": "operator",
                "overall_accept": "PASS",
                "overall_reason": "",
                "reviewed_at_utc": "2026-10-26T09:00:00Z",
                "subtitle": "N/A",
                "original_price": "N/A",
                "original_price_position": "N/A",
            })
    return registry, reviews


def _post(registry: list[dict], reviews: list[dict]):
    return client.post(
        "/api/m6/review-progress",
        files={
            "registry": ("m6_cohort_registry.csv", _csv_bytes(REGISTRY_FIELDS, registry), "text/csv"),
            "reviews": ("m6_output_reviews.csv", _csv_bytes(REVIEW_FIELDS, reviews), "text/csv"),
        },
    )


def test_m6_progress_counts_only_csv_assertions_and_never_claims_ocr():
    registry, reviews = _fixture()
    response = _post(registry, reviews)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["scope"] == "csv_preview_only"
    assert result["cohort_id"] == COHORT
    assert result["sku_total"] == 2
    assert result["expected_outputs"] == 4
    assert result["reported_outputs"] == 4
    assert result["review_complete"] == 4
    assert result["review_pending"] == 0
    assert result["missing_outputs"] == 0
    assert result["governance_asserted"] == 2
    assert result["governance_pending"] == 0
    assert result["ocr_verified"] is False
    assert result["image_hashes_verified"] is False
    assert result["approved_for_publication"] is False
    assert result["issues"] == []
    assert "images/review.png" not in response.text


def test_m6_progress_highlights_unreviewed_and_unauthorized_rows():
    registry, reviews = _fixture()
    registry[1]["source_asset_authorization_status"] = "PENDING"
    reviews[1]["overall_accept"] = "UNVERIFIED"
    reviews[1]["overall_reason"] = "NOT_REVIEWED"
    reviews[1]["reviewed_at_utc"] = ""
    reviews[1]["title"] = "UNVERIFIED"
    response = _post(registry, reviews)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["governance_asserted"] == 1
    assert result["governance_pending"] == 1
    assert result["review_complete"] == 3
    assert result["review_pending"] == 1
    assert any(item["code"] == "GOVERNANCE_PENDING" for item in result["issues"])
    assert any(item["code"] == "REVIEW_PENDING" for item in result["issues"])


def test_m6_progress_preserves_missing_variant_denominator():
    registry, reviews = _fixture()
    reviews.pop()
    response = _post(registry, reviews)
    assert response.status_code == 200
    result = response.json()
    assert result["expected_outputs"] == 4
    assert result["reported_outputs"] == 3
    assert result["review_pending"] == 1
    assert any(item["code"] == "MISSING_ROW" for item in result["issues"])


def test_m6_progress_never_counts_missing_output_as_reviewed():
    registry, reviews = _fixture()
    reviews[0]["output_image_path"] = ""
    reviews[0]["output_image_sha256"] = ""
    response = _post(registry, reviews)
    assert response.status_code == 200
    result = response.json()
    assert result["missing_outputs"] == 1
    assert result["review_complete"] == 3
    assert any(item["code"] == "OUTPUT_MISSING" for item in result["issues"])


def test_m6_progress_rejects_duplicate_review_and_cross_cohort():
    registry, reviews = _fixture()
    response = _post(registry, reviews + [reviews[0]])
    assert response.status_code == 422
    reviews[0]["cohort_id"] = "m6-anothercohort"
    response = _post(registry, reviews)
    assert response.status_code == 422


def test_m6_progress_bounds_upload_and_rejects_bad_header():
    registry, reviews = _fixture()
    response = client.post(
        "/api/m6/review-progress",
        files={
            "registry": ("registry.csv", _csv_bytes(REGISTRY_FIELDS, registry), "text/csv"),
            "reviews": ("reviews.csv", b"x" * (2_000_001), "text/csv"),
        },
    )
    assert response.status_code == 413
    response = client.post(
        "/api/m6/review-progress",
        files={
            "registry": ("registry.csv", _csv_bytes(REGISTRY_FIELDS, registry), "text/csv"),
            "reviews": ("reviews.csv", b"sku_id,overall_accept\nsku-one,PASS\n", "text/csv"),
        },
    )
    assert response.status_code == 422


def test_m6_progress_never_accepts_pass_when_field_failed():
    registry, reviews = _fixture()
    reviews[0]["sku_integrity"] = "FAIL"
    response = _post(registry, reviews)
    assert response.status_code == 200
    assert response.json()["review_complete"] == 3
    assert any(item["code"] == "REVIEW_PENDING" for item in response.json()["issues"])


def test_m6_progress_requires_real_utc_review_time():
    registry, reviews = _fixture()
    reviews[0]["reviewed_at_utc"] = "2026-10-26T09:00:00+08:00"
    response = _post(registry, reviews)
    assert response.status_code == 200
    assert response.json()["review_complete"] == 3


def test_workbench_exposes_m6_local_csv_preview_inputs():
    page = client.get("/")
    assert page.status_code == 200
    assert 'id="m6RegistryCsv"' in page.text
    assert 'id="m6ReviewsCsv"' in page.text
    assert "/api/m6/review-progress" in page.text
