"""Read-only M14 local cutout -> M5g poster -> M6 review lineage verification.

Replays existing per-stage integrity gates and joins every SKU by its pinned
source and candidate bytes, saved poster v1 receipt, review image and export.
A successful result proves local consistency of untrusted observations only:
it never grants publishing rights, model runtime attestation or human approval.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
from pathlib import Path

from PIL import Image

from app import storage
from app.batch_artifact_integrity import verify_batch_artifact
from app.channel_export_routes import download_channel_export
from app.m6_package_download_routes import _read_review_rows
from scripts.local_cutout_cohort import verify_cohort
from scripts.real_sku_batch import preflight
from scripts.verify_m6_cutout_evidence import verify_cutout_evidence

SCHEMA = "m14-m5g-m6-lineage-1"


class LineageError(ValueError):
    """Incomplete or divergent source, candidate, poster or review evidence."""


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _bytes(path: Path, limit: int = 40 * 1024 * 1024) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise LineageError("来源证据文件缺失或使用了符号链接")
    size = path.stat().st_size
    if size <= 0 or size > limit:
        raise LineageError("来源证据文件为空或超出大小限制")
    return path.read_bytes()


def _png(path: Path, size: tuple[int, int], digest: str) -> None:
    raw = _bytes(path, 20 * 1024 * 1024)
    if _sha(raw) != digest:
        raise LineageError("M6 输出图片 SHA-256 与审核表不一致")
    try:
        with Image.open(io.BytesIO(raw)) as picture:
            picture.load()
            if picture.format != "PNG" or picture.size != size:
                raise LineageError("M6 输出图片类型或版式尺寸无效")
    except (OSError, ValueError) as exc:
        raise LineageError("M6 输出图片损坏或尺寸无效") from exc


def _m6_review_rows(package: Path, expected: dict[str, str], cohort: str):
    raw = _bytes(package / "m6_output_reviews.csv", 2 * 1024 * 1024)
    # Reuse the existing ZIP exporter registry/variant membership gate.
    try:
        return _read_review_rows(raw, expected, cohort), _sha(raw)
    except (ValueError, OSError) as exc:
        raise LineageError("M6 审核表 SKU×版式集合或版本字段无效") from exc


def verify_lineage(
    trial_dir: Path, source_manifest: Path, registry: Path,
    m5g_journal: Path, m6_package: Path, *, min_skus: int = 20,
) -> dict:
    """Cross-check all three frozen stages without writing files or running models."""
    trial_dir = Path(trial_dir)
    m5g_journal = Path(m5g_journal)
    m6_package = Path(m6_package)
    if any(p.is_symlink() or not p.is_dir() for p in (trial_dir, m6_package)):
        raise LineageError("M14 或 M6 来源目录不存在或为符号链接")
    try:
        trial = verify_cohort(trial_dir, source_manifest, registry, min_skus=min_skus)
        cutout_m6 = verify_cutout_evidence(
            m6_package, m5g_journal, min_skus=min_skus
        )
        trial_journal_raw = _bytes(trial_dir / "cutout_trial.json", 2 * 1024 * 1024)
        trial_journal = json.loads(trial_journal_raw)
        m5g_raw = _bytes(m5g_journal, 2 * 1024 * 1024)
        batch = json.loads(m5g_raw)
        m6_summary = json.loads(_bytes(
            m6_package / "m6_preparation.json", 2 * 1024 * 1024
        ))
        exported = trial_dir / "manifest_with_cutouts.csv"
        exported_raw = _bytes(exported, 2 * 1024 * 1024)
        _, staged_items = preflight(exported)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        if isinstance(exc, LineageError):
            raise
        raise LineageError("M14、M5g 或 M6 来源验证失败：" + str(exc)) from exc

    if (trial["status"] != "RECORDED_UNATTESTED"
            or batch.get("schema_version") != "m5g-1"
            or batch.get("mode") != "apply"
            or batch.get("completed") is not True
            or batch.get("manifest_sha256") != _sha(exported_raw)
            or batch.get("expected_sku_ids") != trial_journal["expected_sku_ids"]
            or batch.get("expected_sku_count") != len(staged_items)
            or set(batch.get("rows", {})) != {x["sku_id"] for x in staged_items}
            or m6_summary.get("source_batch_journal_sha256") != _sha(m5g_raw)
            or m6_summary.get("source_manifest_sha256") != _sha(exported_raw)
            or cutout_m6["candidate_count"] != len(staged_items)
            or cutout_m6["batch_sku_count"] != len(staged_items)):
        raise LineageError("M14 导出清单与 M5g 批次或 M6 cohort 来源不一致")
    expected = {sku: item["poster_id"] for sku, item in batch["rows"].items()}
    cohort = cutout_m6["cohort_id"]
    m6_rows, reviews_sha = _m6_review_rows(m6_package, expected, cohort)
    by_key = {(r["sku_id"], r["variant_id"]): r for r in m6_rows}
    pairs = []
    for item in staged_items:
        sku = item["sku_id"]
        record = batch["rows"][sku]
        trail = trial_journal["rows"].get(sku)
        pid = record["poster_id"]
        if (not isinstance(trail, dict) or trail.get("status") != "COMPLETED"
                or not item["cutout"] or record.get("version") != 1
                or record.get("request_sha256") != item["request_sha256"]
                or record.get("product_sha256") != item["product_sha256"]
                or record.get("product_cutout_sha256") != item["cutout_sha256"]
                or trail.get("source_sha256") != item["product_sha256"]
                or trail.get("output_sha256") != item["cutout_sha256"]
                or trail.get("model_sha256") != trial_journal["model_sha256"]):
            raise LineageError(f"{sku}: M14 来源抠图与 M5g 批次账本不一致")
        try:
            verify_batch_artifact(pid, record)
            latest_source = storage.poster_dir(pid) / "assets" / "product.png"
            latest_cutout = storage.poster_dir(pid) / "assets" / "product_cutout.png"
            if (_sha(_bytes(latest_source)) != trail["normalized_source_sha256"]
                    or _sha(_bytes(latest_cutout)) != trail["output_sha256"]):
                raise LineageError(f"{sku}: M5g 已保存素材与本地 ONNX 试验哈希不一致")
        except (OSError, ValueError) as exc:
            if isinstance(exc, LineageError):
                raise
            raise LineageError(f"{sku}: M5g 已保存海报和执行凭据校验失败") from exc

        for variant, suffix, dims in (
            ("square", "square", (1024, 1024)),
            ("feed_4x5_native", "native-4x5", (1080, 1350)),
        ):
            row = by_key[(sku, variant)]
            name = f"images/{sku}-{suffix}.png"
            if (row["cohort_id"] != cohort
                    or row["poster_id"] != pid
                    or row["source_version"] != "1"
                    or row["source_product_sha256"] != record["product_sha256"]
                    or row["request_sha256"] != record["request_sha256"]
                    or row["output_image_path"] != name
                    or not row["output_image_sha256"]
                    or row["overall_accept"] not in ("PASS", "FAIL", "UNVERIFIED")):
                raise LineageError(f"{sku}: M6 {variant} 版式来源或审核标签无效")
            _png(m6_package / name, dims, row["output_image_sha256"])
            # Compare against immutable underlying render/export bytes, not
            # only hashes a local operator could update in the review CSV.
            if variant == "square":
                expected_bytes = storage.version_path(pid, 1).read_bytes()
            else:
                response = download_channel_export(pid, 1, variant)
                if response.status_code != 200:
                    raise LineageError(f"{sku}: 4:5 原生重新导出失败")
                expected_bytes = response.body
            if _sha(expected_bytes) != row["output_image_sha256"]:
                raise LineageError(f"{sku}: M6 {variant} 图片被替换或与冻结渲染不一致")
        pairs.append({
            "sku_id": sku, "poster_id": pid, "version": 1,
            "source_sha256": item["product_sha256"],
            "cutout_sha256": item["cutout_sha256"],
            "model_sha256": trial_journal["model_sha256"],
            "source_matched": True, "cutout_matched": True,
            "m6_review_images_matched": True,
        })

    status = (
        "LINEAGE_VERIFIED_REVIEW_PENDING"
        if cutout_m6["status"] == "HUMAN_REVIEW_REQUIRED" else
        "LINEAGE_VERIFIED_OPERATOR_ASSERTED"
        if cutout_m6["status"] == "OPERATOR_ASSERTED_NOT_ATTESTED" else
        "LINEAGE_VERIFIED_OPERATOR_REJECTED"
    )
    return {
        "schema_version": SCHEMA,
        "status": status,
        "source_cohort_sku_count": len(staged_items),
        "linked_sku_count": len(pairs),
        "linked_versions": len(pairs),
        "trial_journal_sha256": _sha(trial_journal_raw),
        "m5g_journal_sha256": _sha(m5g_raw),
        "m6_reviews_sha256": reviews_sha,
        "m6_cutout_reviews_sha256": cutout_m6["reviews_csv_sha256"],
        "model_sha256": trial_journal["model_sha256"],
        "model_runtime_attested": False,
        "independent_review_attested": False,
        "quality_accepted": False,
        "paid_model_calls": 0,
        "items": pairs,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Cross-check M14 cutout, M5g posters and M6 review evidence"
    )
    parser.add_argument("--trial-dir", required=True, type=Path)
    parser.add_argument("--source-manifest", required=True, type=Path)
    parser.add_argument("--registry", required=True, type=Path)
    parser.add_argument("--m5g-journal", required=True, type=Path)
    parser.add_argument("--m6-package", required=True, type=Path)
    args = parser.parse_args()
    try:
        result = verify_lineage(
            args.trial_dir, args.source_manifest, args.registry,
            args.m5g_journal, args.m6_package,
        )
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(2, f"M14→M5g→M6 证据链验证失败：{exc}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
