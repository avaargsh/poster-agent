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
from io import BytesIO
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
_MAX_IMAGE_BYTES = 24 * 1024 * 1024
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


def _parse_finite_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError("non-finite JSON number")
    return parsed


def _loads_strict(raw: bytes | str, *, max_bytes: int) -> object:
    if isinstance(raw, bytes):
        if len(raw) > max_bytes:
            raise EvidenceInvalid("JSON evidence exceeds size limit")
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            raise EvidenceInvalid("JSON evidence is not valid UTF-8") from None
    elif isinstance(raw, str):
        try:
            encoded_size = len(raw.encode("utf-8"))
        except UnicodeEncodeError:
            raise EvidenceInvalid("JSON evidence is not valid UTF-8 text") from None
        if len(raw) > max_bytes or encoded_size > max_bytes:
            raise EvidenceInvalid("JSON evidence exceeds size limit")
        text = raw
    else:
        raise EvidenceInvalid("JSON evidence must be UTF-8 text")
    try:
        return json.loads(
            text,
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_non_finite_json,
            parse_float=_parse_finite_float,
        )
    except (json.JSONDecodeError, ValueError, RecursionError):
        raise EvidenceInvalid("JSON evidence is malformed or ambiguous") from None


def _read_bounded_bytes(path: Path, *, max_bytes: int, label: str) -> bytes:
    try:
        if path.stat().st_size > max_bytes:
            raise EvidenceInvalid(f"{label} exceeds size limit")
        raw = path.read_bytes()
    except OSError:
        raise EvidenceInvalid(f"{label} is missing or unreadable") from None
    if len(raw) > max_bytes:
        raise EvidenceInvalid(f"{label} exceeds size limit")
    return raw


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _valid_bbox(box: object, *, width: int, height: int) -> bool:
    if not isinstance(box, list) or len(box) != 4:
        return False
    if any(type(value) is not int for value in box):
        return False
    x1, y1, x2, y2 = box
    return 0 <= x1 < x2 <= width and 0 <= y1 < y2 <= height


def _image_dimensions(raw: bytes, *, require_png: bool) -> tuple[int, int, str, int]:
    raw_size = len(raw)
    if raw_size < 1 or raw_size > _MAX_IMAGE_BYTES:
        raise EvidenceInvalid("OCR 输入图像大小超出允许范围")
    try:
        with Image.open(BytesIO(raw)) as source:
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
    for row in rows:
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


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == SHA_LENGTH
        and all(char in "0123456789abcdef" for char in value)
    )


def _valid_duration(value: object) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(value)
        and value >= 0
    )


def verify_probe(probe_file: Path, image_file: Path, expected_platform: str | None = None) -> dict:
    probe_raw = _read_bounded_bytes(probe_file, max_bytes=_MAX_JSON_BYTES, label="probe JSON")
    probe = _loads_strict(probe_raw, max_bytes=_MAX_JSON_BYTES)
    if not isinstance(probe, dict):
        raise EvidenceInvalid("probe JSON must be an object")
    environment = probe.get("environment")
    if not isinstance(environment, dict):
        raise EvidenceInvalid("缺少有效的推理环境记录")
    if probe.get("engine") != "PaddleOCR":
        raise EvidenceInvalid("推理引擎标识不匹配")
    if probe.get("mode") != "inference" or probe.get("status") != "OK":
        raise EvidenceInvalid("不是成功的真实推理记录：mode=inference 且 status=OK 才可验收")
    if probe.get("model_invocation_completed") is not True:
        raise EvidenceInvalid("模型调用没有完成")
    for field in (
        "paddlepaddle_distribution",
        "paddleocr_distribution",
        "platform",
        "machine",
        "python",
    ):
        if not isinstance(environment.get(field), str) or not environment[field]:
            raise EvidenceInvalid(f"缺少有效的环境字段：{field}")
    if (
        environment.get("paddle_module_detected") is not True
        or environment.get("paddleocr_module_detected") is not True
    ):
        raise EvidenceInvalid("缺少推理依赖的模块检测结果")
    if expected_platform is not None and environment.get("platform") != expected_platform:
        raise EvidenceInvalid("记录的平台与要求的平台不一致")

    image = probe.get("image")
    if not isinstance(image, dict) or not _is_sha256(image.get("sha256")):
        raise EvidenceInvalid("缺少有效的输入图像证据")
    image_bytes = _read_bounded_bytes(
        image_file, max_bytes=_MAX_IMAGE_BYTES, label="OCR input image"
    )
    actual = _sha256(image_bytes)
    if image.get("sha256") != actual:
        raise EvidenceInvalid("实际图像 SHA-256 与探针报告不一致")
    width, height, image_format, raw_size = _image_dimensions(image_bytes, require_png=False)
    reported_size = image.get("pixel_size")
    if (
        not isinstance(reported_size, list)
        or len(reported_size) != 2
        or any(type(value) is not int for value in reported_size)
        or reported_size != [width, height]
        or image.get("format") != image_format
        or type(image.get("bytes")) is not int
        or image["bytes"] != raw_size
    ):
        raise EvidenceInvalid("探针图像格式、字节数或像素尺寸与实际图像不一致")

    rows = probe.get("ocr_lines")
    if not isinstance(rows, list):
        raise EvidenceInvalid("缺少真实 OCR 原始识别行；请使用 --include-text 运行")
    if type(probe.get("line_count")) is not int or probe["line_count"] != len(rows):
        raise EvidenceInvalid("OCR 行数与原始证据不一致")
    valid_count = _validate_ocr_rows(rows, width=width, height=height, context="probe")
    duration = probe.get("elapsed_ms")
    if not _valid_duration(duration):
        raise EvidenceInvalid("缺少合法推理耗时")
    return {
        "probe_sha256": _sha256(probe_raw),
        "input_image_sha256": actual,
        "platform": environment["platform"],
        "machine": environment["machine"],
        "python": environment["python"],
        "paddlepaddle": environment["paddlepaddle_distribution"],
        "paddleocr": environment["paddleocr_distribution"],
        "ocr_lines": valid_count,
        "valid_bboxes": valid_count,
        "elapsed_ms": duration,
    }


def verify_batch(batch_dir: Path) -> dict:
    root = batch_dir / "images"
    manifest_path = root / "manifest.json"
    report_file = batch_dir / "image_benchmark_summary.json"
    observations_file = batch_dir / "ocr_observations.jsonl"

    manifest_raw = _read_bounded_bytes(
        manifest_path, max_bytes=_MAX_JSON_BYTES, label="image manifest"
    )
    manifest_data = _loads_strict(manifest_raw, max_bytes=_MAX_JSON_BYTES)
    if (
        not isinstance(manifest_data, dict)
        or set(manifest_data)
        != {
            "dataset_id",
            "dataset_kind",
            "caveat",
            "expected_spec",
            "target_fields",
            "fixtures",
        }
        or not isinstance(manifest_data.get("fixtures"), list)
    ):
        raise EvidenceInvalid("image manifest schema is invalid")
    try:
        manifest = ImageManifest.model_validate(manifest_data)
    except ValidationError:
        raise EvidenceInvalid("image manifest schema is invalid") from None
    if any(
        not isinstance(fixture, dict)
        or set(fixture)
        != {
            "case_id",
            "category",
            "image_file",
            "image_sha256",
            "rendered_spec",
            "truth_defects",
        }
        for fixture in manifest_data["fixtures"]
    ):
        raise EvidenceInvalid("image fixture schema is invalid")

    report_raw = _read_bounded_bytes(
        report_file, max_bytes=_MAX_JSON_BYTES, label="benchmark summary"
    )
    report = _loads_strict(report_raw, max_bytes=_MAX_JSON_BYTES)
    if not isinstance(report, dict):
        raise EvidenceInvalid("benchmark summary must be an object")
    if report.get("ocr_mode") != "paddle" or report.get("model_executed") is not True:
        raise EvidenceInvalid("批量报告没有记录 PaddleOCR 完成")
    if report.get("dataset_kind") != "code_rendered_png_fixtures":
        raise EvidenceInvalid("数据集类型与程序渲染 PNG 测试不匹配")
    if report.get("dataset_id") != manifest.dataset_id:
        raise EvidenceInvalid("批量报告与 manifest dataset_id 不一致")
    if report.get("manifest_sha256") != _sha256(manifest_raw):
        raise EvidenceInvalid("批量报告与 manifest 摘要不一致")
    if (
        len(manifest.fixtures) != EXPECTED_FIXTURES
        or type(report.get("fixture_count")) is not int
        or report["fixture_count"] != EXPECTED_FIXTURES
    ):
        raise EvidenceInvalid("批量 PNG 样本数量不足")
    if (
        not isinstance(report.get("paddleocr_package_version"), str)
        or not report["paddleocr_package_version"]
        or report.get("ocr_observations_file") != "ocr_observations.jsonl"
        or not isinstance(report.get("case_metrics"), dict)
        or not isinstance(report.get("field_metrics"), dict)
        or not isinstance(report.get("by_category"), dict)
    ):
        raise EvidenceInvalid("批量报告缺少模型版本或指标摘要")

    observations_raw = _read_bounded_bytes(
        observations_file, max_bytes=_MAX_JSONL_BYTES, label="OCR observations JSONL"
    )
    try:
        lines = observations_raw.decode("utf-8").splitlines()
    except UnicodeDecodeError:
        raise EvidenceInvalid("OCR observations JSONL is not valid UTF-8") from None
    if len(lines) != EXPECTED_FIXTURES:
        raise EvidenceInvalid("逐图 OCR 观察行数不等于 20")
    observations = [_parse_observation(line) for line in lines]
    if any(not isinstance(item.get("case_id"), str) for item in observations):
        raise EvidenceInvalid("逐图 OCR 记录缺少有效 case_id")
    expected_ids = {fixture.case_id for fixture in manifest.fixtures}
    observation_by_id = {item["case_id"]: item for item in observations}
    if len(observation_by_id) != EXPECTED_FIXTURES or set(observation_by_id) != expected_ids:
        raise EvidenceInvalid("逐图 OCR 记录缺失、重复或包含未知 case_id")

    for fixture in manifest.fixtures:
        observation = observation_by_id[fixture.case_id]
        if set(observation) != {"case_id", "image_sha256", "elapsed_ms", "ocr_lines"}:
            raise EvidenceInvalid(f"{fixture.case_id} OCR observation schema is invalid")
        if not _is_sha256(fixture.image_sha256):
            raise EvidenceInvalid(f"{fixture.case_id} manifest image SHA-256 is invalid")
        image_path = root / fixture.image_file
        image_bytes = _read_bounded_bytes(
            image_path, max_bytes=_MAX_IMAGE_BYTES, label=f"{fixture.case_id} PNG"
        )
        actual = _sha256(image_bytes)
        if actual != fixture.image_sha256:
            raise EvidenceInvalid(f"{fixture.case_id} PNG SHA-256 与 manifest 不一致")
        if observation.get("image_sha256") != actual:
            raise EvidenceInvalid(f"{fixture.case_id} OCR 记录与输入图像哈希不一致")
        width, height, _, _ = _image_dimensions(image_bytes, require_png=True)
        if not _valid_duration(observation.get("elapsed_ms")):
            raise EvidenceInvalid(f"{fixture.case_id} OCR elapsed_ms is invalid")
        _validate_ocr_rows(
            observation.get("ocr_lines"),
            width=width,
            height=height,
            context=fixture.case_id,
        )

    return {
        "dataset_kind": manifest.dataset_kind,
        "fixture_count": EXPECTED_FIXTURES,
        "manifest_sha256": _sha256(manifest_raw),
        "report_sha256": _sha256(report_raw),
        "observation_records": len(observations),
        "case_metrics": report["case_metrics"],
        "field_metrics": report["field_metrics"],
    }


def _parse_observation(line: str) -> dict:
    parsed = _loads_strict(line, max_bytes=_MAX_JSONL_LINE_BYTES)
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
