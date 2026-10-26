"""Synthetic offline M6 overlay fixtures: no real OCR or SKU acceptance."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import zipfile

import pytest
from PIL import Image

from scripts.capture_m6_paddle_ocr import REVIEW_FIELDS
from scripts.render_m6_ocr_overlays import OverlayPreparationError, prepare_review_overlays


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _fixture(tmp_path):
    package = tmp_path / "package"
    (package / "images").mkdir(parents=True)
    rows, observations = [], []
    for variant, suffix, size in (
        ("square", "square", (1024, 1024)),
        ("feed_4x5_native", "native-4x5", (1080, 1350)),
    ):
        output = io.BytesIO()
        Image.new("RGB", size, (240, 240, 240)).save(output, "PNG")
        image_bytes = output.getvalue()
        name = f"images/sku-one-{suffix}.png"
        (package / name).write_bytes(image_bytes)
        rows.append({
            **dict.fromkeys(REVIEW_FIELDS, "UNVERIFIED"),
            "cohort_id": "m6-local-example",
            "sku_id": "sku-one",
            "poster_id": "b" * 32,
            "source_version": "1",
            "variant_id": variant,
            "output_image_path": name,
            "output_image_sha256": _sha(image_bytes),
            "source_product_sha256": "a" * 64,
            "request_sha256": "c" * 64,
            "spec_sha256": "d" * 64,
            "source_square_image_sha256": "e" * 64,
        })
        observations.append({
            "sku_id": "sku-one", "variant_id": variant, "poster_id": "b" * 32,
            "image_sha256": _sha(image_bytes),
            "recognized_line_count": 2,
            "recognized_lines": [
                {"text": "¥129", "confidence": 0.96, "bbox": [65, 668, 230, 725]},
                {"text": "Title", "confidence": 0.91, "bbox": None},
            ],
            "status": "CAPTURED_REVIEW_REQUIRED",
        })
    with (package / "m6_output_reviews.csv").open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=REVIEW_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    evidence = {
        "schema_version": "m6-paddle-ocr-evidence-1",
        "cohort_id": "m6-local-example",
        "reviews_csv_sha256": _sha((package / "m6_output_reviews.csv").read_bytes()),
        "sku_count": 1, "expected_variant_count": 2, "captured_variant_count": 2,
        "real_ocr_inference_attempted": True,  # Fixture declaration, not a real OCR run.
        "paddleocr_version": "3.3.0", "paddlepaddle_version": "3.2.0",
        "model_digests": {
            "detection": {"sha256": "f" * 64, "file_count": 1, "total_bytes": 4},
            "recognition": {"sha256": "e" * 64, "file_count": 1, "total_bytes": 4},
        },
        "captured_at_utc": "2026-10-26T00:00:00+00:00",
        "elapsed_seconds": 1.1, "human_review_performed": False,
        "automatically_accepted": False, "source_authorizations_verified": False,
        "min_20_real_skus_proven": False, "observations": observations,
        "positioned_line_count": 2, "unlocated_line_count": 2,
    }
    (package / "m6_paddle_ocr_evidence.json").write_text(
        json.dumps(evidence), encoding="utf-8",
    )
    return package, rows, evidence


def test_bounded_overlay_zip_is_non_destructive_and_review_only(tmp_path):
    package, rows, evidence = _fixture(tmp_path)
    before = {
        row["output_image_path"]: (package / row["output_image_path"]).read_bytes()
        for row in rows
    }
    summary = prepare_review_overlays(package)
    archive_path = package / "m6_ocr_review_overlays.zip"
    assert archive_path.exists()
    assert summary["automatically_accepted"] is False
    assert summary["runtime_attested"] is False
    assert summary["quality_verified"] is False
    assert summary["overlay_count"] == 2
    assert summary["positioned_line_count"] == 2
    assert summary["unlocated_line_count"] == 2
    assert summary["ocr_capture_sha256"] == _sha(
        (package / "m6_paddle_ocr_evidence.json").read_bytes()
    )
    with zipfile.ZipFile(archive_path) as zf:
        assert set(zf.namelist()) == {
            "m6_ocr_overlay_manifest.json",
            "images/sku-one-square.png",
            "images/sku-one-native-4x5.png",
        }
        manifest = json.loads(zf.read("m6_ocr_overlay_manifest.json"))
        assert manifest == summary
        for row in rows:
            output = zf.read(row["output_image_path"])
            with Image.open(io.BytesIO(output)) as rendered:
                assert rendered.size == ((1024, 1024) if row["variant_id"] == "square"
                                        else (1080, 1350))
            assert output != before[row["output_image_path"]]
            assert (package / row["output_image_path"]).read_bytes() == before[
                row["output_image_path"]
            ]
    assert not list(package.glob(".m6-overlays-*.tmp"))


def test_refuses_overwrite_and_preserves_existing_archive(tmp_path):
    package, _, _ = _fixture(tmp_path)
    target = package / "m6_ocr_review_overlays.zip"
    target.write_bytes(b"reviewer-owned zip")
    with pytest.raises(OverlayPreparationError, match="已存在"):
        prepare_review_overlays(package)
    assert target.read_bytes() == b"reviewer-owned zip"


@pytest.mark.parametrize("mutation", ("image", "reviews", "capture", "symlink"))
def test_blocks_changed_inputs_and_never_publishes_partial_archive(tmp_path, mutation):
    package, rows, evidence = _fixture(tmp_path)
    if mutation == "image":
        (package / rows[0]["output_image_path"]).write_bytes(b"tampered")
    elif mutation == "reviews":
        path = package / "m6_output_reviews.csv"
        path.write_bytes(path.read_bytes() + b"new label")
    elif mutation == "capture":
        evidence["observations"][0]["status"] = "PASS"
        (package / "m6_paddle_ocr_evidence.json").write_text(
            json.dumps(evidence), encoding="utf-8",
        )
    else:
        source = package / rows[0]["output_image_path"]
        outside = tmp_path / "outside.png"
        outside.write_bytes(source.read_bytes())
        source.unlink()
        source.symlink_to(outside)
    with pytest.raises(OverlayPreparationError):
        prepare_review_overlays(package)
    assert not (package / "m6_ocr_review_overlays.zip").exists()
    assert not list(package.glob(".m6-overlays-*.tmp"))


def test_release_is_race_safe_when_another_writer_wins(tmp_path, monkeypatch):
    import os

    package, _, _ = _fixture(tmp_path)
    existing = package / "m6_ocr_review_overlays.zip"
    original_link = os.link

    def racing_link(src, dst, *, follow_symlinks=True):
        existing.write_bytes(b"winner")
        return original_link(src, dst, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(os, "link", racing_link)
    with pytest.raises(OverlayPreparationError, match="已存在"):
        prepare_review_overlays(package)
    assert existing.read_bytes() == b"winner"
    assert not list(package.glob(".m6-overlays-*.tmp"))
