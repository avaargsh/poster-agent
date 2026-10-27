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


def _bundle(tmp_path, monkeypatch, count: int = 2, source_format: str = "PNG") -> dict:
    store = tmp_path / "poster_store"
    monkeypatch.setenv("POSTER_DATA_DIR", str(store))
    monkeypatch.setenv("POSTER_EXECUTION_DB", str(tmp_path / "executions.sqlite3"))
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    manifest_rows = []
    for i in range(count):
        sku = f"sku-{i + 1:02d}"
        suffix = ".jpg" if source_format == "JPEG" else ".png"
        source_name = f"{sku}{suffix}"
        Image.new("RGB", (128, 160), (30 + i, 80, 120)).save(
            source_dir / source_name, format=source_format
        )
        manifest_rows.append({
            "sku_id": sku,
            "product_image": source_name,
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
            "product_sha256": _sha(storage.poster_dir(poster_id) / "assets" / "product.png"),
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


@pytest.mark.parametrize("source_format", ["JPEG", "PNG"])
def test_real_native_audit_compares_normalized_assets_not_upload_bytes(
    tmp_path, monkeypatch, source_format,
):
    from evals.native_batch_scope import load_batch_scope
    from evals.native_trial import run_trial

    bundle = _bundle(tmp_path, monkeypatch, source_format=source_format)
    audit_dir = tmp_path / "real-audit"
    run_trial("test-reviewer", audit_dir, batch_scope=load_batch_scope(bundle["journal"]))
    bundle["native_manifest"] = audit_dir / "native_trial_manifest.csv"
    bundle["readiness"] = audit_dir / "native_trial_readiness.json"
    journal = json.loads(bundle["journal"].read_text())
    with bundle["native_manifest"].open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            # An RGB source is normalized to RGBA PNG on upload. These are
            # deliberately different byte identities, even for a PNG input.
            assert row["product_sha256"] != journal["rows"][row["sku_id"]]["product_sha256"]
    report = _validate(bundle)
    assert report["status"] == "BLOCKED"  # Human review and real OCR remain required.
    assert not any("product_sha256" in error for error in report["errors"])


def test_native_manifest_cannot_substitute_original_upload_hash(tmp_path, monkeypatch):
    bundle = _bundle(tmp_path, monkeypatch, source_format="JPEG")
    journal = json.loads(bundle["journal"].read_text())
    row = bundle["native_manifest_rows"][0]
    row["product_sha256"] = journal["rows"][row["sku_id"]]["product_sha256"]
    _write_csv(bundle["native_manifest"], NATIVE_MANIFEST_FIELDS, bundle["native_manifest_rows"])
    bundle["readiness_data"]["native_trial_manifest_sha256"] = _sha(bundle["native_manifest"])
    bundle["readiness"].write_text(json.dumps(bundle["readiness_data"]), encoding="utf-8")
    report = _validate(bundle)
    assert report["status"] == "BLOCKED"
    assert any("product_sha256" in error for error in report["errors"])


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


def test_strict_m6_report_exposes_missing_spatial_ocr_coverage(
    tmp_path, monkeypatch,
):
    bundle = _bundle(tmp_path, monkeypatch)
    evidence, data = _mock_ocr_evidence(bundle, tmp_path)
    for observation in data["observations"]:
        observation["recognized_lines"] = [{
            "text": "¥129",
            "confidence": 0.95,
            "bbox": ([65, 668, 200, 720]
                     if observation["variant_id"] == "square" else None),
        }]
        observation["recognized_line_count"] = 1
    data["positioned_line_count"] = 2
    data["unlocated_line_count"] = 2
    evidence.write_text(json.dumps(data), encoding="utf-8")
    report = validate_bundle(
        bundle["manifest"], bundle["registry"], bundle["journal"],
        bundle["reviews"], bundle["native_manifest"], bundle["readiness"],
        min_skus=2, require_ocr_evidence=True, ocr_evidence=evidence,
    )
    assert report["status"] == "PASS"
    assert report["ocr_capture_positioned_line_count"] == 2
    assert report["ocr_capture_unlocated_line_count"] == 2
    assert report["real_ocr_quality_verified"] is False
    assert any("缺少可信检测框" in warning for warning in report["warnings"])


def _separate_human_reviews(bundle, tmp_path):
    """Copy final manual worksheet away from OCR-bound original, with human edits."""
    from copy import deepcopy
    final = deepcopy(bundle["review_rows"])
    final[0]["reviewer_alias"] = "human-reviewer"
    final[0]["reviewed_at_utc"] = "2026-10-27T04:00:00Z"
    final[0]["overall_reason"] = "Independently reviewed image"
    folder = tmp_path / "operator_downloads"
    folder.mkdir()
    path = folder / "reviewed_final.csv"
    _write_csv(path, REVIEW_FIELDS, final)
    return path, final


def _separate_gate(bundle, evidence, final_reviews):
    return validate_bundle(
        bundle["manifest"], bundle["registry"], bundle["journal"],
        final_reviews, bundle["native_manifest"], bundle["readiness"],
        min_skus=2, require_ocr_evidence=True, ocr_evidence=evidence,
        ocr_source_reviews=bundle["reviews"],
    )


def test_strict_m6_gate_accepts_distinct_human_worksheet_without_rebinding_old_ocr(
    tmp_path, monkeypatch,
):
    bundle = _bundle(tmp_path, monkeypatch)
    evidence, _ = _mock_ocr_evidence(bundle, tmp_path)
    original_sha = _sha(bundle["reviews"])
    final_path, _ = _separate_human_reviews(bundle, tmp_path)
    final_sha = _sha(final_path)
    assert original_sha != final_sha
    assert validate_bundle(
        bundle["manifest"], bundle["registry"], bundle["journal"],
        final_path, bundle["native_manifest"], bundle["readiness"],
        min_skus=2, require_ocr_evidence=True, ocr_evidence=evidence,
    )["status"] == "BLOCKED"  # Legacy strict mode stays strict.
    report = _separate_gate(bundle, evidence, final_path)
    assert report["status"] == "PASS"
    assert report["ocr_capture_consistency"] == "MATCHED_DECLARED_REAL"
    assert report["ocr_source_binding"] == "MATCHED_IMMUTABLE_FIELDS"
    assert report["input_sha256"]["ocr_source_reviews"] == original_sha
    assert report["input_sha256"]["output_reviews"] == final_sha
    assert report["ocr_runtime_attested"] is False
    assert report["real_ocr_quality_verified"] is False


@pytest.mark.parametrize("changed_field,value", [
    ("poster_id", "f" * 32),
    ("output_image_sha256", "e" * 64),
    ("source_version", "2"),
    ("source_product_sha256", "d" * 64),
    ("spec_sha256", "c" * 64),
    ("output_image_path", "images/other.png"),
    ("variant_id", "bogus"),
    ("sku_id", "other-sku"),
])
def test_split_ocr_gate_rejects_changed_frozen_identity(
    tmp_path, monkeypatch, changed_field, value,
):
    bundle = _bundle(tmp_path, monkeypatch)
    evidence, _ = _mock_ocr_evidence(bundle, tmp_path)
    final_path, final = _separate_human_reviews(bundle, tmp_path)
    final[0][changed_field] = value
    _write_csv(final_path, REVIEW_FIELDS, final)
    report = _separate_gate(bundle, evidence, final_path)
    assert report["status"] == "BLOCKED"
    assert report["ocr_source_binding"] == "BLOCKED"
    assert report["ocr_runtime_attested"] is False
    assert any("OCR" in msg or "快照" in msg for msg in report["errors"])


def test_split_ocr_gate_rejects_missing_output_or_wrong_source_snapshot(
    tmp_path, monkeypatch,
):
    bundle = _bundle(tmp_path, monkeypatch)
    evidence, _ = _mock_ocr_evidence(bundle, tmp_path)
    final_path, final = _separate_human_reviews(bundle, tmp_path)
    _write_csv(final_path, REVIEW_FIELDS, final[:-1])
    missing = _separate_gate(bundle, evidence, final_path)
    assert missing["status"] == "BLOCKED"
    assert missing["expected_output_count"] == 4
    assert missing["ocr_source_binding"] == "BLOCKED"
    _write_csv(final_path, REVIEW_FIELDS, final)
    frozen_rows = [dict(row) for row in bundle["review_rows"]]
    frozen_rows[0]["overall_reason"] = "Changed after capture"
    _write_csv(bundle["reviews"], REVIEW_FIELDS, frozen_rows)
    stale = _separate_gate(bundle, evidence, final_path)
    assert stale["status"] == "BLOCKED"
    assert stale["ocr_capture_consistency"] == "BLOCKED"


def test_split_ocr_gate_requires_ocr_evidence_and_rejects_symlinked_source(
    tmp_path, monkeypatch,
):
    bundle = _bundle(tmp_path, monkeypatch)
    final_path, _ = _separate_human_reviews(bundle, tmp_path)
    absent = validate_bundle(
        bundle["manifest"], bundle["registry"], bundle["journal"],
        final_path, bundle["native_manifest"], bundle["readiness"],
        min_skus=2, ocr_source_reviews=bundle["reviews"],
    )
    assert absent["status"] == "BLOCKED"
    evidence, _ = _mock_ocr_evidence(bundle, tmp_path)
    linked = tmp_path / "ocr_source_link.csv"
    linked.symlink_to(bundle["reviews"])
    with pytest.raises(M6ValidationError):
        validate_bundle(
            bundle["manifest"], bundle["registry"], bundle["journal"],
            final_path, bundle["native_manifest"], bundle["readiness"],
            min_skus=2, ocr_evidence=evidence, ocr_source_reviews=linked,
        )


def test_split_ocr_gate_resolves_relative_artifacts_against_frozen_source_directory(
    tmp_path, monkeypatch,
):
    import os
    bundle = _bundle(tmp_path, monkeypatch)
    original = []
    for row in bundle["review_rows"]:
        copy = dict(row)
        copy["output_image_path"] = os.path.relpath(
            row["output_image_path"], bundle["reviews"].parent,
        )
        original.append(copy)
    _write_csv(bundle["reviews"], REVIEW_FIELDS, original)
    bundle["review_rows"] = original
    evidence, _ = _mock_ocr_evidence(bundle, tmp_path)
    final_path, _ = _separate_human_reviews(bundle, tmp_path)
    report = _separate_gate(bundle, evidence, final_path)
    assert report["status"] == "PASS"
    assert report["present_output_count"] == 4


def test_m6_csv_parser_and_sha_use_the_same_byte_snapshot(tmp_path, monkeypatch):
    """A changed file between two opens cannot be parsed under the old hash."""
    import io

    from scripts.validate_m6_cohort import _read_csv

    file = tmp_path / "m6_review.csv"
    file.write_text("sku_id\noriginal\n", encoding="utf-8")
    original_sha = _sha(file)
    real_open = Path.open

    def swapped_text_open(path, mode="r", *args, **kwargs):
        if path == file and mode == "r":
            return io.StringIO("sku_id\nforged\n")
        return real_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", swapped_text_open)
    _, rows, digest = _read_csv(file, ("sku_id",), "M6 CSV")
    assert rows == [{"sku_id": "original"}]
    assert digest == original_sha


def _m6_operator_summary_fixture(tmp_path, monkeypatch):
    from copy import deepcopy
    from scripts.summarize_m6_operator_quality import summarize_operator_quality

    bundle = _bundle(tmp_path, monkeypatch)
    evidence, _ = _mock_ocr_evidence(bundle, tmp_path)
    reviewed = deepcopy(bundle["review_rows"])
    # One explicitly reviewed output fails price, another remains unverified.
    reviewed[0]["price"] = "FAIL"
    reviewed[0]["overall_accept"] = "FAIL"
    reviewed[0]["overall_reason"] = "Incorrect price on the actual poster"
    reviewed[1]["price"] = "UNVERIFIED"
    reviewed[1]["overall_accept"] = "UNVERIFIED"
    reviewed[1]["overall_reason"] = "Review pending"
    final = tmp_path / "final_operator_reviews.csv"
    _write_csv(final, REVIEW_FIELDS, reviewed)
    report = validate_bundle(
        bundle["manifest"], bundle["registry"], bundle["journal"],
        final, bundle["native_manifest"], bundle["readiness"],
        min_skus=2, ocr_evidence=evidence, require_ocr_evidence=True,
        ocr_source_reviews=bundle["reviews"],
    )
    assert report["status"] == "PASS", report["errors"]
    return final, report, summarize_operator_quality


def test_m6_operator_quality_summary_reports_denominators_and_two_variant_pairing(
    tmp_path, monkeypatch,
):
    final, report, summarize = _m6_operator_summary_fixture(tmp_path, monkeypatch)
    summary = summarize(report, final)
    assert summary["status"] == "OPERATOR_LABELS_ONLY_NOT_ATTESTED"
    assert summary["minimum_sku_count_met"] is False
    assert summary["sku_count"] == 2
    assert summary["variant_count"] == 4
    assert summary["overall"]["PASS"] == 2
    assert summary["overall"]["FAIL"] == 1
    assert summary["overall"]["UNVERIFIED"] == 1
    assert summary["both_variants_passed"]["count"] == 1
    assert summary["both_variants_passed"]["denominator"] == 2
    assert summary["both_variants_passed"]["fraction"] == 0.5
    assert summary["by_variant"]["square"]["FAIL"] == 1
    assert summary["by_variant"]["feed_4x5_native"]["UNVERIFIED"] == 1
    assert summary["by_field"]["price"]["FAIL"] == 1
    assert summary["by_field"]["price"]["UNVERIFIED"] == 1
    assert summary["by_field"]["original_price"]["N/A"] == 4
    assert summary["ocr_runtime_attested"] is False
    assert summary["source_authorizations_verified"] is False
    assert summary["approved_for_publication"] is False
    as_text = json.dumps(summary, ensure_ascii=False)
    for sensitive in ("sku-01", "sku-02", "Incorrect price on the actual poster",
                      str(final), "test-reviewer"):
        assert sensitive not in as_text


def test_m6_operator_quality_summary_fails_closed_on_changed_csv_and_forged_report(
    tmp_path, monkeypatch,
):
    from copy import deepcopy
    from scripts.summarize_m6_operator_quality import OperatorQualityError

    final, report, summarize = _m6_operator_summary_fixture(tmp_path, monkeypatch)
    forged = deepcopy(report)
    forged["status"] = "BLOCKED"
    with pytest.raises(OperatorQualityError):
        summarize(forged, final)
    forged = deepcopy(report)
    forged["expected_output_count"] = 2
    with pytest.raises(OperatorQualityError):
        summarize(forged, final)
    forged = deepcopy(report)
    forged["ocr_capture_consistency"] = "NOT_ASSESSED"
    with pytest.raises(OperatorQualityError):
        summarize(forged, final)
    final.write_bytes(final.read_bytes() + b"\n")
    with pytest.raises(OperatorQualityError):
        summarize(report, final)


def test_m6_operator_quality_summary_does_not_use_metadata_only_pass(
    tmp_path, monkeypatch,
):
    from scripts.summarize_m6_operator_quality import (
        OperatorQualityError, summarize_operator_quality,
    )

    bundle = _bundle(tmp_path, monkeypatch)
    metadata_report = _validate(bundle)
    assert metadata_report["status"] == "PASS"
    with pytest.raises(OperatorQualityError, match="OCR"):
        summarize_operator_quality(metadata_report, bundle["reviews"])


def test_m6_operator_quality_cli_refuses_to_overwrite_existing_report_or_source(
    tmp_path, monkeypatch,
):
    from scripts.summarize_m6_operator_quality import main

    reviews, report, _ = _m6_operator_summary_fixture(tmp_path, monkeypatch)
    source_report = tmp_path / "m6_validation_report.json"
    source_report.write_text(json.dumps(report), encoding="utf-8")
    target = tmp_path / "m6_operator_summary.json"
    assert main(["--validation-report", str(source_report),
                 "--reviews", str(reviews), "--output", str(target)]) == 0
    produced = json.loads(target.read_text(encoding="utf-8"))
    assert produced["status"] == "OPERATOR_LABELS_ONLY_NOT_ATTESTED"
    original = target.read_bytes()
    with pytest.raises(SystemExit):
        main(["--validation-report", str(source_report),
              "--reviews", str(reviews), "--output", str(target)])
    assert target.read_bytes() == original
    with pytest.raises(SystemExit):
        main(["--validation-report", str(source_report),
              "--reviews", str(reviews), "--output", str(reviews)])
