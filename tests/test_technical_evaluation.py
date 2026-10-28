"""Technical runs need images, not fabricated authorization or human approvals."""

import csv
import json

import pytest
from PIL import Image, ImageDraw

from scripts.local_cutout_cohort import CutoutCohortError, prepare_cohort, verify_cohort
from scripts.technical_evaluation import evaluate_directory, prepare_inputs


def images(tmp_path, count=2):
    source = tmp_path / "pictures"
    source.mkdir()
    for i in range(count):
        Image.new("RGB", (200, 240), (80 + i, 120, 190)).save(source / f"商品{i}.png")
    return source


def test_directory_scan_creates_unverified_registry_without_touching_sources(tmp_path):
    source = images(tmp_path)
    original = {p.name: p.read_bytes() for p in source.iterdir()}
    out = tmp_path / "inputs"
    prepared = prepare_inputs(source, out, price="199", min_images=2)
    assert prepared["image_count"] == 2
    rows = list(csv.DictReader((out / "sku_registry.csv").open()))
    assert all(r["source_asset_authorization_status"] == "UNVERIFIED" for r in rows)
    assert all(
        r["reviewer_alias"] == "" and r["distinct_sku_verified"] == "UNVERIFIED" for r in rows
    )
    assert {p.name: p.read_bytes() for p in source.iterdir()} == original
    with pytest.raises(ValueError):
        prepare_inputs(source, out, price="199", min_images=2)


def test_technical_cutout_plan_does_not_need_registry_or_reviewer(tmp_path):
    source = images(tmp_path)
    out = tmp_path / "inputs"
    prepare_inputs(source, out, price="199", min_images=2)
    plan = prepare_cohort(
        out / "manifest.csv", None, tmp_path / "trial", min_skus=2, purpose="technical_evaluation"
    )
    assert plan["authorization_status"] == "UNVERIFIED"
    assert plan["authorized_sku_count"] == 0
    assert plan["status"] == "TECHNICAL_READY"
    assert not (tmp_path / "trial").exists()
    with pytest.raises(CutoutCohortError):
        prepare_cohort(
            out / "manifest.csv",
            out / "sku_registry.csv",
            tmp_path / "strict",
            min_skus=2,
            purpose="quality_acceptance",
        )


def test_directory_generation_and_two_format_checks_need_no_license(tmp_path):
    source = images(tmp_path)
    result = evaluate_directory(
        source, tmp_path / "evaluation", price="199", apply=True, min_images=2
    )
    assert result["generated_sku_count"] == 2
    assert result["expected_output_count"] == 4
    assert result["valid_output_count"] == 4
    assert result["authorization_status"] == result["human_visual_review"] == "UNVERIFIED"
    assert result["ocr_status"] == "NOT_RUN"
    assert result["quality_accepted"] is False
    assert result["paid_model_calls"] == 0
    registry = list(csv.DictReader((tmp_path / "evaluation/inputs/sku_registry.csv").open()))
    assert len(registry) == 2
    report = json.loads((tmp_path / "evaluation/technical_report.json").read_text())
    assert report == result


def test_dry_run_invalid_images_and_output_inside_source_do_not_write(tmp_path):
    source = images(tmp_path)
    out = tmp_path / "evaluation"
    plan = evaluate_directory(source, out, price="199", min_images=2)
    assert plan["mode"] == "check" and not out.exists()
    with pytest.raises(ValueError):
        evaluate_directory(source, source / "output", price="199", apply=True, min_images=2)
    (source / "bad.png").write_bytes(b"invalid")
    with pytest.raises(ValueError):
        evaluate_directory(source, out, price="199", apply=True, min_images=2)
    assert not out.exists()


def test_technical_inference_resumes_and_later_authorization_is_independent(tmp_path, monkeypatch):
    source = images(tmp_path)
    inputs = tmp_path / "inputs"
    prepare_inputs(source, inputs, price="199", min_images=2)
    manifest = inputs / "manifest.csv"
    trial = tmp_path / "trial"
    calls = []
    monkeypatch.setattr(
        "scripts.local_cutout_cohort.local_model_identity",
        lambda: {"model_sha256": "a" * 64, "model_id": "u2netp"},
    )

    def candidate(original):
        calls.append(True)
        alpha = Image.new("L", original.size, 0)
        ImageDraw.Draw(alpha).rectangle((40, 40, 160, 200), fill=255)
        original.putalpha(alpha)
        from io import BytesIO

        data = BytesIO()
        original.save(data, "PNG")
        return data.getvalue()

    monkeypatch.setattr("scripts.local_cutout_cohort.local_preview", candidate)
    for _ in range(2):
        result = prepare_cohort(
            manifest, None, trial, apply=True, min_skus=2, purpose="technical_evaluation"
        )
        assert result["authorization_status"] == "UNVERIFIED"
    assert len(calls) == 2
    assert (
        verify_cohort(trial, manifest, None, min_skus=2, purpose="technical_evaluation")["status"]
        == "RECORDED_UNATTESTED"
    )
    registry = list(csv.DictReader((trial / "sku_registry.csv").open()))
    for row in registry:
        row.update(
            source_asset_authorization_status="AUTHORIZED",
            authorization_evidence_ref="owner-record",
            distinct_sku_verified="YES",
            duplicate_source_image_reviewed="YES",
            reviewer_alias="operator",
        )
    authorized = tmp_path / "later-authorization.csv"
    with authorized.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=registry[0].keys())
        writer.writeheader()
        writer.writerows(registry)
    assert (
        verify_cohort(trial, manifest, authorized, min_skus=2)["authorization_status"]
        == "OPERATOR_DECLARED_AUTHORIZED"
    )
    assert len(calls) == 2  # metadata review never re-runs the image model


def test_symlinks_and_duplicate_images_do_not_create_fake_independent_skus(tmp_path):
    source = images(tmp_path)
    (source / "copy.png").write_bytes((source / "商品0.png").read_bytes())
    plan = evaluate_directory(source, tmp_path / "out", price="199", min_images=3)
    assert plan["image_count"] == 3 and plan["unique_source_sha256_count"] == 2
    assert plan["independent_sku_status"] == "UNVERIFIED"
    (source / "linked.png").symlink_to(source / "商品0.png")
    with pytest.raises(ValueError, match="符号链接"):
        evaluate_directory(source, tmp_path / "out", price="199", apply=True, min_images=3)
    assert not (tmp_path / "out").exists()
