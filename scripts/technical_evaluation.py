"""Directory -> local posters -> two-format technical checks, without human claims.

Prices are explicit operator test inputs, never inferred from images. Source
authorization and independent SKU identity remain UNVERIFIED until reviewed.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
from contextlib import contextmanager
from pathlib import Path

from fastapi import HTTPException
from PIL import Image

from app.schemas import PosterSpec
from scripts.local_cutout_cohort import _source_png, prepare_cohort
from scripts.prepare_m6_review import prepare_package
from scripts.real_sku_batch import run_batch
from scripts.validate_m6_cohort import REGISTRY_FIELDS


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _csv_bytes(fields, rows) -> bytes:
    data = io.StringIO(newline="")
    writer = csv.DictWriter(data, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return data.getvalue().encode("utf-8")


def _scan(images: Path, output: Path, price: str, min_images: int):
    if images.is_symlink() or not images.is_dir():
        raise ValueError("图片目录不存在或为符号链接")
    root, target = images.resolve(), output.resolve()
    if target == root or target.is_relative_to(root) or root.is_relative_to(target):
        raise ValueError("输出目录必须与原图目录分开")
    if not 1 <= min_images <= 100:
        raise ValueError("图片数量范围为 1–100")
    candidates = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError("图片目录不能包含符号链接")
        if path.is_file() and path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}:
            candidates.append(path)
            if len(candidates) > 100:
                raise ValueError("每批最多 100 张图片")
    if len(candidates) < min_images:
        raise ValueError(f"至少需要 {min_images} 张图片，当前 {len(candidates)} 张")
    prepared = []
    for index, path in enumerate(candidates, 1):
        with path.open("rb") as handle:
            raw = handle.read(10 * 1024 * 1024 + 1)
        if not raw or len(raw) > 10 * 1024 * 1024:
            raise ValueError("图片为空或超过 10 MiB")
        try:
            _source_png(raw, path.name)  # reuse the real upload/ICC/pixel validation
        except HTTPException as exc:
            raise ValueError(str(exc.detail)) from exc
        digest = _sha(raw)
        sku = f"img-{index:03d}-{digest[:12]}"
        spec = PosterSpec(title=f"技术测试商品 {index:03d}", price=price)
        prepared.append(
            {
                "sku_id": sku,
                "raw": raw,
                "sha256": digest,
                "original_name": str(path.relative_to(root)),
                "image_name": sku + path.suffix.lower(),
                "spec": spec,
            }
        )
    return prepared


def _plan(items: list[dict]) -> dict:
    return {
        "image_count": len(items),
        "unique_source_sha256_count": len({x["sha256"] for x in items}),
        "authorization_status": "UNVERIFIED",
        "independent_sku_status": "UNVERIFIED",
        "human_visual_review": "UNVERIFIED",
        "price_source": "OPERATOR_TEST_VALUE",
        "quality_accepted": False,
        "paid_model_calls": 0,
    }


def _write_inputs(items: list[dict], output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=False)
    (output / "images").mkdir()
    rows = []
    for item in items:
        (output / "images" / item["image_name"]).write_bytes(item["raw"])
        rows.append(
            {
                "sku_id": item["sku_id"],
                "product_image": "images/" + item["image_name"],
                "title": item["spec"].title,
                "price": str(item["spec"].price),
            }
        )
    raw = _csv_bytes(("sku_id", "product_image", "title", "price"), rows)
    (output / "manifest.csv").write_bytes(raw)
    cohort = "technical-" + _sha(raw)[:12]
    registry = []
    for item in items:
        row = dict.fromkeys(REGISTRY_FIELDS, "")
        row.update(
            cohort_id=cohort,
            sku_id=item["sku_id"],
            source_image_sha256=item["sha256"],
            source_asset_authorization_status="UNVERIFIED",
            logo_authorization_status="NOT_APPLICABLE",
            distinct_sku_verified="UNVERIFIED",
            duplicate_source_image_reviewed="UNVERIFIED",
            notes="TECHNICAL_EVALUATION_ONLY",
        )
        registry.append(row)
    (output / "sku_registry.csv").write_bytes(_csv_bytes(REGISTRY_FIELDS, registry))
    (output / "source_snapshot.json").write_text(
        json.dumps(
            [{key: item[key] for key in ("sku_id", "original_name", "sha256")} for item in items],
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return {**_plan(items), "manifest_sha256": _sha(raw), "cohort_id": cohort}


def prepare_inputs(images: Path, output_dir: Path, *, price: str, min_images: int = 20) -> dict:
    """Freeze local copies and machine identity. No license/reviewer is invented."""
    output = Path(output_dir)
    if output.exists() or output.is_symlink():
        raise ValueError("输出目录已存在，请选择新目录以保留既有记录")
    items = _scan(Path(images), output, price, min_images)
    return _write_inputs(items, output)


@contextmanager
def _local_storage(output: Path):
    # Keep test runs separate from the user's running workbench and its ledgers.
    paths = {
        "POSTER_DATA_DIR": str(output / "posters"),
        "POSTER_EXECUTION_DB": str(output / "executions.sqlite3"),
        "POSTER_CHECKPOINT_DB": str(output / "checkpoints.sqlite3"),
    }
    before = {key: os.environ.get(key) for key in paths}
    os.environ.update(paths)
    try:
        yield
    finally:
        for key, value in before.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def evaluate_directory(
    images: Path,
    output_dir: Path,
    *,
    price: str,
    apply: bool = False,
    min_images: int = 20,
    cutout: bool = False,
    ocr: bool = False,
    det_model_dir: Path | None = None,
    rec_model_dir: Path | None = None,
) -> dict:
    if Path(output_dir).expanduser().is_symlink():
        raise ValueError("输出目录不能是符号链接")
    output = Path(output_dir).expanduser().resolve()
    if output.exists():
        raise ValueError("输出目录已存在，请指定新目录；已有证据不会被覆盖")
    items = _scan(Path(images).expanduser(), output, price, min_images)
    report = {
        **_plan(items),
        "mode": "apply" if apply else "check",
        "status": "TECHNICAL_READY",
        "expected_output_count": 2 * len(items),
        "generated_sku_count": 0,
        "valid_output_count": 0,
        "ocr_status": "NOT_RUN",
        "cutout_status": "NOT_RUN",
        "outputs": [],
    }
    if not apply:
        return report
    if ocr and (
        det_model_dir is None
        or rec_model_dir is None
        or not det_model_dir.is_dir()
        or not rec_model_dir.is_dir()
    ):
        raise ValueError("运行 OCR 需要预下载的本地检测及识别模型目录")
    output.mkdir(parents=True, exist_ok=False)
    try:
        _write_inputs(items, output / "inputs")
        manifest = output / "inputs/manifest.csv"
        if cutout:
            result = prepare_cohort(
                manifest,
                output / "inputs/sku_registry.csv",
                output / "cutouts",
                apply=True,
                min_skus=min_images,
                purpose="technical_evaluation",
            )
            report["cutout_status"] = result["status"]
            manifest = output / "cutouts/manifest_with_cutouts.csv"
        with _local_storage(output):
            batch = run_batch(manifest, output / "batch", apply=True)
            report["generated_sku_count"] = batch["created_or_reused"]
            package = output / "m6_review_package"
            prepare_package(output / "batch/batch_import_results.json", package, apply=True)
            for row in csv.DictReader((package / "m6_output_reviews.csv").open(encoding="utf-8")):
                path = package / row["output_image_path"] if row["output_image_path"] else None
                valid = False
                if path and path.is_file():
                    raw = path.read_bytes()
                    expected = (1024, 1024) if row["variant_id"] == "square" else (1080, 1350)
                    with Image.open(io.BytesIO(raw)) as image:
                        image.load()
                        valid = (
                            image.format == "PNG"
                            and image.size == expected
                            and _sha(raw) == row["output_image_sha256"]
                        )
                report["outputs"].append(
                    {
                        "sku_id": row["sku_id"],
                        "variant_id": row["variant_id"],
                        "dimensions_and_hash": "PASS" if valid else "FAIL",
                    }
                )
            report["valid_output_count"] = sum(
                x["dimensions_and_hash"] == "PASS" for x in report["outputs"]
            )
            if ocr:
                from scripts.capture_m6_paddle_ocr import capture

                observed = capture(package, det_model_dir, rec_model_dir)
                report["ocr_status"] = "CAPTURED_REVIEW_REQUIRED"
                report["ocr_captured_output_count"] = observed["captured_variant_count"]
                report["ocr_evidence_file"] = "m6_review_package/m6_paddle_ocr_evidence.json"
            report["status"] = (
                "TECHNICAL_CHECKS_COMPLETED"
                if report["valid_output_count"] == report["expected_output_count"]
                else "TECHNICAL_CHECKS_FAILED"
            )
    except Exception as exc:
        report["status"] = "INCOMPLETE"
        report["error_type"] = type(exc).__name__
        raise
    finally:
        (output / "technical_report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Scan images and run local technical checks; no authorization form required"
    )
    parser.add_argument("--images", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument(
        "--price", required=True, help="Explicit test price, not an inferred product price"
    )
    parser.add_argument("--apply", action="store_true")
    parser.add_argument(
        "--cutout", action="store_true", help="Run the configured local u2netp model"
    )
    parser.add_argument(
        "--ocr", action="store_true", help="Run local PaddleOCR on both output formats"
    )
    parser.add_argument("--det-model-dir", type=Path)
    parser.add_argument("--rec-model-dir", type=Path)
    args = parser.parse_args()
    try:
        result = evaluate_directory(
            args.images,
            args.output_dir,
            price=args.price,
            apply=args.apply,
            cutout=args.cutout,
            ocr=args.ocr,
            det_model_dir=args.det_model_dir,
            rec_model_dir=args.rec_model_dir,
        )
    except (ValueError, OSError, RuntimeError) as exc:
        parser.exit(2, f"技术评测未完成：{exc}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] != "TECHNICAL_CHECKS_FAILED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
