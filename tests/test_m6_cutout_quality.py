"""M6 cutout evidence is independent of visual human acceptance or real ONNX claims."""
from __future__ import annotations

import csv
import hashlib
import io
import json

from PIL import Image, ImageDraw

from app import cutout_quality, storage
from scripts import real_sku_batch
from scripts.prepare_m6_review import prepare_package


def _png(mask: bool) -> bytes:
    image = Image.new("RGBA", (200, 240),
                      (0, 0, 0, 0) if mask else (242, 242, 242, 255))
    ImageDraw.Draw(image).rectangle(
        (40, 30, 163, 205), fill=(65, 140, 220, 255)
    )
    output = io.BytesIO()
    image.save(output, "PNG")
    return output.getvalue()


def _batch(tmp_path, monkeypatch, *, with_cutout=True):
    monkeypatch.setenv("POSTER_DATA_DIR", str(tmp_path / "posters"))
    monkeypatch.setenv("POSTER_EXECUTION_DB", str(tmp_path / "execution.sqlite3"))
    source = tmp_path / "skus"
    source.mkdir()
    (source / "original.png").write_bytes(_png(False))
    (source / "cutout.png").write_bytes(_png(True))
    fieldnames = ["sku_id", "product_image", "title", "price"]
    if with_cutout:
        fieldnames.append("product_cutout_image")
    manifest = source / "manifest.csv"
    with manifest.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        row = {
            "sku_id": "test-sku", "product_image": "original.png",
            "title": "商品", "price": "199",
        }
        if with_cutout:
            row["product_cutout_image"] = "cutout.png"
        writer.writerow(row)
    output = tmp_path / "batch"
    real_sku_batch.run_batch(manifest, output, apply=True)
    return output / "batch_import_results.json"


def _rows(path):
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def test_metrics_and_contact_sheet_use_saved_png_pixels_without_models():
    src, cut = _png(False), _png(True)
    metrics, preview = cutout_quality.evidence_from_pngs(src, cut)
    assert metrics["schema_version"] == "m6-cutout-evidence-1"
    assert 0.3 < metrics["foreground_fraction"] < 0.6
    assert metrics["component_count"] == 1
    assert metrics["model_runtime_attested"] is False
    assert metrics["model_sha256"] is None
    assert metrics["requires_human_review"] is True
    assert metrics["automatically_accepted"] is False
    assert metrics["comparison_png_sha256"] == hashlib.sha256(preview).hexdigest()
    assert preview == cutout_quality.evidence_from_pngs(src, cut)[1]
    with Image.open(io.BytesIO(preview)) as image:
        assert image.size == (1024, 512)
        assert image.getpixel((70, 40)) != image.getpixel((582, 40))
    assert metrics["source_png_sha256"] == hashlib.sha256(src).hexdigest()


def test_risky_masks_are_flags_not_quality_pass():
    original = Image.new("RGBA", (200, 200), (180, 180, 180, 255))
    subject = Image.new("RGBA", (200, 200), (0, 0, 0, 0))
    ImageDraw.Draw(subject).rectangle((0, 0, 198, 198), fill=(80, 80, 80, 255))
    result = cutout_quality.inspect_cutout(original, subject)
    assert "HIGH_FOREGROUND_COVERAGE" in result["machine_risks"]
    assert "SUBJECT_TOUCHES_FRAME" in result["machine_risks"]
    assert result["automatically_accepted"] is False


def test_m6_batch_export_adds_cutout_review_worksheet_without_changing_legacy_reviews(
    tmp_path, monkeypatch,
):
    journal = _batch(tmp_path, monkeypatch)
    output = tmp_path / "review"
    planned = prepare_package(journal, output)
    assert planned["mode"] == "check" and not output.exists()
    report = prepare_package(journal, output, apply=True)
    assert report["cutout_candidate_count"] == 1
    assert report["cutout_evidence_count"] == 1
    assert report["cutout_human_accepted"] == 0
    assert report["cutout_model_inference_performed"] is False
    rows = _rows(output / "m6_cutout_reviews.csv")
    assert len(rows) == 1
    row = rows[0]
    assert row["quality_state"] == "REVIEW_REQUIRED"
    assert row["human_accept"] == "UNVERIFIED"
    assert row["subject_integrity_review"] == "UNVERIFIED"
    assert row["edge_artifact_review"] == "UNVERIFIED"
    assert row["brand_mark_review"] == "UNVERIFIED"
    assert row["model_sha256"] == ""
    assert row["model_runtime_attested"] == "NO"
    assert row["candidate_origin"] == "USER_SUPPLIED_OR_PREVIEW_NOT_ATTESTED"
    for name in (row["comparison_image_path"],):
        assert name.startswith("images/") and ".." not in name
        raw = (output / name).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == row["comparison_image_sha256"]
    meta = json.loads((output / "m6_cutout_evidence.json").read_text())
    assert meta["schema_version"] == "m6-cutout-evidence-1"
    assert meta["candidate_count"] == 1
    assert meta["items"][0]["metrics"]["automatically_accepted"] is False
    legacy = _rows(output / "m6_output_reviews.csv")
    assert len(legacy) == 2 and all(x["overall_accept"] == "UNVERIFIED" for x in legacy)
    assert report["ocr_inference_performed"] is False


def test_m6_original_only_sku_is_not_misreported_as_model_output(tmp_path, monkeypatch):
    journal = _batch(tmp_path, monkeypatch, with_cutout=False)
    output = tmp_path / "review"
    report = prepare_package(journal, output, apply=True)
    assert report["cutout_candidate_count"] == 0
    assert report["cutout_evidence_count"] == 0
    assert report["cutout_human_accepted"] == 0
    rows = _rows(output / "m6_cutout_reviews.csv")
    assert len(rows) == 1
    assert rows[0]["quality_state"] == "NOT_APPLICABLE"
    assert rows[0]["comparison_image_path"] == ""
    assert rows[0]["human_accept"] == "N/A"
    assert not list((output / "images").glob("*cutout-comparison.png"))
    assert len(_rows(output / "m6_output_reviews.csv")) == 2


def test_cutout_asset_tampering_refuses_comparison_and_preserves_review_denominator(
    tmp_path, monkeypatch,
):
    journal = _batch(tmp_path, monkeypatch)
    ledger = json.loads(journal.read_text())
    poster_id = ledger["rows"]["test-sku"]["poster_id"]
    asset = storage.poster_dir(poster_id) / "assets" / "product_cutout.png"
    asset.write_bytes(_png(False))
    output = tmp_path / "review"
    report = prepare_package(journal, output, apply=True)
    assert report["missing_output_count"] == 2
    assert report["cutout_evidence_count"] == 0
    assert report["cutout_candidate_count"] == 1
    row = _rows(output / "m6_cutout_reviews.csv")[0]
    assert row["quality_state"] == "SOURCE_INVALID"
    assert row["human_accept"] == "UNVERIFIED"
    assert row["comparison_image_path"] == ""
    assert len(_rows(output / "m6_output_reviews.csv")) == 2



def test_cutout_verifier_is_read_only_and_returns_pending_human_review(tmp_path, monkeypatch):
    from scripts.verify_m6_cutout_evidence import verify_cutout_evidence

    journal = _batch(tmp_path, monkeypatch)
    output = tmp_path / "review"
    prepare_package(journal, output, apply=True)
    before = (output / "m6_cutout_reviews.csv").read_bytes()
    result = verify_cutout_evidence(output, journal, min_skus=1)
    assert result["status"] == "HUMAN_REVIEW_REQUIRED"
    assert result["candidate_count"] == 1
    assert result["operator_accepted_count"] == 0
    assert result["model_runtime_attested"] is False
    assert (output / "m6_cutout_reviews.csv").read_bytes() == before


def test_cutout_verifier_rejects_corrupted_comparison_without_exposing_approval(
    tmp_path, monkeypatch,
):
    import pytest
    from scripts.verify_m6_cutout_evidence import CutoutEvidenceError, verify_cutout_evidence

    journal = _batch(tmp_path, monkeypatch)
    output = tmp_path / "review"
    prepare_package(journal, output, apply=True)
    row = _rows(output / "m6_cutout_reviews.csv")[0]
    (output / row["comparison_image_path"]).write_bytes(b"tampered-png")
    with pytest.raises(CutoutEvidenceError):
        verify_cutout_evidence(output, journal, min_skus=1)


def test_cutout_verifier_requires_human_details_for_operator_pass(tmp_path, monkeypatch):
    import pytest
    from scripts.verify_m6_cutout_evidence import CutoutEvidenceError, verify_cutout_evidence
    from app.cutout_quality import REVIEW_FIELDS

    journal = _batch(tmp_path, monkeypatch)
    output = tmp_path / "review"
    prepare_package(journal, output, apply=True)
    path = output / "m6_cutout_reviews.csv"
    row = _rows(path)[0]
    row["human_accept"] = "PASS"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=REVIEW_FIELDS)
        writer.writeheader()
        writer.writerow(row)
    with pytest.raises(CutoutEvidenceError, match="人工"):
        verify_cutout_evidence(output, journal, min_skus=1)
    for key in ("subject_integrity_review", "edge_artifact_review", "brand_mark_review"):
        row[key] = "PASS"
    row["reviewer_alias"] = "operator-1"
    row["reviewed_at_utc"] = "2026-10-27T12:00:00+00:00"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=REVIEW_FIELDS)
        writer.writeheader()
        writer.writerow(row)
    verified = verify_cutout_evidence(output, journal, min_skus=1)
    assert verified["status"] == "OPERATOR_ASSERTED_NOT_ATTESTED"
    assert verified["operator_accepted_count"] == 1
    assert verified["model_runtime_attested"] is False


def test_cutout_verifier_enforces_twenty_distinct_skus_for_real_acceptance(
    tmp_path, monkeypatch,
):
    import pytest
    from scripts.verify_m6_cutout_evidence import CutoutEvidenceError, verify_cutout_evidence

    journal = _batch(tmp_path, monkeypatch)
    output = tmp_path / "review"
    prepare_package(journal, output, apply=True)
    with pytest.raises(CutoutEvidenceError, match="20"):
        verify_cutout_evidence(output, journal)



def test_zip_contains_actual_cutout_contact_sheet_and_is_hash_bound(tmp_path, monkeypatch):
    import zipfile
    import pytest
    from app.m6_package_download_routes import _zip_contents

    journal = _batch(tmp_path, monkeypatch)
    folder = journal.parent / "m6_review_package"
    prepare_package(journal, folder, apply=True)
    record = json.loads(journal.read_text())["rows"]["test-sku"]
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        _zip_contents(archive, folder, {"test-sku": record["poster_id"]})
    with zipfile.ZipFile(io.BytesIO(out.getvalue())) as archive:
        names = archive.namelist()
        assert "m6_cutout_evidence.json" in names
        assert "m6_cutout_reviews.csv" in names
        assert "images/test-sku-cutout-comparison.png" in names
        row = _rows(folder / "m6_cutout_reviews.csv")[0]
        assert (hashlib.sha256(archive.read(row["comparison_image_path"])).hexdigest()
                == row["comparison_image_sha256"])
    picture = folder / "images" / "test-sku-cutout-comparison.png"
    picture.write_bytes(b"swapped")
    with pytest.raises(Exception):
        with zipfile.ZipFile(io.BytesIO(), "w") as archive:
            _zip_contents(archive, folder, {"test-sku": record["poster_id"]})



def test_original_only_cannot_forge_cutout_model_provenance(tmp_path, monkeypatch):
    import pytest
    from app.cutout_quality import REVIEW_FIELDS
    from scripts.verify_m6_cutout_evidence import CutoutEvidenceError, verify_cutout_evidence

    journal = _batch(tmp_path, monkeypatch, with_cutout=False)
    folder = tmp_path / "m6"
    prepare_package(journal, folder, apply=True)
    field = folder / "m6_cutout_reviews.csv"
    rows = _rows(field)
    rows[0]["model_sha256"] = "f" * 64
    with field.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=REVIEW_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    with pytest.raises(CutoutEvidenceError, match="原图模式"):
        verify_cutout_evidence(folder, journal, min_skus=1)
