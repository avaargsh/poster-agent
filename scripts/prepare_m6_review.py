"""M6: prepare a private offline review package from a completed M5g batch.

Default is a read-only plan. --apply copies the existing square PNGs, uses
the official read-only native 4:5 export route, and creates unreviewed CSV
drafts. No OCR, paid model, upload, authorization or human judgment occurs.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
from pathlib import Path

from fastapi import HTTPException
from PIL import Image

from app import storage
from app.batch_artifact_integrity import verify_batch_artifact
from app.cutout_quality import (
    REVIEW_FIELDS as CUTOUT_REVIEW_FIELDS,
    SCHEMA as CUTOUT_SCHEMA,
    evidence_from_pngs,
)
from app.channel_export_routes import download_channel_export
from evals.native_batch_scope import load_batch_scope
from scripts.validate_m6_cohort import REGISTRY_FIELDS, REVIEW_FIELDS

SCHEMA = "m6-review-preparation-1"
NATIVE_ID = "feed_4x5_native"
FIELDS = (
    "sku_integrity", "text_legibility", "title", "subtitle",
    "price", "original_price", "price_position", "original_price_position",
    "cta", "composition",
)


class PreparationError(ValueError):
    """Local batch review preparation could not be completed safely."""


def _sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _image_ok(content: bytes, size: tuple[int, int]) -> bool:
    try:
        with Image.open(io.BytesIO(content)) as image:
            image.load()
            return image.format == "PNG" and image.size == size
    except (OSError, ValueError):
        return False


def _write_csv(path: Path, fields: tuple[str, ...], rows: list[dict]) -> None:
    with path.open("x", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="raise")
        writer.writeheader()
        writer.writerows(rows)


def prepare_package(journal: Path, output_dir: Path, *, apply: bool = False) -> dict:
    """Reuse existing artifacts without changing poster storage or review state."""
    journal = Path(journal).resolve(strict=True)
    scope = load_batch_scope(journal)
    source = json.loads(journal.read_text(encoding="utf-8"))
    batch = source["rows"]
    sku_by_id = scope["sku_by_id"]
    items = sorted(((sku, pid) for pid, sku in sku_by_id.items()))
    output_dir = Path(output_dir).expanduser().resolve()
    if apply and output_dir.exists():
        raise PreparationError("输出目录已存在；为防止覆盖图片或人工标注，请指定全新的批次目录")

    cohort_id = "m6-" + scope["source_batch_journal_sha256"][:12]
    plan = {
        "schema_version": SCHEMA,
        "cohort_id": cohort_id,
        "mode": "apply" if apply else "check",
        "source_batch_journal_sha256": scope["source_batch_journal_sha256"],
        "source_manifest_sha256": scope["source_manifest_sha256"],
        "batch_sku_count": len(items),
        "expected_review_rows": len(items) * 2,
        "automatically_accepted": False,
        "human_review_performed": False,
        "ocr_inference_performed": False,
        "paid_model_calls": 0,
        "cutout_candidate_count": sum(
            bool(batch[sku].get("product_cutout_sha256")) for sku, _ in items
        ),
        "cutout_model_inference_performed": False,
        "cutout_human_accepted": 0,
    }
    if not apply:
        return plan

    # Directories are intentionally local-only and must be new. A failure
    # produces an incomplete directory, never a fake completed review package.
    output_dir.mkdir(parents=True, exist_ok=False)
    images_dir = output_dir / "images"
    images_dir.mkdir()

    registry_rows: list[dict] = []
    review_rows: list[dict] = []
    cutout_rows: list[dict] = []
    cutout_items: list[dict] = []
    invalid_cutout_count = 0
    missing_images = 0
    export_failures: list[dict] = []

    for sku, pid in items:
        record = batch[sku]
        has_cutout = bool(record.get("product_cutout_sha256"))
        cutout_row = {field: "" for field in CUTOUT_REVIEW_FIELDS}
        cutout_row.update({
            "cohort_id": cohort_id,
            "sku_id": sku,
            "poster_id": pid,
            "source_version": str(record["version"]),
            "quality_state": "SOURCE_INVALID" if has_cutout else "NOT_APPLICABLE",
            "candidate_origin": (
                "USER_SUPPLIED_OR_PREVIEW_NOT_ATTESTED" if has_cutout else "N/A"
            ),
            "model_runtime_attested": "NO",
            "subject_integrity_review": "UNVERIFIED" if has_cutout else "N/A",
            "edge_artifact_review": "UNVERIFIED" if has_cutout else "N/A",
            "brand_mark_review": "UNVERIFIED" if has_cutout else "N/A",
            "human_accept": "UNVERIFIED" if has_cutout else "N/A",
        })
        registry_rows.append({
            "cohort_id": cohort_id,
            "sku_id": sku,
            "product_category": "",
            "product_family_id": "",
            "variant_or_packaging_condition": "",
            "source_asset_authorization_status": "UNVERIFIED",
            "authorization_evidence_ref": "",
            "logo_authorization_status": (
                "UNVERIFIED" if record.get("logo_sha256") else "NOT_APPLICABLE"
            ),
            "source_image_sha256": record["product_sha256"],
            "distinct_sku_verified": "UNVERIFIED",
            "duplicate_source_image_reviewed": "UNVERIFIED",
            "reviewer_alias": "",
            "notes": "",
        })

        version = str(record["version"])
        spec_sha = ""
        square_sha = ""
        spec: dict = {}
        square_data = b""
        problem = ""
        try:
            verify_batch_artifact(pid, record)
            pinned_version = record.get("version")
            if type(pinned_version) is not int or pinned_version < 1:
                raise PreparationError("M5g 记录的海报版本号非法")
            current = storage.version_view(pid, pinned_version)
            version = str(pinned_version)
            if current["background_mode"] != "local":
                raise PreparationError("背景不是 local，拒绝混入免费基线")
            spec_path = storage.poster_dir(pid) / "versions" / f"v{version}.json"
            spec_raw = spec_path.read_bytes()
            spec = json.loads(spec_raw)
            if spec.get("background_mode") != "local":
                raise PreparationError("规格中的背景模式不是 local")
            spec_sha = _sha(spec_raw)
            square_data = storage.version_path(pid, int(version)).read_bytes()
            if not _image_ok(square_data, (1024, 1024)):
                raise PreparationError("当前方图不是有效的 1024x1024 PNG")
            square_sha = _sha(square_data)
        except (FileNotFoundError, OSError, ValueError, KeyError,
                json.JSONDecodeError) as exc:
            problem = f"SOURCE_INVALID:{type(exc).__name__}"

        common = {
            "cohort_id": cohort_id,
            "sku_id": sku,
            "poster_id": pid,
            "source_version": version,
            "source_product_sha256": record["product_sha256"],
            "request_sha256": record["request_sha256"],
            "spec_sha256": spec_sha,
            "source_square_image_sha256": square_sha,
            "reviewer_alias": "",
            **{field: "UNVERIFIED" for field in FIELDS},
            "overall_accept": "UNVERIFIED",
            "overall_reason": "NOT_REVIEWED",
            "reviewed_at_utc": "",
        }
        if not str(spec.get("subtitle") or "").strip():
            common["subtitle"] = "N/A"
        if spec.get("original_price") in (None, ""):
            common["original_price"] = "N/A"
            common["original_price_position"] = "N/A"

        square_row = {
            **common, "variant_id": "square",
            "output_image_path": "", "output_image_sha256": "",
        }
        native_row = {
            **common, "variant_id": NATIVE_ID,
            "output_image_path": "", "output_image_sha256": "",
        }
        if problem:
            for row in (square_row, native_row):
                row["overall_reason"] = "OUTPUT_MISSING:" + problem
            missing_images += 2
            export_failures.append({"sku_id": sku, "reason": problem})
            review_rows.extend((square_row, native_row))
            cutout_rows.append(cutout_row)
            if has_cutout:
                invalid_cutout_count += 1
            continue

        if has_cutout:
            try:
                assets = storage.poster_dir(pid) / "assets"
                original_png = (assets / "product.png").read_bytes()
                cutout_png = (assets / "product_cutout.png").read_bytes()
                metrics, paired_png = evidence_from_pngs(original_png, cutout_png)
                name = f"images/{sku}-cutout-comparison.png"
                (output_dir / name).write_bytes(paired_png)
                cutout_row.update({
                    "source_product_sha256": metrics["source_png_sha256"],
                    "cutout_sha256": metrics["cutout_png_sha256"],
                    "comparison_image_path": name,
                    "comparison_image_sha256": metrics["comparison_png_sha256"],
                    "foreground_fraction": str(metrics["foreground_fraction"]),
                    "soft_alpha_fraction": str(metrics["soft_alpha_fraction"]),
                    "edge_touch_fraction": str(metrics["edge_touch_fraction"]),
                    "component_count": str(metrics["component_count"]),
                    "retained_rgb_mae": str(metrics["retained_rgb_mae"]),
                    "machine_risks": ";".join(metrics["machine_risks"]),
                    "quality_state": "REVIEW_REQUIRED",
                })
                cutout_items.append({
                    "sku_id": sku, "poster_id": pid,
                    "source_version": int(version), "metrics": metrics,
                    "comparison_image_path": name,
                })
            except (OSError, ValueError) as exc:
                invalid_cutout_count += 1
                cutout_row["quality_state"] = "SOURCE_INVALID"
                cutout_row["machine_risks"] = "CUTOUT_EVIDENCE_INVALID"
                cutout_items.append({
                    "sku_id": sku, "poster_id": pid,
                    "source_version": int(version),
                    "error": f"INVALID_CUTOUT_EVIDENCE:{type(exc).__name__}",
                })
        cutout_rows.append(cutout_row)

        square_name = f"images/{sku}-square.png"
        (output_dir / square_name).write_bytes(square_data)
        square_row.update({
            "output_image_path": square_name,
            "output_image_sha256": square_sha,
        })
        # Reuse the same export handler as the API without starting another
        # ASGI lifespan. The live local workbench may own the worker locks.
        try:
            response = download_channel_export(pid, int(version), NATIVE_ID)
        except HTTPException as exc:
            native_bytes = b""
            export_status = exc.status_code
            export_headers = {}
        else:
            native_bytes = response.body
            export_status = response.status_code
            export_headers = response.headers
        if (
            export_status == 200
            and export_headers.get("x-source-image-sha256") == square_sha
            and export_headers.get("x-export-mode") == "native_layout"
            and export_headers.get("x-automatically-accepted") == "false"
            and _image_ok(native_bytes, (1080, 1350))
        ):
            native_name = f"images/{sku}-native-4x5.png"
            (output_dir / native_name).write_bytes(native_bytes)
            native_row.update({
                "output_image_path": native_name,
                "output_image_sha256": _sha(native_bytes),
            })
        else:
            # No content or failure text from the route is treated as
            # approved content. The row must remain visible and unverified.
            reason = f"EXPORT_FAILED:HTTP_{export_status}"
            native_row["overall_reason"] = reason
            export_failures.append({"sku_id": sku, "reason": reason})
            missing_images += 1

        review_rows.extend((square_row, native_row))

    _write_csv(
        output_dir / "m6_cohort_registry.csv", REGISTRY_FIELDS, registry_rows
    )
    _write_csv(
        output_dir / "m6_cutout_reviews.csv", CUTOUT_REVIEW_FIELDS, cutout_rows
    )
    cutout_manifest = {
        "schema_version": CUTOUT_SCHEMA,
        "cohort_id": cohort_id,
        "source_batch_journal_sha256": scope["source_batch_journal_sha256"],
        "candidate_count": plan["cutout_candidate_count"],
        "evidence_count": sum(
            row["quality_state"] == "REVIEW_REQUIRED" for row in cutout_rows
        ),
        "invalid_evidence_count": invalid_cutout_count,
        "model_inference_performed": False,
        "model_runtime_attested": False,
        "human_acceptance_performed": False,
        "comparison_panels": "LEFT_ORIGINAL_RIGHT_TRANSPARENT_CHECKERBOARD",
        "items": cutout_items,
    }
    (output_dir / "m6_cutout_evidence.json").write_text(
        json.dumps(cutout_manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    _write_csv(
        output_dir / "m6_output_reviews.csv", REVIEW_FIELDS, review_rows
    )
    summary = {
        **plan,
        "review_rows_created": len(review_rows),
        "missing_output_count": missing_images,
        "export_failures": export_failures,
        "cutout_evidence_count": cutout_manifest["evidence_count"],
        "cutout_invalid_count": invalid_cutout_count,
        "cutout_reviews_file": "m6_cutout_reviews.csv",
        "cutout_evidence_file": "m6_cutout_evidence.json",
        "registry_file": "m6_cohort_registry.csv",
        "reviews_file": "m6_output_reviews.csv",
        "workflow": (
            "MANUAL_REQUIRED: confirm licenses and independent SKUs, "
            "review paired original/cutout images if present, verify masks and "
            "brand marks, review both exported PNGs, run real OCR separately, "
            "then validate evidence"
        ),
    }
    (output_dir / "m6_preparation.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Prepare local unverified M6 drafts without models or OCR"
    )
    parser.add_argument("--journal", required=True, type=Path)
    parser.add_argument(
        "--output-dir", type=Path,
        default=Path("data/eval_runs/m6_review_package_001"),
    )
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        result = prepare_package(args.journal, args.output_dir, apply=args.apply)
    except (PreparationError, OSError, ValueError, KeyError) as exc:
        parser.exit(2, f"M6 离线准备失败：{exc}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
