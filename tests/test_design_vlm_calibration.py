"""Operator-only shadow VLM calibration: complete denominators and fail-closed joins."""

import csv
import hashlib
import json
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from scripts.evaluate_design_vlm_shadow import (
    CalibrationError,
    LABEL_FIELDS,
    VISUAL_CATEGORIES,
    evaluate_shadow_cohort,
    main,
)

MODEL_SHA = "d" * 64
SVG_SHA = "a" * 64
PNG_SHA = "b" * 64


def _finding(category="contrast"):
    return {
        "finding_id": "visual-1",
        "category": category,
        "severity": "minor",
        "confidence": 0.75,
        "explanation": "Synthetic visual label",
        "suggested_action": "REVIEW_STYLE",
        "region": None,
    }


def _fixture(tmp_path, count=20):
    db = tmp_path / "designs.sqlite3"
    with sqlite3.connect(db) as connection:
        connection.execute(
            """
            CREATE TABLE design_vlm_runs (
                operation_id TEXT PRIMARY KEY, document_id TEXT, revision INTEGER,
                approval_id TEXT, svg_sha256 TEXT, png_sha256 TEXT,
                model_profile_sha256 TEXT, model_name TEXT, status TEXT,
                findings_json TEXT, runtime_json TEXT, started_at TEXT,
                finished_at TEXT, principal_id TEXT
            )
            """
        )
        labels = []
        for i in range(count):
            row = {
                "sku_id": f"private-sku-{i:02d}",
                "operation_id": f"vlm_{i+1:032x}",
                "document_id": f"doc-{i:02d}",
                "revision": "1",
                "approval_id": f"approval-{i}",
                "svg_sha256": SVG_SHA,
                "png_sha256": PNG_SHA,
                "model_profile_sha256": MODEL_SHA,
                "reviewer_alias": "reviewer-local",
                "source_authorization": "DECLARED_AUTHORIZED",
                **{category: "ABSENT" for category in VISUAL_CATEGORIES},
            }
            if i % 2 == 0:
                row["contrast"] = "PRESENT"
            labels.append(row)
            predicted = i % 2 == 0 or i in {1, 3}
            findings = [_finding()] if predicted else []
            started = datetime(2026, 11, 15, tzinfo=UTC) + timedelta(seconds=i)
            finished = started + timedelta(milliseconds=500 + i)
            connection.execute(
                """
                INSERT INTO design_vlm_runs (
                    operation_id, document_id, revision, approval_id,
                    svg_sha256, png_sha256, model_profile_sha256, model_name,
                    status, findings_json, runtime_json, started_at,
                    finished_at, principal_id
                ) VALUES (?, ?, 1, ?, ?, ?, ?, ?, 'EXECUTED', ?, ?, ?, ?, ?)
                """,
                (
                    row["operation_id"], row["document_id"], row["approval_id"],
                    SVG_SHA, PNG_SHA, MODEL_SHA, "llava:latest",
                    json.dumps(findings), json.dumps(
                        {
                            "source": "local_ollama",
                            "attested": False,
                            "execution_evidence": "OPERATOR_LOCAL_NOT_ATTESTED",
                        }
                    ), started.isoformat(), finished.isoformat(), "model-operator",
                ),
            )
    labels_file = tmp_path / "labels.csv"
    _write_labels(labels_file, labels)
    return db, labels_file, labels


def _write_labels(path, rows):
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=LABEL_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def _eval(db, labels):
    return evaluate_shadow_cohort(db, labels)


def test_twenty_distinct_labeled_skus_are_only_operator_calibration(tmp_path):
    db, labels, _ = _fixture(tmp_path)
    report = _eval(db, labels)
    assert report["status"] == "OPERATOR_CALIBRATION_ONLY_NOT_ATTESTED"
    assert report["sku_count"] == 20
    assert report["complete_case_count"] == 20
    assert report["minimum_complete_skus_met"] is True
    assert report["journal_status_counts"] == {"EXECUTED": 20, "UNKNOWN": 0, "EXECUTING": 0}
    contrast = report["by_category"]["contrast"]
    assert (contrast["tp"], contrast["fp"], contrast["fn"], contrast["tn"]) == (10, 2, 0, 8)
    assert contrast["precision"] == pytest.approx(10 / 12)
    assert contrast["recall"] == 1.0
    assert contrast["false_positive_rate"] == 0.2
    assert report["by_category"]["spacing"]["precision"] is None
    assert report["by_category"]["spacing"]["recall"] is None
    assert report["wall_elapsed_ms"]["p50"] == 509
    assert report["wall_elapsed_ms"]["p95"] == 518
    assert report["model_execution_attested"] is False
    assert report["source_authorizations_verified"] is False
    assert report["independent_human_review_attested"] is False
    assert report["quality_verified"] is False
    assert report["repair_authorized"] is False
    assert report["release_ready"] is False
    assert len(report["input_sha256"]["labels_csv"]) == 64
    assert len(report["input_sha256"]["journal_rows"]) == 64
    data = json.dumps(report)
    assert "private-sku-" not in data
    assert "reviewer-local" not in data
    assert "Synthetic visual label" not in data
    assert _eval(db, labels) == report


def test_small_cohort_never_becomes_acceptance_evidence(tmp_path):
    db, labels, _ = _fixture(tmp_path, count=19)
    report = _eval(db, labels)
    assert report["status"] == "INSUFFICIENT_OPERATOR_CALIBRATION"
    assert report["minimum_complete_skus_met"] is False
    assert report["complete_case_count"] == 19
    assert report["release_ready"] is False


def test_unknown_run_is_not_misclassified_as_no_finding_or_false_negative(tmp_path):
    db, labels, rows = _fixture(tmp_path)
    with sqlite3.connect(db) as connection:
        connection.execute(
            """
            UPDATE design_vlm_runs
            SET status='UNKNOWN', findings_json=NULL, runtime_json=NULL,
                finished_at=NULL
            WHERE operation_id=?
            """,
            (rows[0]["operation_id"],),
        )
    report = _eval(db, labels)
    assert report["status"] == "INSUFFICIENT_OPERATOR_CALIBRATION"
    assert report["journal_status_counts"]["UNKNOWN"] == 1
    assert report["complete_case_count"] == 19
    c = report["by_category"]["contrast"]
    assert c["fn"] == 0
    assert c["not_executed_labels"] == 1
    assert c["tp"] == 9


def test_unverified_label_never_enters_denominator(tmp_path):
    db, labels, rows = _fixture(tmp_path)
    rows[0]["contrast"] = "UNVERIFIED"
    _write_labels(labels, rows)
    report = _eval(db, labels)
    assert report["status"] == "INSUFFICIENT_OPERATOR_CALIBRATION"
    assert report["by_category"]["contrast"]["unverified_human_labels"] == 1
    assert report["by_category"]["contrast"]["tp"] == 9
    assert report["by_category"]["contrast"]["fn"] == 0
    assert report["complete_case_count"] == 19


@pytest.mark.parametrize("mutation", ["duplicate_sku", "duplicate_operation", "wrong_hash", "other_model", "missing_run", "unapproved"])
def test_rejects_selection_collisions_cross_model_or_unbound_inputs(tmp_path, mutation):
    db, labels, rows = _fixture(tmp_path)
    if mutation == "duplicate_sku":
        rows[1]["sku_id"] = rows[0]["sku_id"]
    elif mutation == "duplicate_operation":
        rows[1]["operation_id"] = rows[0]["operation_id"]
    elif mutation == "wrong_hash":
        rows[1]["png_sha256"] = "c" * 64
    elif mutation == "other_model":
        rows[1]["model_profile_sha256"] = "e" * 64
    elif mutation == "missing_run":
        rows[1]["operation_id"] = "vlm_" + "f" * 32
    else:
        rows[1]["source_authorization"] = "NO"
    _write_labels(labels, rows)
    with pytest.raises(CalibrationError):
        _eval(db, labels)


@pytest.mark.parametrize("malformed", ["bad_json", "illegal_category", "bad_runtime", "bad_timing", "inconsistent_status"])
def test_invalid_journal_is_not_scored(tmp_path, malformed):
    db, labels, rows = _fixture(tmp_path)
    fields = {
        "bad_json": ("findings_json", '{"findings":'),
        "illegal_category": ("findings_json", json.dumps([_finding("price")])),
        "bad_runtime": ("runtime_json", '{"attested":true}'),
        "bad_timing": ("finished_at", "2026-11-14T00:00:00+00:00"),
        "inconsistent_status": ("status", "FAILED"),
    }
    col, value = fields[malformed]
    with sqlite3.connect(db) as connection:
        connection.execute(
            f"UPDATE design_vlm_runs SET {col}=? WHERE operation_id=?",
            (value, rows[0]["operation_id"]),
        )
    with pytest.raises(CalibrationError):
        _eval(db, labels)


def test_read_only_cli_no_model_calls_no_db_mutation_and_no_identifiers(tmp_path, capsys):
    db, labels, _ = _fixture(tmp_path)
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    assert main(["--db", str(db), "--labels", str(labels)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["sku_count"] == 20
    assert payload["release_ready"] is False
    assert before == hashlib.sha256(db.read_bytes()).hexdigest()


def test_reject_extra_csv_columns_and_forged_absence_of_labels(tmp_path):
    db, labels, rows = _fixture(tmp_path)
    rows[0]["contrast"] = ""
    _write_labels(labels, rows)
    with pytest.raises(CalibrationError):
        _eval(db, labels)
    rows[0]["contrast"] = "ABSENT"
    with labels.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=[*LABEL_FIELDS, "release_ready"])
        writer.writeheader()
        for row in rows:
            writer.writerow({**row, "release_ready": "true"})
    with pytest.raises(CalibrationError):
        _eval(db, labels)
