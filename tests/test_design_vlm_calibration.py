"""Operator-only shadow VLM calibration: complete denominators and fail-closed joins."""

import csv
import hashlib
import json
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from scripts.evaluate_design_vlm_shadow import (
    LABEL_FIELDS,
    VISUAL_CATEGORIES,
    CalibrationError,
    evaluate_shadow_cohort,
    main,
)

MODEL_SHA = "d" * 64


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
                finished_at TEXT, reason TEXT, principal_id TEXT
            )
            """
        )
        labels = []
        for i in range(count):
            row = {
                "sku_id": f"private-sku-{i:02d}",
                "operation_id": f"vlm_{i + 1:032x}",
                "document_id": f"doc-{i:02d}",
                "revision": "1",
                "approval_id": f"approval-{i}",
                "svg_sha256": hashlib.sha256(f"synthetic-svg-{i}".encode()).hexdigest(),
                "png_sha256": hashlib.sha256(f"synthetic-png-{i}".encode()).hexdigest(),
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
                    row["operation_id"],
                    row["document_id"],
                    row["approval_id"],
                    row["svg_sha256"],
                    row["png_sha256"],
                    MODEL_SHA,
                    "llava:latest",
                    json.dumps(findings),
                    json.dumps(
                        {
                            "source": "local_ollama",
                            "attested": False,
                            "execution_evidence": "OPERATOR_LOCAL_NOT_ATTESTED",
                            "local_model_digest_claim": "sha256:" + "f" * 64,
                        }
                    ),
                    started.isoformat(),
                    finished.isoformat(),
                    "model-operator",
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
                reason='RECOVERED_AFTER_RESTART',
                finished_at='2026-11-15T00:00:01+00:00'
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


@pytest.mark.parametrize(
    "mutation",
    [
        "duplicate_sku",
        "duplicate_operation",
        "wrong_hash",
        "other_model",
        "missing_run",
        "unapproved",
    ],
)
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


@pytest.mark.parametrize(
    "malformed",
    ["bad_json", "illegal_category", "bad_runtime", "bad_timing", "inconsistent_status"],
)
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


@pytest.mark.parametrize(
    "mutation",
    [
        "extra_release_field",
        "duplicate_runtime_key",
        "missing_model_digest",
        "invalid_model_digest",
    ],
)
def test_calibration_rejects_mutated_runtime_provenance(tmp_path, mutation):
    db, labels, rows = _fixture(tmp_path)
    with sqlite3.connect(db) as connection:
        raw = connection.execute(
            "SELECT runtime_json FROM design_vlm_runs WHERE operation_id = ?",
            (rows[0]["operation_id"],),
        ).fetchone()[0]
        runtime = json.loads(raw)
        if mutation == "extra_release_field":
            runtime["release_ready"] = True
        elif mutation == "missing_model_digest":
            del runtime["local_model_digest_claim"]
        elif mutation == "invalid_model_digest":
            runtime["local_model_digest_claim"] = "sha256:not-a-digest"
        if mutation == "duplicate_runtime_key":
            encoded = raw[:-1] + ', "source": "local_ollama"}'
        else:
            encoded = json.dumps(runtime)
        connection.execute(
            "UPDATE design_vlm_runs SET runtime_json=? WHERE operation_id=?",
            (encoded, rows[0]["operation_id"]),
        )
    with pytest.raises(CalibrationError):
        _eval(db, labels)


def test_calibration_rejects_unresolved_record_with_stale_runtime(tmp_path):
    db, labels, rows = _fixture(tmp_path)
    with sqlite3.connect(db) as connection:
        connection.execute(
            "UPDATE design_vlm_runs SET status='UNKNOWN', findings_json=NULL,"
            " reason='RECOVERED_AFTER_RESTART', finished_at='2026-11-15T00:00:01+00:00'"
            " WHERE operation_id=?",
            (rows[0]["operation_id"],),
        )
    with pytest.raises(CalibrationError):
        _eval(db, labels)


def test_shadow_calibration_rejects_symlinked_label_csv(tmp_path):
    db, labels, _ = _fixture(tmp_path)
    frozen = tmp_path / "actual-frozen-labels.csv"
    labels.rename(frozen)
    labels.symlink_to(frozen)

    with pytest.raises(CalibrationError):
        evaluate_shadow_cohort(db, labels)


def test_shadow_calibration_checks_actual_read_limit_not_stale_stat(tmp_path, monkeypatch):
    from pathlib import Path
    from types import SimpleNamespace

    from scripts.evaluate_design_vlm_shadow import MAX_CSV_BYTES

    db, labels, _ = _fixture(tmp_path)
    # Blank lines are valid CSV whitespace, so a stale pre-read stat can
    # otherwise permit the full oversize file to be parsed and scored.
    with labels.open("ab") as stream:
        stream.write(b"\n" * (MAX_CSV_BYTES + 1))
    original_stat = Path.stat

    def stale_stat(self, *args, **kwargs):
        observed = original_stat(self, *args, **kwargs)
        if self == labels:
            return SimpleNamespace(st_mode=observed.st_mode, st_size=100)
        return observed

    monkeypatch.setattr(Path, "stat", stale_stat)
    with pytest.raises(CalibrationError, match="size|exceed|limit"):
        evaluate_shadow_cohort(db, labels)


@pytest.mark.parametrize(
    "mutation",
    [
        "executed_with_failure_reason",
        "unknown_missing_reason",
        "unknown_missing_finished_at",
        "unknown_non_monotonic_timestamps",
        "unknown_naive_started_at",
        "executing_with_stale_reason",
        "executing_with_stale_finished_at",
        "executing_missing_started_at",
    ],
)
def test_calibration_rejects_state_inconsistent_journal_metadata(tmp_path, mutation):
    db, labels, rows = _fixture(tmp_path)
    changes = {
        "executed_with_failure_reason": {
            "reason": "VLM_EXECUTION_AMBIGUOUS",
        },
        "unknown_missing_reason": {
            "status": "UNKNOWN",
            "findings_json": None,
            "runtime_json": None,
            "reason": None,
            "finished_at": "2026-11-15T00:00:01+00:00",
        },
        "unknown_missing_finished_at": {
            "status": "UNKNOWN",
            "findings_json": None,
            "runtime_json": None,
            "reason": "VLM_EXECUTION_AMBIGUOUS",
            "finished_at": None,
        },
        "unknown_non_monotonic_timestamps": {
            "status": "UNKNOWN",
            "findings_json": None,
            "runtime_json": None,
            "reason": "VLM_EXECUTION_AMBIGUOUS",
            "finished_at": "2026-11-14T23:59:59+00:00",
        },
        "unknown_naive_started_at": {
            "status": "UNKNOWN",
            "findings_json": None,
            "runtime_json": None,
            "reason": "VLM_EXECUTION_AMBIGUOUS",
            "started_at": "2026-11-15T00:00:00",
            "finished_at": "2026-11-15T00:00:01+00:00",
        },
        "executing_with_stale_reason": {
            "status": "EXECUTING",
            "findings_json": None,
            "runtime_json": None,
            "reason": "VLM_EXECUTION_AMBIGUOUS",
            "finished_at": None,
        },
        "executing_with_stale_finished_at": {
            "status": "EXECUTING",
            "findings_json": None,
            "runtime_json": None,
            "reason": None,
            "finished_at": "2026-11-15T00:00:01+00:00",
        },
        "executing_missing_started_at": {
            "status": "EXECUTING",
            "findings_json": None,
            "runtime_json": None,
            "reason": None,
            "started_at": None,
            "finished_at": None,
        },
    }[mutation]
    with sqlite3.connect(db) as connection:
        assignments = ", ".join(f"{column} = ?" for column in changes)
        connection.execute(
            f"UPDATE design_vlm_runs SET {assignments} WHERE operation_id = ?",
            (*changes.values(), rows[0]["operation_id"]),
        )

    with pytest.raises(CalibrationError):
        _eval(db, labels)


def test_zero_findings_is_explicitly_not_a_pass(tmp_path):
    db, labels, _ = _fixture(tmp_path)
    with sqlite3.connect(db) as connection:
        connection.execute("UPDATE design_vlm_runs SET findings_json='[]'")

    report = evaluate_shadow_cohort(db, labels)

    assert report["model_findings_count"] == 0
    assert report["signal_status"] == "NO_FINDINGS_NOT_A_PASS"
    assert report["zero_findings_not_a_pass"] is True
    assert report["quality_verified"] is False
    assert report["release_ready"] is False


def test_calibration_reports_unmetered_local_compute_cost_explicitly(tmp_path):
    db, labels, _ = _fixture(tmp_path)

    report = evaluate_shadow_cohort(db, labels)

    assert report["cost"]["status"] == "LOCAL_COMPUTE_COST_NOT_METERED"
    assert report["cost"]["resource_hourly_rate_usd"] is None
    assert report["cost"]["estimated_total_usd"] is None
    assert report["cost"]["estimated_per_executed_operation_usd"] is None
    assert report["cost"]["executed_operation_count"] == 20
    assert report["cost"]["scope"] == "EXECUTED_JOURNAL_WALL_TIME_ONLY"


def test_calibration_estimates_cost_from_operator_supplied_resource_rate(tmp_path):
    db, labels, _ = _fixture(tmp_path)

    report = evaluate_shadow_cohort(db, labels, resource_hourly_rate_usd="36")

    assert report["cost"]["status"] == "OPERATOR_RATE_ESTIMATE"
    assert report["cost"]["resource_hourly_rate_usd"] == "36"
    assert report["cost"]["estimated_total_usd"] == "0.101900"
    assert report["cost"]["estimated_per_executed_operation_usd"] == "0.005095"
    assert report["cost"]["executed_operation_count"] == 20
    assert report["cost"]["unresolved_operation_count"] == 0
    assert "not provider billing" in report["cost"]["limitations"]


@pytest.mark.parametrize("rate", ["-0.01", "NaN", "Infinity", "1000001"])
def test_calibration_rejects_invalid_operator_resource_rate(tmp_path, rate):
    db, labels, _ = _fixture(tmp_path)

    with pytest.raises(CalibrationError):
        evaluate_shadow_cohort(db, labels, resource_hourly_rate_usd=rate)


def test_no_executed_runs_are_not_reported_as_zero_model_findings(tmp_path):
    db, labels, _ = _fixture(tmp_path)
    with sqlite3.connect(db) as connection:
        connection.execute(
            """
            UPDATE design_vlm_runs
            SET status='UNKNOWN', findings_json=NULL, runtime_json=NULL,
                reason='RECOVERED_AFTER_RESTART',
                finished_at='2026-11-15T00:01:00+00:00'
            """
        )

    report = _eval(db, labels)

    assert report["journal_status_counts"]["EXECUTED"] == 0
    assert report["journal_status_counts"]["UNKNOWN"] == 20
    assert report["model_findings_count"] == 0
    assert report["signal_status"] == "NO_EXECUTED_MODEL_RUNS_NOT_A_PASS"
    assert report["zero_findings_not_a_pass"] is False
    assert report["quality_verified"] is False
    assert report["release_ready"] is False


def test_category_coverage_reports_missing_defects_and_missing_clean_examples(tmp_path):
    db, labels, rows = _fixture(tmp_path)
    rows[0]["contrast"] = "UNVERIFIED"
    for row in rows:
        row["spacing"] = "PRESENT"
        row["visual_hierarchy"] = "UNVERIFIED"
    _write_labels(labels, rows)
    with sqlite3.connect(db) as connection:
        connection.execute(
            "UPDATE design_vlm_runs SET findings_json='[]' WHERE operation_id=?",
            (rows[2]["operation_id"],),
        )

    report = _eval(db, labels)
    contrast = report["by_category"]["contrast"]
    assert contrast["positive_support"] == 9
    assert contrast["negative_support"] == 10
    assert contrast["false_negative_rate"] == pytest.approx(1 / 9, abs=1e-6)
    assert contrast["coverage_status"] == "BOTH_CLASSES_OBSERVED"
    spacing = report["by_category"]["spacing"]
    assert spacing["coverage_status"] == "NO_NEGATIVE_EXAMPLES"
    assert spacing["false_negative_rate"] == 1.0
    assert spacing["false_positive_rate"] is None
    visibility = report["by_category"]["product_visibility"]
    assert visibility["coverage_status"] == "NO_POSITIVE_EXAMPLES"
    assert visibility["false_negative_rate"] is None
    hierarchy = report["by_category"]["visual_hierarchy"]
    assert hierarchy["coverage_status"] == "NO_EVALUABLE_CASES"
    assert hierarchy["positive_support"] == hierarchy["negative_support"] == 0
    assert hierarchy["false_negative_rate"] is None
    assert report["micro"]["false_negative_rate"] == pytest.approx(21 / 29, abs=1e-6)
    assert report["quality_verified"] is False
    assert report["release_ready"] is False


def test_unresolved_operations_do_not_supply_category_coverage(tmp_path):
    db, labels, rows = _fixture(tmp_path, count=1)
    with sqlite3.connect(db) as connection:
        connection.execute(
            "UPDATE design_vlm_runs SET status='UNKNOWN', findings_json=NULL,"
            " runtime_json=NULL, reason='RECOVERED_AFTER_RESTART',"
            " finished_at='2026-11-15T00:00:01+00:00' WHERE operation_id=?",
            (rows[0]["operation_id"],),
        )

    report = _eval(db, labels)
    contrast = report["by_category"]["contrast"]
    assert contrast["positive_support"] == 0
    assert contrast["negative_support"] == 0
    assert contrast["coverage_status"] == "NO_EVALUABLE_CASES"
    assert contrast["false_negative_rate"] is None
    assert report["micro"]["false_negative_rate"] is None


@pytest.mark.parametrize(
    ("field", "error"),
    [
        ("png_sha256", "duplicate approved PNG"),
        ("svg_sha256", "duplicate approved SVG"),
        ("document_id", "duplicate document revision"),
    ],
)
def test_distinct_sku_ids_cannot_reuse_approved_evidence(tmp_path, field, error):
    db, labels, rows = _fixture(tmp_path)
    # Keep the CSV and journal consistent: this is a cohort collision,
    # not a mismatched journal join or duplicate SKU/operation identifier.
    rows[1][field] = rows[0][field]
    _write_labels(labels, rows)
    with sqlite3.connect(db) as connection:
        connection.execute(
            f"UPDATE design_vlm_runs SET {field}=? WHERE operation_id=?",
            (rows[1][field], rows[1]["operation_id"]),
        )

    with pytest.raises(CalibrationError, match=error):
        _eval(db, labels)


def test_distinct_revisions_of_one_document_are_not_exact_duplicates(tmp_path):
    db, labels, rows = _fixture(tmp_path, count=2)
    rows[1]["document_id"] = rows[0]["document_id"]
    rows[1]["revision"] = "2"
    _write_labels(labels, rows)
    with sqlite3.connect(db) as connection:
        connection.execute(
            "UPDATE design_vlm_runs SET document_id=?, revision=2 WHERE operation_id=?",
            (rows[1]["document_id"], rows[1]["operation_id"]),
        )

    report = _eval(db, labels)
    assert report["sku_count"] == 2
    assert report["status"] == "INSUFFICIENT_OPERATOR_CALIBRATION"
    assert report["cohort_selection_independently_verified"] is False
