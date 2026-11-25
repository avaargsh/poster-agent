"""Read-only, operator-asserted Visual Critic calibration over an explicit SKU cohort.

No model inference, OCR, network access, database migrations or file writes.
A local VLM journal is NOT proof that genuine weights ran. A CSV reviewer alias
is NOT an authenticated independent human review or a product-use license.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import os
import re
import sqlite3
import stat
import sys
from collections import Counter
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import quote

from app.designs.vlm_local import VLMModelProfileError, _finite_json, load_vlm_profile, parse_findings

SCHEMA = "design-vlm-shadow-calibration-v1"
MINIMUM_SKUS = 20
MAX_SAMPLES = 1000
MAX_CSV_BYTES = 2 * 1024 * 1024
VISUAL_CATEGORIES = (
    "contrast",
    "spacing",
    "visual_hierarchy",
    "product_visibility",
    "text_legibility",
    "brand_style",
)
LABEL_FIELDS = (
    "sku_id",
    "operation_id",
    "document_id",
    "revision",
    "approval_id",
    "svg_sha256",
    "png_sha256",
    "model_profile_sha256",
    "reviewer_alias",
    "source_authorization",
    *VISUAL_CATEGORIES,
)
_LABELS = frozenset({"PRESENT", "ABSENT", "UNVERIFIED"})
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_LOCAL_MODEL_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_SKU_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_DOC_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_REVIEWER = re.compile(r"^[a-z][a-z0-9_-]{2,63}$")
_APPROVAL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_OP_ID = re.compile(r"^vlm_[0-9a-f]{32}$")
_REVISION = re.compile(r"^[1-9][0-9]{0,9}$")
_STATUSES = ("EXECUTED", "UNKNOWN", "EXECUTING")


class CalibrationError(ValueError):
    """An untrusted cohort or journal cannot be compared safely."""


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _fraction(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 6) if denominator else None


def _coverage_status(positive: int, negative: int) -> str:
    if not positive and not negative:
        return "NO_EVALUABLE_CASES"
    if not positive:
        return "NO_POSITIVE_EXAMPLES"
    if not negative:
        return "NO_NEGATIVE_EXAMPLES"
    return "BOTH_CLASSES_OBSERVED"


def _resource_hourly_rate(value: Decimal | str | None) -> Decimal | None:
    if value is None:
        return None
    try:
        rate = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise CalibrationError("invalid resource hourly rate") from None
    if not rate.is_finite() or rate < 0 or rate > Decimal("1000000"):
        raise CalibrationError("invalid resource hourly rate")
    return rate


def _require(value: object, pattern: re.Pattern[str]) -> None:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise CalibrationError("invalid cohort identity or digest")


def _read_labels(path: Path) -> tuple[list[dict[str, str]], str]:
    try:
        # The CSV is untrusted operator input. Open a single regular-file
        # descriptor and bound the actual read, not a stale path.stat() value.
        # O_NONBLOCK avoids blocking on a substituted pipe or device.
        if path.is_symlink():
            raise CalibrationError("cohort CSV must be a regular file, not a symlink")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        flags |= getattr(os, "O_NONBLOCK", 0)
        with os.fdopen(os.open(path, flags), "rb") as source:
            if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                raise CalibrationError("cohort CSV must be a regular file")
            raw = source.read(MAX_CSV_BYTES + 1)
        if not raw or len(raw) > MAX_CSV_BYTES:
            raise CalibrationError("cohort CSV is missing or exceeds size limit")
        decoded = raw.decode("utf-8-sig")
        with io.StringIO(decoded, newline="") as stream:
            reader = csv.DictReader(stream, strict=True)
            if (
                reader.fieldnames is None
                or len(reader.fieldnames) != len(LABEL_FIELDS)
                or set(reader.fieldnames) != set(LABEL_FIELDS)
            ):
                raise CalibrationError("cohort CSV columns do not match the frozen schema")
            rows = list(reader)
    except (OSError, UnicodeError, csv.Error):
        raise CalibrationError("cohort CSV cannot be read") from None

    if not 1 <= len(rows) <= MAX_SAMPLES:
        raise CalibrationError("cohort must contain between 1 and 1000 samples")
    operations: set[str] = set()
    skus: set[str] = set()
    document_revisions: set[tuple[str, int]] = set()
    approved_pngs: set[str] = set()
    approved_svgs: set[str] = set()
    profiles: set[str] = set()
    for row in rows:
        if None in row or any(value is None for value in row.values()):
            raise CalibrationError("malformed cohort CSV row")
        _require(row["sku_id"], _SKU_ID)
        _require(row["operation_id"], _OP_ID)
        _require(row["document_id"], _DOC_ID)
        _require(row["revision"], _REVISION)
        _require(row["approval_id"], _APPROVAL)
        _require(row["svg_sha256"], _SHA256)
        _require(row["png_sha256"], _SHA256)
        _require(row["model_profile_sha256"], _SHA256)
        _require(row["reviewer_alias"], _REVIEWER)
        if int(row["revision"]) > 2**31 - 1:
            raise CalibrationError("revision exceeds supported range")
        if row["source_authorization"] != "DECLARED_AUTHORIZED":
            raise CalibrationError("operator has not declared source authorization")
        if any(row[category] not in _LABELS for category in VISUAL_CATEGORIES):
            raise CalibrationError("invalid or absent human category label")
        if row["sku_id"] in skus or row["operation_id"] in operations:
            raise CalibrationError("duplicate SKU or operation in frozen cohort")
        document_revision = (row["document_id"], int(row["revision"]))
        if document_revision in document_revisions:
            raise CalibrationError("duplicate document revision in frozen cohort")
        if row["png_sha256"] in approved_pngs:
            raise CalibrationError("duplicate approved PNG in frozen cohort")
        if row["svg_sha256"] in approved_svgs:
            raise CalibrationError("duplicate approved SVG in frozen cohort")
        document_revisions.add(document_revision)
        approved_pngs.add(row["png_sha256"])
        approved_svgs.add(row["svg_sha256"])
        skus.add(row["sku_id"])
        operations.add(row["operation_id"])
        profiles.add(row["model_profile_sha256"])
    if len(profiles) != 1:
        raise CalibrationError("mixed model profile snapshots cannot be compared")
    return rows, hashlib.sha256(raw).hexdigest()


def _read_journal(db_path: Path, labels: list[dict[str, str]]) -> tuple[list[dict], str]:
    """Query the existing authoritative table in SQLite read-only mode.

    Read a consistent snapshot; NEVER instantiate DesignRepository, whose
    initializer performs schema creation and orphan-execution reconciliation.
    """
    try:
        if not db_path.is_file():
            raise CalibrationError("local VLM journal unavailable")
        uri = "file:" + quote(str(db_path.resolve())) + "?mode=ro"
        with sqlite3.connect(uri, uri=True, timeout=3.0) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA query_only=ON")
            db.execute("BEGIN")
            selected: list[dict] = []
            for label in labels:
                result = db.execute(
                    """
                    SELECT operation_id, document_id, revision, approval_id,
                           svg_sha256, png_sha256, model_profile_sha256,
                           model_name, status, findings_json, runtime_json,
                           started_at, finished_at, reason, principal_id
                    FROM design_vlm_runs
                    WHERE operation_id = ?
                    """,
                    (label["operation_id"],),
                ).fetchone()
                if result is None:
                    raise CalibrationError("cohort references a missing VLM journal entry")
                selected.append(dict(result))
            db.execute("ROLLBACK")
    except CalibrationError:
        raise
    except (sqlite3.Error, OSError, ValueError):
        raise CalibrationError("local VLM journal unavailable or malformed") from None
    # The digest covers the entire selected journal rows, including model
    # findings and timestamps, without publishing raw SKU/reviewer text.
    return selected, _digest(sorted(selected, key=lambda row: row["operation_id"]))


def _journal_timestamp(value: object) -> datetime:
    """Match the live journal's bounded, timezone-aware ISO timestamp contract."""
    if not isinstance(value, str) or not 1 <= len(value) <= 64:
        raise CalibrationError("missing or oversized journal timestamp")
    try:
        parsed = datetime.fromisoformat(value)
    except (ValueError, OverflowError):
        raise CalibrationError("invalid journal timestamp") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise CalibrationError("journal timestamp must include a timezone")
    return parsed


def _elapsed_ms(start: object, finish: object) -> int:
    a, b = _journal_timestamp(start), _journal_timestamp(finish)
    elapsed = (b - a).total_seconds() * 1000
    if not math.isfinite(elapsed) or elapsed < 0 or elapsed > 24 * 60 * 60 * 1000:
        raise CalibrationError("invalid or non-monotonic journal timestamps")
    return round(elapsed)


def _validate_journal_metadata(row: dict) -> int | None:
    """Apply the same state/timestamp invariants as online VLM journal replay."""
    started = _journal_timestamp(row["started_at"])
    status = row["status"]
    reason = row["reason"]

    if status == "EXECUTING":
        if reason is not None or row["finished_at"] is not None:
            raise CalibrationError("executing journal contains terminal metadata")
        return None

    finished = _journal_timestamp(row["finished_at"])
    if finished < started:
        raise CalibrationError("journal finish precedes start")

    if status == "EXECUTED":
        if reason is not None:
            raise CalibrationError("executed journal contains a failure reason")
        return _elapsed_ms(row["started_at"], row["finished_at"])

    if status == "UNKNOWN":
        if not isinstance(reason, str) or not reason or len(reason) > 128:
            raise CalibrationError("unknown journal is missing a bounded reason")
        return None

    raise CalibrationError("unsupported visual journal state")


def _checked_record(row: dict, label: dict[str, str]) -> tuple[set[str], int | None, int]:
    """Fail the entire analysis on drift instead of quietly shrinking the cohort."""
    for field in (
        "operation_id",
        "document_id",
        "approval_id",
        "svg_sha256",
        "png_sha256",
        "model_profile_sha256",
    ):
        if row[field] != label[field]:
            raise CalibrationError("journal and reviewer snapshot identities disagree")
    if type(row["revision"]) is not int or row["revision"] != int(label["revision"]):
        raise CalibrationError("journal revision does not match reviewed snapshot")
    if not isinstance(row["principal_id"], str) or not row["principal_id"]:
        raise CalibrationError("journal has no attributed local operator")
    if not isinstance(row["model_name"], str) or not row["model_name"]:
        raise CalibrationError("journal model identity is missing")
    if row["status"] not in _STATUSES:
        raise CalibrationError("journal contains an unsupported execution state")
    elapsed = _validate_journal_metadata(row)

    if row["status"] != "EXECUTED":
        if row["findings_json"] is not None or row["runtime_json"] is not None:
            raise CalibrationError("unresolved run must not supply model evidence")
        return set(), None, 0

    if not isinstance(row["findings_json"], str):
        raise CalibrationError("executed run is missing its recorded findings")
    # parse_findings checks duplicate keys/IDs, arbitrary claims, size, bounds,
    # and categories; do not rely on a trusted database or model output.
    try:
        findings = parse_findings('{"findings":' + row["findings_json"] + "}")
        if not isinstance(row["runtime_json"], str) or len(row["runtime_json"]) > 4096:
            raise ValueError("missing runtime provenance")
        runtime = _finite_json(row["runtime_json"])
        if (
            not isinstance(runtime, dict)
            or set(runtime)
            != {
                "source",
                "local_model_digest_claim",
                "execution_evidence",
                "attested",
            }
            or runtime["source"] != "local_ollama"
            or runtime["execution_evidence"] != "OPERATOR_LOCAL_NOT_ATTESTED"
            or runtime["attested"] is not False
            or not isinstance(runtime["local_model_digest_claim"], str)
            or _LOCAL_MODEL_DIGEST.fullmatch(runtime["local_model_digest_claim"]) is None
        ):
            raise ValueError("unexpected runtime provenance claim")
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise CalibrationError("invalid or untrusted local VLM observations") from None
    try:
        profile = load_vlm_profile(
            json.dumps(
                {
                    "schema_version": 1,
                    "model": row["model_name"],
                    "digest": runtime["local_model_digest_claim"],
                }
            )
        )
    except VLMModelProfileError:
        raise CalibrationError("invalid journal model profile fingerprint") from None
    if profile.fingerprint != row["model_profile_sha256"]:
        raise CalibrationError("journal model profile fingerprint mismatch")
    return {finding["category"] for finding in findings}, elapsed, len(findings)


def _nearest_rank(values: list[int], fraction: float) -> int | None:
    if not values:
        return None
    return sorted(values)[max(0, math.ceil(len(values) * fraction) - 1)]


def _cost_report(rate: Decimal | None, durations: list[int], unresolved_count: int) -> dict:
    executed_count = len(durations)
    elapsed_ms = sum(durations)
    total_cost = None
    per_operation_cost = None
    if rate is None:
        status = "LOCAL_COMPUTE_COST_NOT_METERED"
    elif executed_count == 0:
        status = "NO_EXECUTED_OPERATION_COST_BASELINE"
    else:
        raw_total = rate * Decimal(elapsed_ms) / Decimal(3_600_000)
        total_cost = raw_total.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
        per_operation_cost = (raw_total / Decimal(executed_count)).quantize(
            Decimal("0.000001"), rounding=ROUND_HALF_UP
        )
        status = "OPERATOR_RATE_ESTIMATE"

    return {
        "status": status,
        "resource_hourly_rate_usd": format(rate.normalize(), "f") if rate is not None else None,
        "estimated_total_usd": format(total_cost, "f") if total_cost is not None else None,
        "estimated_per_executed_operation_usd": (
            format(per_operation_cost, "f") if per_operation_cost is not None else None
        ),
        "executed_operation_count": executed_count,
        "unresolved_operation_count": unresolved_count,
        "total_executed_wall_elapsed_ms": elapsed_ms,
        "scope": "EXECUTED_JOURNAL_WALL_TIME_ONLY",
        "limitations": [
            "not provider billing",
            "wall time includes preflight and postflight, not model-only inference or GPU utilization",
            "UNKNOWN and EXECUTING attempts are excluded and may have consumed compute",
            "startup, idle capacity and shared host costs are excluded",
        ],
    }


def evaluate_shadow_cohort(
    db_path: Path,
    labels_path: Path,
    *,
    resource_hourly_rate_usd: Decimal | str | None = None,
) -> dict:
    """Metrics refer only to this explicitly selected, operator-asserted cohort."""
    rate = _resource_hourly_rate(resource_hourly_rate_usd)
    labels, csv_sha = _read_labels(Path(labels_path))
    runs, journal_sha = _read_journal(Path(db_path), labels)
    statuses = Counter()
    measures = {
        category: Counter(tp=0, fp=0, fn=0, tn=0, unverified_human_labels=0, not_executed_labels=0)
        for category in VISUAL_CATEGORIES
    }
    profiles: set[str] = set()
    models: set[str] = set()
    fully_reviewed = 0
    durations: list[int] = []
    model_findings_count = 0
    sku_with_model_findings = 0

    for label, run in zip(labels, runs, strict=True):
        predictions, elapsed, finding_count = _checked_record(run, label)
        model_findings_count += finding_count
        if finding_count:
            sku_with_model_findings += 1
        status = run["status"]
        statuses[status] += 1
        profiles.add(run["model_profile_sha256"])
        models.add(run["model_name"])
        if elapsed is not None:
            durations.append(elapsed)
        if status == "EXECUTED" and all(
            label[category] != "UNVERIFIED" for category in VISUAL_CATEGORIES
        ):
            fully_reviewed += 1

        for category in VISUAL_CATEGORIES:
            counts = measures[category]
            observed = label[category]
            if observed == "UNVERIFIED":
                counts["unverified_human_labels"] += 1
            elif status != "EXECUTED":
                counts["not_executed_labels"] += 1
            else:
                predicted = category in predictions
                counts[
                    "tp"
                    if observed == "PRESENT" and predicted
                    else "fn"
                    if observed == "PRESENT"
                    else "fp"
                    if predicted
                    else "tn"
                ] += 1

    if len(models) != 1 or len(profiles) != 1:
        raise CalibrationError("mixed local VLM models were selected")
    categories = {}
    for category in VISUAL_CATEGORIES:
        counts = measures[category]
        tp, fp, fn, tn = (counts[key] for key in ("tp", "fp", "fn", "tn"))
        categories[category] = {
            **{
                key: counts[key]
                for key in (
                    "tp",
                    "fp",
                    "fn",
                    "tn",
                    "unverified_human_labels",
                    "not_executed_labels",
                )
            },
            "evaluable_denominator": tp + fp + fn + tn,
            "positive_support": tp + fn,
            "negative_support": fp + tn,
            "coverage_status": _coverage_status(tp + fn, fp + tn),
            "precision": _fraction(tp, tp + fp),
            "recall": _fraction(tp, tp + fn),
            "false_positive_rate": _fraction(fp, fp + tn),
            "false_negative_rate": _fraction(fn, tp + fn),
        }

    total = {
        key: sum(measures[category][key] for category in VISUAL_CATEGORIES)
        for key in ("tp", "fp", "fn", "tn")
    }
    complete = len(labels) >= MINIMUM_SKUS and fully_reviewed == len(labels)
    executed_operation_count = statuses["EXECUTED"]
    zero_findings_not_a_pass = executed_operation_count > 0 and model_findings_count == 0
    signal_status = (
        "NO_EXECUTED_MODEL_RUNS_NOT_A_PASS"
        if executed_operation_count == 0
        else (
            "NO_FINDINGS_NOT_A_PASS"
            if zero_findings_not_a_pass
            else "FINDINGS_RECORDED_REQUIRES_HUMAN_REVIEW"
        )
    )
    cost = _cost_report(rate, durations, statuses["UNKNOWN"] + statuses["EXECUTING"])
    return {
        "schema_version": SCHEMA,
        "status": (
            "OPERATOR_CALIBRATION_ONLY_NOT_ATTESTED"
            if complete
            else "INSUFFICIENT_OPERATOR_CALIBRATION"
        ),
        "scope": "selected_local_operator_journal_and_declared_human_labels_only",
        "sku_count": len(labels),
        "operation_count": len(runs),
        "complete_case_count": fully_reviewed,
        "signal_status": signal_status,
        "model_findings_count": model_findings_count,
        "sku_with_model_findings": sku_with_model_findings,
        "zero_findings_not_a_pass": zero_findings_not_a_pass,
        "minimum_complete_skus_required": MINIMUM_SKUS,
        "minimum_complete_skus_met": complete,
        "journal_status_counts": {state: statuses[state] for state in _STATUSES},
        "model_name": next(iter(models)),
        "model_profile_sha256": next(iter(profiles)),
        "by_category": categories,
        "micro": {
            **total,
            "precision": _fraction(total["tp"], total["tp"] + total["fp"]),
            "recall": _fraction(total["tp"], total["tp"] + total["fn"]),
            "false_positive_rate": _fraction(total["fp"], total["fp"] + total["tn"]),
            "false_negative_rate": _fraction(total["fn"], total["tp"] + total["fn"]),
        },
        "wall_elapsed_ms": {
            "count": len(durations),
            "p50": _nearest_rank(durations, 0.50),
            "p95": _nearest_rank(durations, 0.95),
            "scope": "local_adapter_journal_elapsed_not_model_only",
        },
        "cost": cost,
        "input_sha256": {"labels_csv": csv_sha, "journal_rows": journal_sha},
        "source_authorizations_verified": False,
        "cohort_selection_independently_verified": False,
        "model_execution_attested": False,
        "independent_human_review_attested": False,
        "quality_verified": False,
        "repair_authorized": False,
        "release_ready": False,
        "limitations": [
            "Rows and labels are operator-selected; cohort selection is not independently audited.",
            "Declared source authorization and reviewer aliases are not independently verified.",
            "Local Ollama journal does not attest model weights or genuine visual inference.",
            "Metrics are category-presence comparisons, not box localization or SKU safety checks.",
            "A completed shadow cohort never grants edit, repair, or publish permissions.",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compare local Visual Critic journaled findings to a frozen human CSV"
    )
    parser.add_argument("--db", type=Path, required=True, help="existing local designs.sqlite3")
    parser.add_argument("--labels", type=Path, required=True, help="manual category review CSV")
    parser.add_argument(
        "--resource-hourly-rate-usd",
        default=None,
        help="optional operator-supplied local resource rate per hour for a wall-time estimate",
    )
    args = parser.parse_args(argv)
    try:
        result = evaluate_shadow_cohort(
            args.db, args.labels, resource_hourly_rate_usd=args.resource_hourly_rate_usd
        )
    except CalibrationError as exc:
        print(f"Visual Critic calibration refused: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
