"""Offline u2netp SKU cohort: synthetic inference boundary, real audit/recovery.

The local rembg process is stubbed here. A real Mac/ONNX trial requires separate
operator execution and 20 authorized, distinct products.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json

import pytest
from PIL import Image, ImageDraw

from app import cutout_service
from scripts.local_cutout_cohort import (
    CutoutCohortError,
    prepare_cohort,
    verify_cohort,
)
from scripts.real_sku_batch import preflight
from scripts.validate_m6_cohort import REGISTRY_FIELDS


def _png(*, alpha=False, tone=100):
    image = Image.new("RGBA", (200, 220), (240, 240, 240, 255))
    ImageDraw.Draw(image).rectangle((42, 40, 158, 182), fill=(tone, 130, 210, 255))
    if alpha:
        for y in range(image.height):
            for x in range(image.width):
                if x < 42 or x > 158 or y < 40 or y > 182:
                    image.putpixel((x, y), (0, 0, 0, 0))
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


def _inputs(tmp_path, *, n=2, unauthorized=None):
    source = tmp_path / "input"
    source.mkdir()
    manifest = source / "source.csv"
    registry = source / "authorization.csv"
    with manifest.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=("sku_id", "product_image", "title", "price"))
        writer.writeheader()
        for i in range(n):
            sku = f"sku-{i:02d}"
            image = source / f"{sku}.png"
            image.write_bytes(_png(tone=50 + i))
            writer.writerow(
                {
                    "sku_id": sku,
                    "product_image": image.name,
                    "title": f"商品 {i}",
                    "price": str(100 + i),
                }
            )
    with registry.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=REGISTRY_FIELDS)
        writer.writeheader()
        for i in range(n):
            sku = f"sku-{i:02d}"
            row = {field: "" for field in REGISTRY_FIELDS}
            row.update(
                {
                    "cohort_id": "m6-trial-pending",
                    "sku_id": sku,
                    "source_asset_authorization_status": "PENDING"
                    if sku == unauthorized
                    else "AUTHORIZED",
                    "authorization_evidence_ref": "license-internal-001",
                    "logo_authorization_status": "NOT_APPLICABLE",
                    "source_image_sha256": hashlib.sha256(
                        (source / f"{sku}.png").read_bytes()
                    ).hexdigest(),
                    "distinct_sku_verified": "YES",
                    "duplicate_source_image_reviewed": "YES",
                    "reviewer_alias": "internal-operator",
                    "product_category": "synthetic",
                    "product_family_id": sku,
                    "variant_or_packaging_condition": "synthetic-png",
                }
            )
            writer.writerow(row)
    return manifest, registry


def _model(tmp_path, monkeypatch):
    home = tmp_path / "models"
    model = home / "models" / "u2netp" / "u2netp.onnx"
    model.parent.mkdir(parents=True)
    model.write_bytes(b"fake-onnx-only-for-test")
    monkeypatch.setenv("POSTER_CUTOUT_REMBG_HOME", str(home))
    monkeypatch.setenv("POSTER_CUTOUT_MODEL_SHA256", hashlib.sha256(model.read_bytes()).hexdigest())
    monkeypatch.setattr(cutout_service.shutil, "which", lambda _: "/usr/local/bin/rembg")
    return hashlib.sha256(model.read_bytes()).hexdigest()


def test_plan_is_read_only_and_does_not_require_model(tmp_path, monkeypatch):
    manifest, registry = _inputs(tmp_path)
    out = tmp_path / "trial"
    monkeypatch.delenv("POSTER_CUTOUT_REMBG_HOME", raising=False)
    plan = prepare_cohort(manifest, registry, out, min_skus=2)
    assert plan["mode"] == "check"
    assert plan["cohort_sku_count"] == 2
    assert plan["authorized_sku_count"] == 2
    assert plan["model_runtime_attested"] is False
    assert not out.exists()


def test_authorization_and_minimum_sku_gate_before_any_write(tmp_path, monkeypatch):
    manifest, registry = _inputs(tmp_path, unauthorized="sku-01")
    out = tmp_path / "trial"
    with pytest.raises(CutoutCohortError, match="授权"):
        prepare_cohort(manifest, registry, out, apply=True, min_skus=2)
    assert not out.exists()
    with pytest.raises(CutoutCohortError, match="20"):
        prepare_cohort(manifest, registry, out, min_skus=20)


def test_real_upload_pipeline_and_pinned_model_without_fallback(tmp_path, monkeypatch):
    manifest, registry = _inputs(tmp_path)
    out = tmp_path / "trial"
    digest = _model(tmp_path, monkeypatch)
    calls = []

    def synthetic_cutout(original):
        calls.append(original.size)
        return _png(alpha=True, tone=50 + len(calls) - 1)

    monkeypatch.setattr("scripts.local_cutout_cohort.local_preview", synthetic_cutout)
    done = prepare_cohort(manifest, registry, out, apply=True, min_skus=2)
    assert done["status"] == "COMPLETED"
    assert done["completed_sku_count"] == 2
    assert calls == [(200, 220)] * 2
    assert done["model_sha256"] == digest
    assert done["model_runtime_attested"] is False
    assert done["real_model_quality_verified"] is False
    copy = out / "manifest_with_cutouts.csv"
    _, prepared = preflight(copy)
    assert len(prepared) == 2
    assert all(row["cutout"] is not None for row in prepared)
    assert all(row["data"]["product_source_mode"] == "cutout" for row in prepared)
    assert (out / "source" / "sku-00.png").read_bytes() == (
        manifest.parent / "sku-00.png"
    ).read_bytes()
    report = verify_cohort(out, manifest, registry, min_skus=2)
    assert report["status"] == "RECORDED_UNATTESTED"
    assert report["completed_sku_count"] == 2
    assert report["model_sha256"] == digest
    assert report["model_runtime_attested"] is False
    # Re-applying completed identical cohort must not execute inference.
    again = prepare_cohort(manifest, registry, out, apply=True, min_skus=2)
    assert again["status"] == "COMPLETED"
    assert len(calls) == 2


def test_partial_failure_requires_explicit_retry_without_repeating_done_sku(
    tmp_path,
    monkeypatch,
):
    manifest, registry = _inputs(tmp_path)
    out = tmp_path / "trial"
    _model(tmp_path, monkeypatch)
    calls = []

    def fail_second(original):
        calls.append(True)
        if len(calls) == 2:
            raise cutout_service.CutoutExecutionError("synthetic inference error")
        return _png(alpha=True, tone=50 + len(calls) - 1)

    monkeypatch.setattr("scripts.local_cutout_cohort.local_preview", fail_second)
    with pytest.raises(CutoutCohortError, match="sku-01"):
        prepare_cohort(manifest, registry, out, apply=True, min_skus=2)
    state = json.loads((out / "cutout_trial.json").read_text())
    assert state["status"] == "INCOMPLETE"
    assert state["rows"]["sku-00"]["status"] == "COMPLETED"
    assert state["rows"]["sku-01"]["status"] == "FAILED"
    assert not (out / "manifest_with_cutouts.csv").exists()
    with pytest.raises(CutoutCohortError, match="retry"):
        prepare_cohort(manifest, registry, out, apply=True, min_skus=2)
    assert len(calls) == 2
    monkeypatch.setattr(
        "scripts.local_cutout_cohort.local_preview", lambda original: _png(alpha=True, tone=51)
    )
    done = prepare_cohort(manifest, registry, out, apply=True, retry_failed=True, min_skus=2)
    assert done["status"] == "COMPLETED"
    assert done["completed_sku_count"] == 2
    assert verify_cohort(out, manifest, registry, min_skus=2)["status"] == "RECORDED_UNATTESTED"


def test_mutated_output_or_registry_is_rejected_without_reinference(
    tmp_path,
    monkeypatch,
):
    manifest, registry = _inputs(tmp_path)
    out = tmp_path / "trial"
    _model(tmp_path, monkeypatch)
    monkeypatch.setattr("scripts.local_cutout_cohort.local_preview", lambda orig: _png(alpha=True))
    prepare_cohort(manifest, registry, out, apply=True, min_skus=2)
    png = out / "cutouts" / "sku-01.png"
    png.write_bytes(b"bad")
    with pytest.raises(CutoutCohortError, match="摘要|文件"):
        verify_cohort(out, manifest, registry, min_skus=2)
    with pytest.raises(CutoutCohortError):
        prepare_cohort(manifest, registry, out, apply=True, min_skus=2)
    assert not (out / "manifest_with_cutouts.csv").is_symlink()


def test_changed_authorization_or_model_hash_cannot_resume(tmp_path, monkeypatch):
    manifest, registry = _inputs(tmp_path)
    out = tmp_path / "trial"
    _model(tmp_path, monkeypatch)
    monkeypatch.setattr("scripts.local_cutout_cohort.local_preview", lambda orig: _png(alpha=True))
    prepare_cohort(manifest, registry, out, apply=True, min_skus=2)
    monkeypatch.setenv("POSTER_CUTOUT_MODEL_SHA256", "f" * 64)
    with pytest.raises(CutoutCohortError):
        prepare_cohort(manifest, registry, out, apply=True, min_skus=2)
    monkeypatch.setenv(
        "POSTER_CUTOUT_MODEL_SHA256",
        json.loads((out / "cutout_trial.json").read_text())["model_sha256"],
    )
    registry.write_text(registry.read_text() + "\n", encoding="utf-8")
    with pytest.raises(CutoutCohortError, match="授权|清单|摘要"):
        prepare_cohort(manifest, registry, out, apply=True, min_skus=2)


def test_verifier_is_read_only_when_export_manifest_missing(tmp_path, monkeypatch):
    manifest, registry = _inputs(tmp_path)
    out = tmp_path / "trial"
    _model(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "scripts.local_cutout_cohort.local_preview", lambda original: _png(alpha=True)
    )
    prepare_cohort(manifest, registry, out, apply=True, min_skus=2)
    exported = out / "manifest_with_cutouts.csv"
    exported.unlink()
    with pytest.raises(CutoutCohortError, match="输出清单"):
        verify_cohort(out, manifest, registry, min_skus=2)
    assert not exported.exists()


def test_local_cli_overrides_legacy_u2net_home(tmp_path, monkeypatch):
    """An inherited U2NET_HOME cannot silently redirect to another model."""
    from subprocess import CompletedProcess

    _model(tmp_path, monkeypatch)
    monkeypatch.setenv("U2NET_HOME", str(tmp_path / "attacker-models"))
    observed = []

    def stub(argv, *, env, timeout, capture_output, check):
        observed.append(env)
        from pathlib import Path

        Path(argv[-1]).write_bytes(_png(alpha=True))
        return CompletedProcess(argv, 0, b"", b"")

    monkeypatch.setattr(cutout_service.subprocess, "run", stub)
    with Image.open(io.BytesIO(_png())) as original:
        result = cutout_service.local_preview(original)
    assert result.startswith(bytes([137, 80, 78, 71]))
    assert "U2NET_HOME" not in observed[0]
    assert observed[0]["REMBG_HOME"] == str(tmp_path / "models")


def test_reapply_rejects_altered_copied_source_without_model_call(tmp_path, monkeypatch):
    manifest, registry = _inputs(tmp_path)
    out = tmp_path / "trial"
    _model(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "scripts.local_cutout_cohort.local_preview", lambda original: _png(alpha=True)
    )
    prepare_cohort(manifest, registry, out, apply=True, min_skus=2)
    copied = out / "source" / "sku-00.png"
    copied.write_bytes(_png(tone=215))
    monkeypatch.setattr(
        "scripts.local_cutout_cohort.local_preview",
        lambda original: (_ for _ in ()).throw(
            AssertionError("Already completed SKU was re-inferred")
        ),
    )
    with pytest.raises(CutoutCohortError, match="源图|源素材|文件"):
        prepare_cohort(manifest, registry, out, apply=True, min_skus=2)


def _full_lineage(tmp_path, monkeypatch, *, candidate_tone=50):
    from scripts.prepare_m6_review import prepare_package
    from scripts.real_sku_batch import run_batch

    manifest, registry = _inputs(tmp_path)
    trial = tmp_path / "trial"
    _model(tmp_path, monkeypatch)
    monkeypatch.setenv("POSTER_DATA_DIR", str(tmp_path / "posters"))
    monkeypatch.setenv("POSTER_EXECUTION_DB", str(tmp_path / "execution.sqlite3"))
    tones = [candidate_tone, candidate_tone + 1]
    attempts = []

    def local_fake(image):
        attempts.append(True)
        return _png(alpha=True, tone=tones[len(attempts) - 1])

    monkeypatch.setattr("scripts.local_cutout_cohort.local_preview", local_fake)
    prepare_cohort(manifest, registry, trial, apply=True, min_skus=2)
    m5g = tmp_path / "m5g"
    run_batch(trial / "manifest_with_cutouts.csv", m5g, apply=True)
    journal = m5g / "batch_import_results.json"
    package = m5g / "m6_review_package"
    prepare_package(journal, package, apply=True)
    return manifest, registry, trial, journal, package


def test_m14_m5g_m6_lineage_is_read_only_and_review_stays_pending(
    tmp_path,
    monkeypatch,
):
    from scripts.verify_m14_lineage import verify_lineage

    manifest, registry, trial, journal, package = _full_lineage(tmp_path, monkeypatch)
    before = {
        str(path): hashlib.sha256(path.read_bytes()).hexdigest()
        for folder in (trial, journal.parent, package)
        for path in folder.glob("*.json")
    }
    result = verify_lineage(trial, manifest, registry, journal, package, min_skus=2)
    assert result["status"] == "LINEAGE_VERIFIED_REVIEW_PENDING"
    assert result["source_cohort_sku_count"] == 2
    assert result["linked_sku_count"] == 2
    assert result["linked_versions"] == 2
    assert result["model_runtime_attested"] is False
    assert result["quality_accepted"] is False
    assert result["independent_review_attested"] is False
    assert len(result["items"]) == 2
    assert all(
        item["source_matched"] and item["cutout_matched"] and item["m6_review_images_matched"]
        for item in result["items"]
    )
    after = {
        str(path): hashlib.sha256(path.read_bytes()).hexdigest()
        for folder in (trial, journal.parent, package)
        for path in folder.glob("*.json")
    }
    assert before == after


def test_m14_bridge_rejects_two_individually_valid_but_unrelated_trials(
    tmp_path,
    monkeypatch,
):
    from scripts.verify_m14_lineage import LineageError, verify_lineage

    manifest, registry, trial, journal, package = _full_lineage(tmp_path, monkeypatch)
    other_trial = tmp_path / "second-trial"
    tones = [95, 96]
    state = []

    def other_fake(image):
        state.append(True)
        return _png(alpha=True, tone=tones[len(state) - 1])

    monkeypatch.setattr("scripts.local_cutout_cohort.local_preview", other_fake)
    prepare_cohort(manifest, registry, other_trial, apply=True, min_skus=2)
    assert (
        verify_cohort(other_trial, manifest, registry, min_skus=2)["status"]
        == "RECORDED_UNATTESTED"
    )
    with pytest.raises(LineageError, match="清单|抠图|sha|SHA|来源"):
        verify_lineage(other_trial, manifest, registry, journal, package, min_skus=2)
    # Normal lineage still succeeds.
    assert (
        verify_lineage(trial, manifest, registry, journal, package, min_skus=2)["linked_sku_count"]
        == 2
    )


def test_m14_bridge_rejects_m6_review_image_tampering(tmp_path, monkeypatch):
    from scripts.verify_m14_lineage import LineageError, verify_lineage

    manifest, registry, trial, journal, package = _full_lineage(tmp_path, monkeypatch)
    image = package / "images" / "sku-00-square.png"
    image.write_bytes(b"tampered")
    with pytest.raises(LineageError, match="M6|图片|输出"):
        verify_lineage(trial, manifest, registry, journal, package, min_skus=2)


def test_m14_bridge_honors_default_twenty_distinct_sku_gate(tmp_path, monkeypatch):
    from scripts.verify_m14_lineage import LineageError, verify_lineage

    manifest, registry, trial, journal, package = _full_lineage(tmp_path, monkeypatch)
    with pytest.raises(LineageError, match="20"):
        verify_lineage(trial, manifest, registry, journal, package)


def test_m14_bridge_rejects_review_row_spec_or_square_source_rebinding(
    tmp_path,
    monkeypatch,
):
    from scripts.verify_m14_lineage import LineageError, verify_lineage

    manifest, registry, trial, journal, package = _full_lineage(tmp_path, monkeypatch)
    path = package / "m6_output_reviews.csv"
    original = path.read_bytes()
    for field in ("spec_sha256", "source_square_image_sha256"):
        with path.open("r", newline="", encoding="utf-8") as source:
            rows = list(csv.DictReader(source))
        rows[0][field] = "f" * 64
        with path.open("w", newline="", encoding="utf-8") as target:
            writer = csv.DictWriter(target, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)
        with pytest.raises(LineageError, match="M6|规格|来源"):
            verify_lineage(trial, manifest, registry, journal, package, min_skus=2)
        path.write_bytes(original)
