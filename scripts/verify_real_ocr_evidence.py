"""Verify an *actual* PaddleOCR probe and optional 20-PNG replay artifact.

This is a provenance sanity checker, not a human-annotated accuracy audit.
It rejects all NOT_RUN / UNAVAILABLE / ERROR states, missing runtime versions,
missing OCR boxes and mismatched image digests. The GitHub workflow and logs
provide external evidence that the real model process was launched.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

from PIL import Image
from pydantic import ValidationError

from evals.image_fixtures import ImageManifest

SHA_LENGTH = 64
EXPECTED_FIXTURES = 20
_MAX_JSON_BYTES = 1024 * 1024
_MAX_JSONL_BYTES = 8 * 1024 * 1024
_MAX_JSONL_LINE_BYTES = 512 * 1024
_MAX_OCR_LINES_PER_IMAGE = 200
_MAX_OCR_TEXT_LENGTH = 200
_MAX_IMAGE_DIMENSION = 4096
_MAX_IMAGE_PIXELS = 8_000_000
_OCR_ROW_FIELDS = {"text", "score", "bbox"}


class EvidenceInvalid(ValueError):
    pass


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict:
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


def _reject_non_finite_json(value: str):
    raise ValueError("non-finite JSON number")


def _loads_strict(raw: bytes | str, *, max_bytes: int) -> object:
    if isinstance(raw, bytes):
        if len(raw) > max_bytes:
            raise EvidenceInvalid("JSON evidence exceeds size limit")
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            raise EvidenceInvalid("JSON evidence is not valid UTF-8") from None
    elif isinstance(raw, str):
        if len(raw) > max_bytes or len(raw.encode("utf-8")) > max_bytes:
            raise EvidenceInvalid("JSON evidence exceeds size limit")
        text = raw
    else:
        raise EvidenceInvalid("JSON evidence must be UTF-8 text")
    try:
        return json.loads(
            text,
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_non_finite_json,
        )
    except (json.JSONDecodeError, ValueError, RecursionError):
        raise EvidenceInvalid("JSON evidence is malformed or ambiguous") from None


def _read_json(path: Path) -> dict:
    try:
        raw = path.read_bytes()
    except OSError:
        raise EvidenceInvalid(f"缺失或无法读取的 JSON 证据：{path.name}") from None
    obj = _loads_strict(raw, max_bytes=_MAX_JSON_BYTES)
    if not isinstance(obj, dict):
        raise EvidenceInvalid("JSON 证据必须为对象")
    return obj


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _valid_bbox(box: object, *, width: int, height: int) -> bool:
    if not isinstance(box, list) or len(box) != 4:
        return False
    if any(type(value) is not int for value in box):
        return False
    x1, y1, x2, y2 = box
    return 0 <= x1 < x2 <= width and 0 <= y1 < y2 <= height


def _image_dimensions(path: Path, *, require_png: bool) -> tuple[int, int, str, int]:
    try:
        raw_size = path.stat().st_size
        with Image.open(path) as source:
            image_format = source.format
            width, height = source.size
            source.verify()
    except (OSError, ValueError, Image.DecompressionBombError):
        raise EvidenceInvalid("OCR 输入图像不是有效的受支持图像") from None
    if (
        (require_png and image_format != "PNG")
        or image_format not in {"PNG", "JPEG", "WEBP"}
        or width < 1
        or height < 1
        or width > _MAX_IMAGE_DIMENSION
        or height > _MAX_IMAGE_DIMENSION
        or width * height > _MAX_IMAGE_PIXELS
    ):
        raise EvidenceInvalid("OCR 输入图像格式或尺寸超出允许范围")
    return width, height, image_format, raw_size


def _validate_ocr_rows(rows: object, *, width: int, height: int, context: str) -> int:
    if not isinstance(rows, list) or not rows or len(rows) > _MAX_OCR_LINES_PER_IMAGE:
        raise EvidenceInvalid(f"{context}: OCR 行数缺失或超出限制")
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or set(row) != _OCR_ROW_FIELDS:
            raise EvidenceInvalid(f"{context}: OCR row schema is invalid")
        text = row["text"]
        if not isinstance(text, str) or not text.strip() or len(text) > _MAX_OCR_TEXT_LENGTH:
            raise EvidenceInvalid(f"{context}: OCR text is invalid")
        score = row["score"]
        if (
            isinstance(score, bool)
            or not isinstance(score, (int, float))
            or not math.isfinite(score)
            or not 0 <= score <= 1
        ):
            raise EvidenceInvalid(f"{context}: OCR score is invalid")
        if not _valid_bbox(row["bbox"], width=width, height=height):
            raise EvidenceInvalid(f"{context}: OCR bbox is missing or outside the image")
    return len(rows)


def verify_probe(probe_file: Path, image_file: Path, expected_platform: str | None = None) -> dict:
    probe = _read_json(probe_file)
    environment = probe.get("environment") or {}
    if probe.get("mode") != "inference" or probe.get("status") != "OK":
        raise EvidenceInvalid("不是成功的真实推理记录：mode=inference 且 status=OK 才可验收")
    if probe.get("model_invocation_completed") is not True:
        raise EvidenceInvalid("模型调用没有完成")
    if not environment.get("paddlepaddle_distribution") or not environment.get(
        "paddleocr_distribution"
    ):
        raise EvidenceInvalid("缺少 PaddlePaddle/PaddleOCR 已安装版本证据")
    if not environment.get("paddle_module_detected") or not environment.get(
        "paddleocr_module_detected"
    ):
        raise EvidenceInvalid("缺少推理依赖的模块检测结果")
    if expected_platform is not None and environment.get("platform") != expected_platform:
        raise EvidenceInvalid("记录的平台与要求的平台不一致")
    image = probe.get("image")
    if not isinstance(image, dict):
        raise EvidenceInvalid("缺少有效的输入图像证据")
    try:
        actual = _sha256(image_file.read_bytes())
    except OSError:
        raise EvidenceInvalid("无法读取 OCR 输入图像") from None
    if image.get("sha256") != actual:
        raise EvidenceInvalid("实际 PNG 图片 SHA-256 与探针报告不一致")
    try:
        with Image.open(image_file) as source:
            actual_size = [source.width, source.height]
            source.verify()
    except (OSError, ValueError):
        raise EvidenceInvalid("OCR 输入图像不是有效的受支持图像") from None
    reported_size = image.get("pixel_size")
    if (
        not isinstance(reported_size, list)
        or len(reported_size) != 2
        or any(type(value) is not int for value in reported_size)
        or reported_size != actual_size
    ):
        raise EvidenceInvalid("探针图像尺寸与实际像素尺寸不一致")
    rows = probe.get("ocr_lines")
    if not isinstance(rows, list) or not rows:
        raise EvidenceInvalid("缺少真实 OCR 原始识别行；请使用 --include-text 运行")
    if probe.get("line_count") != len(rows):
        raise EvidenceInvalid("OCR 行数与原始证据不一致")
    width, height = actual_size
    bbox_count = sum(
        _valid_bbox(row.get("bbox"), width=width, height=height)
        for row in rows
        if isinstance(row, dict)
    )
    if bbox_count != len(rows):
        raise EvidenceInvalid("OCR 坐标框缺失、无效或超出实际图像边界，不能形成完整空间证据")
    duration = probe.get("elapsed_ms")
    if (
        isinstance(duration, bool)
        or not isinstance(duration, (int, float))
        or not math.isfinite(duration)
        or duration < 0
    ):
        raise EvidenceInvalid("缺少合法推理耗时")
    return {
        "probe_sha256": _sha256(probe_file.read_bytes()),
        "input_image_sha256": actual,
        "platform": environment["platform"],
        "machine": environment.get("machine"),
        "python": environment.get("python"),
        "paddlepaddle": environment["paddlepaddle_distribution"],
        "paddleocr": environment["paddleocr_distribution"],
        "ocr_lines": len(rows),
        "valid_bboxes": bbox_count,
        "elapsed_ms": duration,
    }


def verify_batch(batch_dir: Path) -> dict:
    root = batch_dir / "images"
    manifest_path = root / "manifest.json"
    report_file = batch_dir / "image_benchmark_summary.json"
    observations_file = batch_dir / "ocr_observations.jsonl"
    manifest = ImageManifest.model_validate_json(manifest_path.read_bytes())
    report = _read_json(report_file)
    if report.get("ocr_mode") != "paddle" or report.get("model_executed") is not True:
        raise EvidenceInvalid("批量报告没有记录 PaddleOCR 完成")
    if report.get("dataset_kind") != "code_rendered_png_fixtures":
        raise EvidenceInvalid("数据集类型与程序渲染 PNG 测试不匹配")
    if report.get("manifest_sha256") != _sha256(manifest_path.read_bytes()):
        raise EvidenceInvalid("批量报告与 manifest 摘要不一致")
    if (
        len(manifest.fixtures) != EXPECTED_FIXTURES
        or report.get("fixture_count") != EXPECTED_FIXTURES
    ):
        raise EvidenceInvalid("批量 PNG 样本数量不足")
    lines = observations_file.read_text(encoding="utf-8").splitlines()
    if len(lines) != EXPECTED_FIXTURES:
        raise EvidenceInvalid("逐图 OCR 观察行数不等于 20")
    observations = [_parse_observation(line) for line in lines]
    observation_by_id = {item.get("case_id"): item for item in observations}
    if len(observation_by_id) != EXPECTED_FIXTURES:
        raise EvidenceInvalid("逐图 OCR 记录缺失或存在重复 case_id")
    for fixture in manifest.fixtures:
        actual = _sha256((root / fixture.image_file).read_bytes())
        if actual != fixture.image_sha256:
            raise EvidenceInvalid(f"{fixture.case_id} PNG SHA-256 与 manifest 不一致")
        if observation_by_id[fixture.case_id].get("image_sha256") != actual:
            raise EvidenceInvalid(f"{fixture.case_id} OCR 记录与输入图像哈希不一致")
        if not isinstance(observation_by_id[fixture.case_id].get("ocr_lines"), list):
            raise EvidenceInvalid(f"{fixture.case_id} OCR 结果缺少行数组")
    return {
        "dataset_kind": manifest.dataset_kind,
        "fixture_count": EXPECTED_FIXTURES,
        "manifest_sha256": _sha256(manifest_path.read_bytes()),
        "report_sha256": _sha256(report_file.read_bytes()),
        "observation_records": len(observations),
        "case_metrics": report["case_metrics"],
        "field_metrics": report["field_metrics"],
    }


def _parse_observation(line: str) -> dict:
    try:
        parsed = json.loads(line)
    except json.JSONDecodeError as exc:
        raise EvidenceInvalid("OCR observations JSONL 不合法") from exc
    if not isinstance(parsed, dict):
        raise EvidenceInvalid("OCR observations 必须是 JSON 对象")
    return parsed


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate true PaddleOCR invocation artifacts")
    parser.add_argument("--probe", type=Path, required=True)
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--expected-platform", default=None)
    parser.add_argument("--benchmark-dir", type=Path, default=None)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    evidence = {
        "source": "real_paddleocr_probe",
        "warning": "Runtime proof is NOT a real-user OCR accuracy study.",
        "probe": verify_probe(args.probe, args.image, args.expected_platform),
        "benchmark": verify_batch(args.benchmark_dir) if args.benchmark_dir else None,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(evidence, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
