"""Operator-invoked, offline-only, resumable u2netp trial for authorized SKU cohorts.

The default is read-only. --apply runs the existing SHA-pinned rembg CLI once
per unfinished SKU, writing a new private output tree. Every completed output
is hashed and verified before resume. No automatic human/model attestation:
this journal is an operator-owned local observation and requires independent
M6 checks on at least 20 real, authorized SKUs.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import io
import json
import os
import platform
import re
import uuid
from pathlib import Path

from fastapi import UploadFile
from PIL import Image

from app.cutout_quality import evidence_from_pngs
from app.cutout_service import local_model_identity, local_preview
from app.main import read_upload
from scripts.real_sku_batch import preflight
from scripts.validate_m6_cohort import REGISTRY_FIELDS

SCHEMA = "local-u2netp-cohort-1"
RUNNER = "rembg-cli-u2netp"
SHA = re.compile(r"^[a-f0-9]{64}$")


class CutoutCohortError(ValueError):
    """Missing authorization, stale source/model, broken recovery or local inference."""


def _sha(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()


def _read_csv(path: Path, fields: tuple[str, ...] | None = None):
    if path.is_symlink() or not path.is_file():
        raise CutoutCohortError("清单缺失或指向符号链接")
    raw = path.read_bytes()
    if not raw or len(raw) > 2 * 1024 * 1024:
        raise CutoutCohortError("CSV 不能为空或超过 2 MiB")
    try:
        table = csv.DictReader(io.StringIO(raw.decode("utf-8-sig"), newline=""),
                               strict=True)
        header = table.fieldnames
        if (not header or len(header) != len(set(header))
                or (fields is not None and set(header) != set(fields))):
            raise CutoutCohortError("CSV 字段与授权模板不匹配")
        rows = list(table)
        if any(None in row or any(value is None for value in row.values())
               for row in rows):
            raise CutoutCohortError("CSV 行包含重复/缺失字段")
        return header, rows, _sha(raw)
    except (UnicodeError, csv.Error) as exc:
        raise CutoutCohortError("CSV 编码或记录格式错误") from exc


def _sources(manifest: Path, registry: Path, *, min_skus: int):
    if min_skus < 1:
        raise CutoutCohortError("min_skus 需要正整数")
    # The existing M5g preflight enforces 1–100 unique SKUs, path containment,
    # pixel caps, hashes and PosterSpec/layout safety in the same order.
    source_sha, prepared = preflight(manifest)
    if len(prepared) < min_skus:
        raise CutoutCohortError(f"至少需要 {min_skus} 个独立 SKU（当前 {len(prepared)}）")
    manifest_header, source_rows, same_sha = _read_csv(manifest)
    if source_sha != same_sha or len(source_rows) != len(prepared):
        raise CutoutCohortError("生成清单在预检后发生变化")
    if any(row.get("product_cutout_image", "").strip() for row in source_rows):
        raise CutoutCohortError("本地推理输入必须是尚未抠图的原始 SKU 清单")
    registry_header, authorized, registry_sha = _read_csv(registry, REGISTRY_FIELDS)
    if len(authorized) != len(prepared):
        raise CutoutCohortError("授权登记表必须每个 SKU 恰好一行")
    rows_by_sku = {}
    cohort_ids = set()
    source_hashes = set()
    for row in authorized:
        sku = row["sku_id"]
        if sku in rows_by_sku:
            raise CutoutCohortError("授权登记表存在重复 SKU")
        rows_by_sku[sku] = row
        cohort_ids.add(row["cohort_id"])
    if set(rows_by_sku) != {x["sku_id"] for x in prepared}:
        raise CutoutCohortError("授权登记 SKU 与原图 SKU 集合不一致")
    if len(cohort_ids) != 1 or not next(iter(cohort_ids)):
        raise CutoutCohortError("授权登记表 cohort_id 必须一致且非空")
    for item in prepared:
        sku = item["sku_id"]
        reg = rows_by_sku[sku]
        if (reg["source_asset_authorization_status"] != "AUTHORIZED"
                or not reg["authorization_evidence_ref"]
                or reg["distinct_sku_verified"] != "YES"
                or reg["duplicate_source_image_reviewed"] != "YES"
                or not reg["reviewer_alias"]):
            raise CutoutCohortError(f"{sku}: 商品素材授权或独立 SKU 人工登记不完整")
        if reg["source_image_sha256"] != item["product_sha256"]:
            raise CutoutCohortError(f"{sku}: 授权登记源图 SHA-256 与输入不一致")
        if (item["logo"] is not None and reg["logo_authorization_status"] != "AUTHORIZED"
                or item["logo"] is None
                and reg["logo_authorization_status"] != "NOT_APPLICABLE"):
            raise CutoutCohortError(f"{sku}: Logo 授权声明与输入资源不一致")
        if item["product_sha256"] in source_hashes:
            raise CutoutCohortError("不同 SKU 使用重复原图；请核对实际独立商品身份")
        source_hashes.add(item["product_sha256"])
    return {
        "manifest_sha256": source_sha,
        "registry_sha256": registry_sha,
        "cohort_id": next(iter(cohort_ids)),
        "rows": source_rows,
        "header": manifest_header,
        "prepared": prepared,
    }


def _safe_dir(directory: Path):
    if (directory.is_symlink()
            or (directory.exists() and not directory.is_dir())):
        raise CutoutCohortError("输出路径为符号链接或非目录")
    if directory.exists() and (directory / "cutout_trial.json").is_symlink():
        raise CutoutCohortError("本地运行账本不得使用符号链接")


def _atomic_json(path: Path, value: dict) -> None:
    pending = path.with_name("." + path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with pending.open("xb") as handle:
            handle.write((json.dumps(value, ensure_ascii=False, indent=2)
                          + "\n").encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(pending, path)
    finally:
        pending.unlink(missing_ok=True)


def _store_once(path: Path, data: bytes):
    if path.is_symlink():
        raise CutoutCohortError("输出资源不能使用符号链接")
    if path.exists():
        if path.read_bytes() != data:
            raise CutoutCohortError("已有源图或输出文件摘要发生变化")
        return
    with path.open("xb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def _source_png(raw: bytes, filename: str) -> tuple[Image.Image, bytes]:
    image = asyncio.run(read_upload(
        UploadFile(file=io.BytesIO(raw), filename=filename)
    ))
    picture = io.BytesIO()
    image.save(picture, format="PNG")
    return image, picture.getvalue()


def _check_done(out: Path, item: dict, row: dict) -> None:
    sku = item["sku_id"]
    if row.get("status") != "COMPLETED":
        raise CutoutCohortError(f"{sku}: 运行记录尚未完成")
    expected_sha = item["product_sha256"]
    cutout = out / "cutouts" / f"{sku}.png"
    if (cutout.is_symlink() or not cutout.is_file()
            or not SHA.fullmatch(str(row.get("output_sha256", "")))
            or _sha(cutout.read_bytes()) != row["output_sha256"]
            or row.get("source_sha256") != expected_sha):
        raise CutoutCohortError(f"{sku}: 输出文件摘要不一致或已丢失")
    original = item["product"].read_bytes()
    if _sha(original) != expected_sha:
        raise CutoutCohortError(f"{sku}: 原始输入文件摘要发生变化")
    _, normalized = _source_png(original, item["product"].name)
    try:
        computed, _ = evidence_from_pngs(normalized, cutout.read_bytes())
    except (OSError, ValueError) as exc:
        raise CutoutCohortError(f"{sku}: 输出 PNG 文件无效") from exc
    if (row.get("normalized_source_sha256") != computed["source_png_sha256"]
            or row.get("metrics") != computed):
        raise CutoutCohortError(f"{sku}: 当前输出与记录的像素指标不一致")


def _manifest_bytes(source: dict) -> bytes:
    output_header = list(source["header"])
    if "product_cutout_image" not in output_header:
        output_header.append("product_cutout_image")
    data = io.StringIO(newline="")
    writer = csv.DictWriter(data, fieldnames=output_header,
                            lineterminator="\n", extrasaction="raise")
    writer.writeheader()
    for item, original in zip(source["prepared"], source["rows"], strict=True):
        sku = item["sku_id"]
        row = dict(original)
        # Original artifacts are copied to this private output folder
        # because the M5g loader disallows accessing paths outside a manifest.
        row["product_image"] = f"source/{sku}{item['product'].suffix.lower()}"
        if item["logo"] is not None:
            row["logo_image"] = f"source/{sku}-logo{item['logo'].suffix.lower()}"
        row["product_cutout_image"] = f"cutouts/{sku}.png"
        writer.writerow(row)
    return data.getvalue().encode("utf-8")


def _export_manifest(base: Path, source: dict):
    path = base / "manifest_with_cutouts.csv"
    _store_once(path, _manifest_bytes(source))
    _, prepared = preflight(path)
    if len(prepared) != len(source["prepared"]):
        raise CutoutCohortError("导出的 M5g SKU 清单自检失败")
    return _sha(path.read_bytes())


def _result(state: dict, mode: str):
    return {
        "schema_version": SCHEMA,
        "mode": mode,
        "status": state["status"],
        "cohort_sku_count": len(state["expected_sku_ids"]),
        "completed_sku_count": sum(
            r.get("status") == "COMPLETED" for r in state["rows"].values()
        ),
        "model_sha256": state["model_sha256"],
        "model_runtime_attested": False,
        "real_model_quality_verified": False,
        "source_manifest_sha256": state["source_manifest_sha256"],
        "authorization_registry_sha256": state["authorization_registry_sha256"],
    }


def prepare_cohort(
    manifest: Path, registry: Path, output_dir: Path, *, apply: bool = False,
    min_skus: int = 20, retry_failed: bool = False,
) -> dict:
    """Read-only plan or an explicitly started, safe-to-resume local CLI run."""
    manifest = Path(manifest).expanduser()
    registry = Path(registry).expanduser()
    out = Path(output_dir).expanduser()
    source = _sources(manifest, registry, min_skus=min_skus)
    _safe_dir(out)
    if not apply:
        return {
            "schema_version": SCHEMA, "mode": "check", "status": "AUTHORIZED_READY",
            "cohort_sku_count": len(source["prepared"]),
            "authorized_sku_count": len(source["prepared"]),
            "model_runtime_attested": False,
            "source_manifest_sha256": source["manifest_sha256"],
            "authorization_registry_sha256": source["registry_sha256"],
            "output_exists": out.exists(),
        }
    try:
        identity = local_model_identity()
    except (OSError, RuntimeError) as exc:
        raise CutoutCohortError(f"本地模型尚未就绪：{exc}") from exc
    expected_skus = [x["sku_id"] for x in source["prepared"]]
    journal = out / "cutout_trial.json"
    if out.exists():
        if not journal.is_file() or journal.is_symlink():
            raise CutoutCohortError("输出目录已有内容但缺少可信运行账本")
        try:
            state = json.loads(journal.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise CutoutCohortError("已有运行账本损坏") from exc
        if (state.get("schema_version") != SCHEMA
                or state.get("source_manifest_sha256") != source["manifest_sha256"]
                or state.get("authorization_registry_sha256") != source["registry_sha256"]
                or state.get("model_sha256") != identity["model_sha256"]
                or state.get("expected_sku_ids") != expected_skus
                or state.get("cohort_id") != source["cohort_id"]
                or state.get("runner") != RUNNER
                or not isinstance(state.get("rows"), dict)
                or set(state["rows"]) - set(expected_skus)):
            raise CutoutCohortError("已有账本与当前授权清单、模型或 SKU 集合不一致")
    else:
        out.mkdir(parents=True, exist_ok=False)
        (out / "cutouts").mkdir()
        (out / "source").mkdir()
        state = {
            "schema_version": SCHEMA,
            "runner": RUNNER,
            "cohort_id": source["cohort_id"],
            "source_manifest_sha256": source["manifest_sha256"],
            "authorization_registry_sha256": source["registry_sha256"],
            "model_sha256": identity["model_sha256"],
            "model_id": "u2netp",
            "machine": {"os": platform.system(), "architecture": platform.machine(),
                        "python": platform.python_version()},
            "expected_sku_ids": expected_skus, "rows": {},
            "status": "INCOMPLETE", "model_runtime_attested": False,
            "independent_human_review_attested": False,
            "real_model_quality_verified": False,
        }
        _atomic_json(journal, state)

    if (
        (out / "source").is_symlink()
        or (out / "cutouts").is_symlink()
        or not (out / "source").is_dir()
        or not (out / "cutouts").is_dir()
    ):
        raise CutoutCohortError("源图或抠图输出文件夹被更换")
    for item in source["prepared"]:
        sku = item["sku_id"]
        old = state["rows"].get(sku)
        if old and old["status"] == "COMPLETED":
            _check_done(out, item, old)
            continue
        if old and old["status"] == "FAILED" and not retry_failed:
            raise CutoutCohortError(f"{sku}: 上次本地推理失败；请显式使用 --retry-failed")
        src = item["product"].read_bytes()
        if _sha(src) != item["product_sha256"]:
            raise CutoutCohortError(f"{sku}: 商品原图在预检后已更改")
        _store_once(out / "source" / f"{sku}{item['product'].suffix.lower()}", src)
        if item["logo"] is not None:
            logo = item["logo"].read_bytes()
            if _sha(logo) != item["logo_sha256"]:
                raise CutoutCohortError(f"{sku}: Logo 在预检后已更改")
            _store_once(out / "source" / f"{sku}-logo{item['logo'].suffix.lower()}", logo)
        target = out / "cutouts" / f"{sku}.png"
        if target.exists() or target.is_symlink():
            raise CutoutCohortError(f"{sku}: 存在无完整账本的孤立抠图，请人工对账")
        try:
            original, normalized = _source_png(src, item["product"].name)
            candidate = local_preview(original)
            if len(candidate) > 40 * 1024 * 1024:
                raise ValueError("本地模型输出超过 40 MiB")
            metrics, _ = evidence_from_pngs(normalized, candidate)
            _store_once(target, candidate)
            state["rows"][sku] = {
                "status": "COMPLETED", "source_sha256": item["product_sha256"],
                "normalized_source_sha256": metrics["source_png_sha256"],
                "output_sha256": _sha(candidate),
                "metrics": metrics,
                "runner": RUNNER,
                "model_sha256": identity["model_sha256"],
                "execution_observation": "LOCAL_CLI_RESULT_RECORDED_UNATTESTED",
            }
            _atomic_json(journal, state)
        except (OSError, ValueError, RuntimeError) as exc:
            state["rows"][sku] = {
                "status": "FAILED", "source_sha256": item["product_sha256"],
                "error_code": type(exc).__name__,
            }
            state["status"] = "INCOMPLETE"
            _atomic_json(journal, state)
            raise CutoutCohortError(
                f"{sku}: 本地抠图失败（{type(exc).__name__}），需要明确重试"
            ) from exc
    state["export_manifest_sha256"] = _export_manifest(out, source)
    state["status"] = "COMPLETED"
    _atomic_json(journal, state)
    return _result(state, "apply")


def verify_cohort(
    output_dir: Path, manifest: Path, registry: Path, *, min_skus: int = 20,
) -> dict:
    """Re-read all source and output pixels without loading ONNX or contacting network."""
    out = Path(output_dir)
    _safe_dir(out)
    source = _sources(Path(manifest), Path(registry), min_skus=min_skus)
    journal = out / "cutout_trial.json"
    if journal.is_symlink() or not journal.is_file():
        raise CutoutCohortError("本地模型运行账本缺失")
    try:
        state = json.loads(journal.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CutoutCohortError("模型运行账本无效") from exc
    if (state.get("schema_version") != SCHEMA or state.get("runner") != RUNNER
            or state.get("source_manifest_sha256") != source["manifest_sha256"]
            or state.get("authorization_registry_sha256") != source["registry_sha256"]
            or state.get("cohort_id") != source["cohort_id"]
            or state.get("expected_sku_ids") != [x["sku_id"] for x in source["prepared"]]
            or not SHA.fullmatch(str(state.get("model_sha256", "")))
            or state.get("model_runtime_attested") is not False
            or state.get("independent_human_review_attested") is not False
            or state.get("real_model_quality_verified") is not False
            or state.get("status") != "COMPLETED"
            or not isinstance(state.get("rows"), dict)
            or set(state["rows"]) != set(state["expected_sku_ids"])):
        raise CutoutCohortError("本地批次账本身份或完成状态无效")
    for item in source["prepared"]:
        sku = item["sku_id"]
        _check_done(out, item, state["rows"][sku])
        source_file = out / "source" / f"{sku}{item['product'].suffix.lower()}"
        original = item["product"].read_bytes()
        if (source_file.is_symlink() or not source_file.is_file()
                or source_file.read_bytes() != original):
            raise CutoutCohortError(f"{sku}: 导出的原始素材摘要不一致")
        if item["logo"] is not None:
            copy = out / "source" / f"{sku}-logo{item['logo'].suffix.lower()}"
            if (copy.is_symlink() or not copy.is_file()
                    or _sha(copy.read_bytes()) != item["logo_sha256"]):
                raise CutoutCohortError(f"{sku}: 导出的 Logo 素材摘要不一致")
    if (out / "manifest_with_cutouts.csv").is_symlink():
        raise CutoutCohortError("导出的清单为符号链接")
    # Verify is strictly read-only; missing exports stay missing and fail.
    exported = out / "manifest_with_cutouts.csv"
    if (exported.is_symlink() or not exported.is_file()
            or exported.read_bytes() != _manifest_bytes(source)
            or state.get("export_manifest_sha256") != _sha(exported.read_bytes())):
        raise CutoutCohortError("输出清单缺失、被替换或与批次账本不一致")
    try:
        _, exported_rows = preflight(exported)
    except (OSError, ValueError) as exc:
        raise CutoutCohortError("输出清单未通过下游 SKU 导入预检") from exc
    if len(exported_rows) != len(source["prepared"]):
        raise CutoutCohortError("输出清单的 SKU 集合已更改")
    result = _result(state, "verify")
    result["status"] = "RECORDED_UNATTESTED"
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Private, offline, operator-authorized rembg/u2netp SKU trial")
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--registry", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    try:
        if args.verify:
            result = verify_cohort(args.output_dir, args.manifest, args.registry)
        else:
            result = prepare_cohort(
                args.manifest, args.registry, args.output_dir,
                apply=args.apply, retry_failed=args.retry_failed,
            )
    except (OSError, ValueError, RuntimeError) as exc:
        parser.exit(2, f"本地 u2netp 批次未完成：{exc}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
