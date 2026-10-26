"""M6 local evidence gate: deterministic fixtures only, no real SKU data."""
from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

import pytest
from PIL import Image

from app import storage
from scripts import real_sku_batch as batch
from scripts.validate_m6_cohort import (
    M6ValidationError,
    _safe_report_destination,
    _publish_report,
    validate_bundle,
)


REGISTRY_FIELDS = [
    "cohort_id", "sku_id", "product_category", "product_family_id",
    "variant_or_packaging_condition", "source_asset_authorization_status",
    "authorization_evidence_ref", "logo_authorization_status",
    "source_image_sha256", "distinct_sku_verified",
    "duplicate_source_image_reviewed", "reviewer_alias", "notes",
]
REVIEW_FIELDS = [
    "cohort_id", "sku_id", "poster_id", "source_version", "variant_id",
    "output_image_path", "source_product_sha256", "request_sha256",
    "spec_sha256", "output_image_sha256", "source_square_image_sha256",
    "reviewer_alias", "sku_integrity", "text_legibility", "title",
    "subtitle", "price", "original_price", "price_position",
    "original_price_position", "cta", "composition", "overall_accept",
    "overall_reason", "reviewed_at_utc",
]
NATIVE_MANIFEST_FIELDS = [
    "poster_id", "version", "sku_id", "status", "definite_labels",
    "ocr_text_labels", "unsure_labels", "product_sha256",
    "native_image_sha256", "action",
]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_csv(path: Path, fields: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _png(path: Path, size: tuple[int, int], color: tuple[int, int, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(path, format="PNG")


def _bundle(tmp_path, monkeypatch, count: int = 2) -> dict:
    store = tmp_path / "poster_store"
    monkeypatch.setenv("POSTER_DATA_DIR", str(store))
    monkeypatch.setenv("POSTER_EXECUTION_DB", str(tmp_path / "executions.sqlite3"))
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    manifest_rows = []
    for i in range(count):
        sku = f"sku-{i + 1:02d}"
        _png(source_dir / f"{sku}.png", (128, 160), (30 + i, 80, 120))
        manifest_rows.append({
            "sku_id": sku,
            "product_image": f"{sku}.png",
            "title": f"测试商品{i + 1}",
            "price": str(99 + i),
            "original_price": "",
            "subtitle": "",
            "cta": "立即抢购",
            "style": "蓝色科技风",
            "logo_image": "",
        })
    manifest = source_dir / "real_skus.csv"
    _write_csv(
        manifest,
        ["sku_id", "product_image", "title", "price", "original_price",
         "subtitle", "cta", "style", "logo_image"],
        manifest_rows,
    )
    output_dir = tmp_path / "m5g"
    batch.run_batch(manifest, output_dir, apply=True)
    journal_path = output_dir / "batch_import_results.json"
    journal = json.loads(journal_path.read_text(encoding="utf-8"))
    cohort_id = "m6-" + _sha(journal_path)[:12]

    registry_rows = []
    review_rows = []
    native_manifest_rows = []
    for i, item in enumerate(manifest_rows):
        sku = item["sku_id"]
        journal_row = journal["rows"][sku]
        poster_id = journal_row["poster_id"]
        version = 1
        square_path = storage.version_path(poster_id, version)
        square_sha = _sha(square_path)
        spec_path = storage.poster_dir(poster_id) / "versions" / "v1.json"
        spec_sha = _sha(spec_path)
        native_path = tmp_path / "native" / f"{sku}.png"
        _png(native_path, (1080, 1350), (120, 50 + i, 90))
        native_sha = _sha(native_path)
        native_manifest_rows.append({
            "poster_id": poster_id,
            "version": str(version),
            "sku_id": sku,
            "status": "READY",
            "definite_labels": "8",
            "ocr_text_labels": "5",
            "unsure_labels": "0",
            "product_sha256": journal_row["product_sha256"],
            "native_image_sha256": native_sha,
            "action": "synthetic test fixture",
        })
        registry_rows.append({
            "cohort_id": cohort_id,
            "sku_id": sku,
            "product_category": "care" if i % 2 == 0 else "home",
            "product_family_id": f"family-{i + 1}",
            "variant_or_packaging_condition": "plain-background-test",
            "source_asset_authorization_status": "AUTHORIZED",
            "authorization_evidence_ref": "tests-only-fixture",
            "logo_authorization_status": "NOT_APPLICABLE",
            "source_image_sha256": journal_row["product_sha256"],
            "distinct_sku_verified": "YES",
            "duplicate_source_image_reviewed": "YES",
            "reviewer_alias": "test-reviewer",
            "notes": "",
        })
        common = {
            "cohort_id": cohort_id,
            "sku_id": sku,
            "poster_id": poster_id,
            "source_version": str(version),
            "source_product_sha256": journal_row["product_sha256"],
            "request_sha256": journal_row["request_sha256"],
            "spec_sha256": spec_sha,
            "source_square_image_sha256": square_sha,
            "reviewer_alias": "test-reviewer",
            "sku_integrity": "PASS",
            "text_legibility": "PASS",
            "title": "PASS",
            "subtitle": "PASS",
            "price": "PASS",
            "original_price": "N/A",
            "price_position": "PASS",
            "original_price_position": "N/A",
            "cta": "PASS",
            "composition": "PASS",
            "overall_accept": "PASS",
            "overall_reason": "",
            "reviewed_at_utc": "2026-10-23T12:00:00Z",
        }
        review_rows.append({
            **common,
            "variant_id": "square",
            "output_image_path": str(square_path),
            "output_image_sha256": square_sha,
        })
        review_rows.append({
            **common,
            "variant_id": "feed_4x5_native",
            "output_image_path": str(native_path),
            "output_image_sha256": native_sha,
        })

    registry_path = tmp_path / "m6_cohort_registry.csv"
    reviews_path = tmp_path / "m6_output_reviews.csv"
    native_manifest_path = tmp_path / "native_trial_manifest.csv"
    _write_csv(registry_path, REGISTRY_FIELDS, registry_rows)
    _write_csv(reviews_path, REVIEW_FIELDS, review_rows)
    _write_csv(native_manifest_path, NATIVE_MANIFEST_FIELDS, native_manifest_rows)
    readiness_path = tmp_path / "native_trial_readiness.json"
    readiness = {
        "native_trial_manifest_sha256": _sha(native_manifest_path),
        "schema_version": "1.0",
        "variant_id": "feed_4x5_native",
        "can_score": True,
        "scope_mode": "batch_latest_per_sku",
        "source_batch_journal_sha256": _sha(journal_path),
        "source_manifest_sha256": journal["manifest_sha256"],
        "batch_sku_count": count,
        "total_versions": count,
        "ready_versions": count,
        "ocr_runtime_attested": False,
    }
    readiness_path.write_text(json.dumps(readiness), encoding="utf-8")
    return {
        "manifest": manifest,
        "registry": registry_path,
        "journal": journal_path,
        "reviews": reviews_path,
        "native_manifest": native_manifest_path,
        "readiness": readiness_path,
        "registry_rows": registry_rows,
        "review_rows": review_rows,
        "native_manifest_rows": native_manifest_rows,
        "readiness_data": readiness,
    }


def _validate(bundle: dict, *, min_skus: int = 2) -> dict:
    return validate_bundle(
        bundle["manifest"], bundle["registry"], bundle["journal"],
        bundle["reviews"], bundle["native_manifest"], bundle["readiness"],
        min_skus=min_skus,
    )


def test_valid_synthetic_bundle_passes_without_quality_score(tmp_path, monkeypatch):
    bundle = _bundle(tmp_path, monkeypatch)
    report = _validate(bundle)
    assert report["status"] == "PASS"
    assert report["expected_sku_count"] == 2
    assert report["expected_output_count"] == 4
    assert report["present_output_count"] == 4
    assert report["missing_output_count"] == 0
    assert report["product_quality_score_computed"] is False
    assert report["ocr_inference_performed"] is False
    assert report["paid_model_calls"] == 0
    assert report["input_sha256"]["batch_journal"] == _sha(bundle["journal"])


def test_cohort_id_must_bind_current_journal_sha(tmp_path, monkeypatch):
    bundle = _bundle(tmp_path, monkeypatch)
    wrong_id = "m6-" + "0" * 12
    for row in bundle["registry_rows"]:
        row["cohort_id"] = wrong_id
    for row in bundle["review_rows"]:
        row["cohort_id"] = wrong_id
    _write_csv(bundle["registry"], REGISTRY_FIELDS, bundle["registry_rows"])
    _write_csv(bundle["reviews"], REVIEW_FIELDS, bundle["review_rows"])

    report = _validate(bundle)
    assert report["status"] == "BLOCKED"
    assert any(
        "cohort_id 必须由当前 M5g journal SHA-256 派生" in error
        for error in report["errors"]
    )


def test_readiness_must_bind_exact_native_manifest(tmp_path, monkeypatch):
    bundle = _bundle(tmp_path, monkeypatch)
    readiness = bundle["readiness_data"]
    readiness["native_trial_manifest_sha256"] = "0" * 64
    bundle["readiness"].write_text(json.dumps(readiness), encoding="utf-8")

    report = _validate(bundle)
    assert report["status"] == "BLOCKED"
    assert any("native_manifest_sha256" in error for error in report["errors"])


def test_registry_authorization_must_be_explicit(tmp_path, monkeypatch):
    bundle = _bundle(tmp_path, monkeypatch)
    bundle["registry_rows"][0]["source_asset_authorization_status"] = "PENDING"
    _write_csv(bundle["registry"], REGISTRY_FIELDS, bundle["registry_rows"])
    report = _validate(bundle)
    assert report["status"] == "BLOCKED"
    assert any("授权状态不是 AUTHORIZED" in error for error in report["errors"])


def test_output_hash_mismatch_blocks_evidence(tmp_path, monkeypatch):
    bundle = _bundle(tmp_path, monkeypatch)
    bundle["review_rows"][0]["output_image_sha256"] = "0" * 64
    _write_csv(bundle["reviews"], REVIEW_FIELDS, bundle["review_rows"])
    report = _validate(bundle)
    assert report["status"] == "BLOCKED"
    assert any("output_image_sha256 与实际文件不一致" in error for error in report["errors"])


def test_missing_output_is_retained_in_denominator_and_blocks_gate(tmp_path, monkeypatch):
    bundle = _bundle(tmp_path, monkeypatch)
    native = next(row for row in bundle["review_rows"] if row["variant_id"] == "feed_4x5_native")
    native["output_image_path"] = ""
    native["output_image_sha256"] = ""
    native["overall_accept"] = "UNVERIFIED"
    native["overall_reason"] = "OUTPUT_MISSING"
    _write_csv(bundle["reviews"], REVIEW_FIELDS, bundle["review_rows"])
    report = _validate(bundle)
    assert report["status"] == "BLOCKED"
    assert report["expected_output_count"] == 4
    assert report["missing_output_count"] == 1
    assert any("保留在分母" in error for error in report["errors"])


def test_readiness_must_bind_exact_batch_journal(tmp_path, monkeypatch):
    bundle = _bundle(tmp_path, monkeypatch)
    readiness = bundle["readiness_data"]
    readiness["source_batch_journal_sha256"] = "0" * 64
    bundle["readiness"].write_text(json.dumps(readiness), encoding="utf-8")
    report = _validate(bundle)
    assert report["status"] == "BLOCKED"
    assert any("journal_sha256" in error for error in report["errors"])

def test_report_path_cannot_overwrite_any_input(tmp_path):
    manifest = tmp_path / "real_skus.csv"
    manifest.write_text("sku_id\n", encoding="utf-8")
    with pytest.raises(M6ValidationError, match="不能覆盖"):
        _safe_report_destination(manifest, (manifest,))


def test_report_path_does_not_overwrite_previous_evidence(tmp_path):
    report = tmp_path / "m6_report.json"
    report.write_text('{"status":"PASS"}', encoding="utf-8")
    with pytest.raises(M6ValidationError, match="已存在"):
        _safe_report_destination(report, (tmp_path / "manifest.csv",))

def test_native_manifest_hash_must_match_reviewed_native_png(tmp_path, monkeypatch):
    bundle = _bundle(tmp_path, monkeypatch)
    bundle["native_manifest_rows"][0]["native_image_sha256"] = "0" * 64
    _write_csv(
        bundle["native_manifest"], NATIVE_MANIFEST_FIELDS, bundle["native_manifest_rows"]
    )
    report = _validate(bundle)
    assert report["status"] == "BLOCKED"
    assert any("M5j native_image_sha256 不一致" in error for error in report["errors"])



def test_overall_pass_is_blocked_when_any_field_fails(tmp_path, monkeypatch):
    bundle = _bundle(tmp_path, monkeypatch)
    row = next(row for row in bundle["review_rows"] if row["variant_id"] == "square")
    row["price"] = "FAIL"
    row["overall_accept"] = "PASS"
    _write_csv(bundle["reviews"], REVIEW_FIELDS, bundle["review_rows"])

    report = _validate(bundle)

    assert report["status"] == "BLOCKED"
    assert report["field_overall_mismatch_count"] == 1
    assert any("字段含 FAIL/UNVERIFIED，禁止 overall PASS" in error for error in report["errors"])


def test_overall_pass_is_blocked_when_any_field_is_unverified(tmp_path, monkeypatch):
    bundle = _bundle(tmp_path, monkeypatch)
    row = next(row for row in bundle["review_rows"] if row["variant_id"] == "feed_4x5_native")
    row["title"] = "UNVERIFIED"
    row["overall_accept"] = "PASS"
    _write_csv(bundle["reviews"], REVIEW_FIELDS, bundle["review_rows"])

    report = _validate(bundle)

    assert report["status"] == "BLOCKED"
    assert report["field_overall_mismatch_count"] == 1
    assert any("字段含 FAIL/UNVERIFIED，禁止 overall PASS" in error for error in report["errors"])


def test_overall_fail_with_all_fields_pass_is_reported_for_manual_reconciliation(
    tmp_path, monkeypatch,
):
    bundle = _bundle(tmp_path, monkeypatch)
    row = next(row for row in bundle["review_rows"] if row["variant_id"] == "square")
    row["overall_accept"] = "FAIL"
    row["overall_reason"] = "HOLISTIC_LAYOUT_DEFECT"
    _write_csv(bundle["reviews"], REVIEW_FIELDS, bundle["review_rows"])

    report = _validate(bundle)

    assert report["status"] == "PASS"
    assert report["field_overall_mismatch_count"] == 1
    assert any("最终人工汇总中复核原因" in warning for warning in report["warnings"])


@pytest.mark.parametrize("artifact", ["product", "background", "receipt"])
def test_m6_gate_rejects_changed_original_m5g_artifact(
    tmp_path, monkeypatch, artifact,
):
    """Review hashes alone must not hide changes to the original M5g receipt."""
    bundle = _bundle(tmp_path, monkeypatch)
    journal = json.loads(bundle["journal"].read_text(encoding="utf-8"))
    poster_id = journal["rows"]["sku-01"]["poster_id"]
    poster_dir = storage.poster_dir(poster_id)

    if artifact == "receipt":
        meta_path = poster_dir / "meta.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["execution_receipt"]["key"] = "a-different-paid-job"
        meta_path.write_text(json.dumps(meta), encoding="utf-8")
    else:
        path = poster_dir / "assets" / f"{artifact}.png"
        with Image.open(path) as original:
            dimensions = original.size
        # Preserve format and dimensions: the bytes change while the reviewed
        # square PNG, spec and M5j manifest remain untouched.
        Image.new("RGB", dimensions, "#ab4c32").save(path, format="PNG")

    report = _validate(bundle)

    assert report["status"] == "BLOCKED"
    assert any(
        "M5g 原始产物与执行凭据不一致" in error
        for error in report["errors"]
    )


def test_m6_gate_rejects_symlinked_review_png_even_when_digest_matches(tmp_path, monkeypatch):
    """The review CSV must not silently follow a mutable image link."""
    bundle = _bundle(tmp_path, monkeypatch)
    row = next(
        row for row in bundle["review_rows"]
        if row["sku_id"] == "sku-01" and row["variant_id"] == "feed_4x5_native"
    )
    original = Path(row["output_image_path"])
    link = original.parent / "review-linked.png"
    link.symlink_to(original)
    row["output_image_path"] = str(link)
    _write_csv(bundle["reviews"], REVIEW_FIELDS, bundle["review_rows"])

    report = _validate(bundle)

    assert report["status"] == "BLOCKED"
    assert any("符号链接" in error for error in report["errors"])


def test_m6_gate_bounds_review_png_bytes_before_decoding(tmp_path, monkeypatch):
    """A parseable PNG with large trailing bytes cannot exhaust review memory."""
    bundle = _bundle(tmp_path, monkeypatch)
    row = next(
        row for row in bundle["review_rows"]
        if row["sku_id"] == "sku-01" and row["variant_id"] == "feed_4x5_native"
    )
    image_path = Path(row["output_image_path"])
    # Pillow accepts arbitrary trailing bytes after IEND. The old gate read the
    # whole file and considered it valid if the operator also updated hashes.
    with image_path.open("ab") as handle:
        handle.write(b"X" * (20 * 1024 * 1024 + 1))
    updated_sha = _sha(image_path)
    row["output_image_sha256"] = updated_sha
    native = next(
        item for item in bundle["native_manifest_rows"]
        if item["sku_id"] == "sku-01"
    )
    native["native_image_sha256"] = updated_sha
    _write_csv(
        bundle["native_manifest"], NATIVE_MANIFEST_FIELDS,
        bundle["native_manifest_rows"],
    )
    bundle["readiness_data"]["native_trial_manifest_sha256"] = _sha(
        bundle["native_manifest"]
    )
    bundle["readiness"].write_text(
        json.dumps(bundle["readiness_data"]), encoding="utf-8"
    )
    _write_csv(bundle["reviews"], REVIEW_FIELDS, bundle["review_rows"])

    report = _validate(bundle)

    assert report["status"] == "BLOCKED"
    assert any("20 MiB" in error for error in report["errors"])


def test_m6_gate_reports_decompression_bomb_as_blocker(tmp_path, monkeypatch):
    """A tiny PNG claiming massive dimensions must not crash the whole audit."""
    import struct
    import zlib

    bundle = _bundle(tmp_path, monkeypatch)
    row = next(
        row for row in bundle["review_rows"]
        if row["sku_id"] == "sku-01" and row["variant_id"] == "feed_4x5_native"
    )
    image_path = Path(row["output_image_path"])
    raw = bytearray(image_path.read_bytes())
    assert raw[12:16] == b"IHDR"
    raw[16:24] = struct.pack(">II", 40_000, 40_000)
    raw[29:33] = struct.pack(">I", zlib.crc32(raw[12:29]) & 0xffffffff)
    image_path.write_bytes(raw)
    _write_csv(bundle["reviews"], REVIEW_FIELDS, bundle["review_rows"])

    report = _validate(bundle)

    assert report["status"] == "BLOCKED"
    assert any("输出 PNG 不可读取" in error for error in report["errors"])


def _mock_ocr_evidence(bundle, tmp_path):
    """Local declaration fixture, NOT proof that real PaddleOCR was executed."""
    rows = bundle["review_rows"]
    data = {
        "schema_version": "m6-paddle-ocr-evidence-1",
        "cohort_id": rows[0]["cohort_id"],
        "reviews_csv_sha256": _sha(bundle["reviews"]),
        "sku_count": len(rows) // 2,
        "expected_variant_count": len(rows),
        "captured_variant_count": len(rows),
        "real_ocr_inference_attempted": True,
        "paddleocr_version": "3.3.0",
        "paddlepaddle_version": "3.2.0",
        "model_digests": {
            "detection": {"sha256": "a" * 64, "file_count": 1, "total_bytes": 3},
            "recognition": {"sha256": "b" * 64, "file_count": 1, "total_bytes": 3},
        },
        "automatically_accepted": False,
        "human_review_performed": False,
        "source_authorizations_verified": False,
        "min_20_real_skus_proven": False,
        "captured_at_utc": "2026-10-26T05:00:00Z",
        "elapsed_seconds": 0.2,
        "observations": [
            {
                "sku_id": row["sku_id"],
                "variant_id": row["variant_id"],
                "poster_id": row["poster_id"],
                "image_sha256": row["output_image_sha256"],
                "status": "CAPTURED_REVIEW_REQUIRED",
                "recognized_lines": [],
                "recognized_line_count": 0,
            }
            for row in rows
        ],
    }
    path = tmp_path / "m6_paddle_ocr_evidence.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path, data


def test_strict_ocr_gate_missing_file_blocks_without_forgetting_denominator(
    tmp_path, monkeypatch,
):
    bundle = _bundle(tmp_path, monkeypatch)
    report = validate_bundle(
        bundle["manifest"], bundle["registry"], bundle["journal"],
        bundle["reviews"], bundle["native_manifest"], bundle["readiness"],
        min_skus=2, require_ocr_evidence=True,
    )
    assert report["status"] == "BLOCKED"
    assert report["ocr_capture_consistency"] == "NOT_ASSESSED"
    assert report["expected_output_count"] == 4
    assert report["ocr_runtime_attested"] is False
    assert any("OCR" in error for error in report["errors"])


def test_strict_ocr_gate_matches_declared_capture_but_not_real_runtime_or_quality(
    tmp_path, monkeypatch,
):
    bundle = _bundle(tmp_path, monkeypatch)
    evidence, _ = _mock_ocr_evidence(bundle, tmp_path)
    report = validate_bundle(
        bundle["manifest"], bundle["registry"], bundle["journal"],
        bundle["reviews"], bundle["native_manifest"], bundle["readiness"],
        min_skus=2, require_ocr_evidence=True, ocr_evidence=evidence,
    )
    assert report["status"] == "PASS"
    assert report["ocr_capture_consistency"] == "MATCHED_DECLARED_REAL"
    assert report["ocr_capture_observation_count"] == 4
    assert report["input_sha256"]["ocr_capture"] == _sha(evidence)
    assert report["ocr_runtime_attested"] is False
    assert report["product_quality_score_computed"] is False
    assert report["real_ocr_quality_verified"] is False
    assert report["ocr_inference_performed"] is False


def test_strict_ocr_gate_rejects_stale_reviews_and_fake_capture(tmp_path, monkeypatch):
    bundle = _bundle(tmp_path, monkeypatch)
    evidence, data = _mock_ocr_evidence(bundle, tmp_path)
    data["real_ocr_inference_attempted"] = False
    evidence.write_text(json.dumps(data), encoding="utf-8")
    report = validate_bundle(
        bundle["manifest"], bundle["registry"], bundle["journal"],
        bundle["reviews"], bundle["native_manifest"], bundle["readiness"],
        min_skus=2, require_ocr_evidence=True, ocr_evidence=evidence,
    )
    assert report["status"] == "BLOCKED"
    assert report["ocr_capture_consistency"] == "BLOCKED"
    assert any("OCR" in error for error in report["errors"])
    data["real_ocr_inference_attempted"] = True
    evidence.write_text(json.dumps(data), encoding="utf-8")
    bundle["review_rows"][0]["overall_reason"] = "updated human note"
    _write_csv(bundle["reviews"], REVIEW_FIELDS, bundle["review_rows"])
    report = validate_bundle(
        bundle["manifest"], bundle["registry"], bundle["journal"],
        bundle["reviews"], bundle["native_manifest"], bundle["readiness"],
        min_skus=2, require_ocr_evidence=True, ocr_evidence=evidence,
    )
    assert report["status"] == "BLOCKED"
    assert report["ocr_capture_consistency"] == "BLOCKED"


def test_legacy_metadata_mode_labels_ocr_not_assessed_explicitly(tmp_path, monkeypatch):
    bundle = _bundle(tmp_path, monkeypatch)
    report = _validate(bundle)
    assert report["status"] == "PASS"
    assert report["ocr_capture_consistency"] == "NOT_ASSESSED"
    assert report["ocr_runtime_attested"] is False
    assert report["real_ocr_quality_verified"] is False


def test_m6_report_publication_writes_exact_bytes_without_temp_leftovers(tmp_path):
    output = tmp_path / "audits" / "report.json"
    output.parent.mkdir()
    payload = b'{"status":"BLOCKED","reason":"synthetic"}\n'
    _publish_report(output, payload)
    assert output.read_bytes() == payload
    assert not list(output.parent.glob(".m6-report-*.tmp"))


def test_m6_report_publication_never_overwrites_existing_evidence(tmp_path):
    output = tmp_path / "report.json"
    output.write_bytes(b"original evidence")
    with pytest.raises(M6ValidationError, match="已存在"):
        _publish_report(output, b"replacement")
    assert output.read_bytes() == b"original evidence"
    assert not list(tmp_path.glob(".m6-report-*.tmp"))


def test_m6_report_publication_is_safe_when_target_appears_during_publish(
    tmp_path, monkeypatch,
):
    """The precheck can race; the final operation must still be no-clobber."""
    import os

    output = _safe_report_destination(
        tmp_path / "report.json", (tmp_path / "source.csv",),
    )
    original_link = os.link

    def racing_link(src, dst, *, follow_symlinks=True):
        Path(dst).write_bytes(b"other writer's finished evidence")
        return original_link(src, dst, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(os, "link", racing_link)
    with pytest.raises(M6ValidationError, match="已存在"):
        _publish_report(output, b"our later output")
    assert output.read_bytes() == b"other writer's finished evidence"
    assert not list(tmp_path.glob(".m6-report-*.tmp"))


def test_m6_report_publication_cleans_temporary_file_on_link_error(
    tmp_path, monkeypatch,
):
    import os

    def inaccessible_target(*args, **kwargs):
        raise PermissionError("simulated denied publication")

    monkeypatch.setattr(os, "link", inaccessible_target)
    with pytest.raises(PermissionError, match="denied publication"):
        _publish_report(tmp_path / "report.json", b"not completed")
    assert not (tmp_path / "report.json").exists()
    assert not list(tmp_path.glob(".m6-report-*.tmp"))
