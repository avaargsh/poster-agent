"""Explicit, model-preloaded, local rembg preview for merchandise images.

The only supported model is the local u2netp ONNX file. The CLI is an opt-in
operator-installed dependency; no implicit model download is initiated by
Poster Agent. The rembg CLI performs the inference in a bounded child process.
"""
from __future__ import annotations

import hashlib
import io
import os
import re
import threading
from pathlib import Path
import shutil
import subprocess
import tempfile

from PIL import Image


class CutoutUnavailable(RuntimeError):
    """Required optional local runtime or operator-prepared model is missing."""


class CutoutRejected(ValueError):
    """An inferred or user-provided candidate is invalid or unusable."""


class CutoutExecutionError(RuntimeError):
    """The local CLI process failed, timed out, or returned corrupt output."""


# One local CPU model preview at a time; a second request is rejected.
_CUTOUT_SLOT = threading.BoundedSemaphore(1)


def verify_candidate(original: Image.Image, candidate: Image.Image) -> Image.Image:
    """Validate a non-empty mask and preserve product pixel geometry."""
    if candidate.size != original.size:
        raise CutoutRejected("抠图候选尺寸与原始商品图不一致")
    if candidate.mode != "RGBA":
        candidate = candidate.convert("RGBA")
    low, high = candidate.getchannel("A").getextrema()
    if low > 0 or high < 255:
        raise CutoutRejected("抠图候选没有透明背景或商品主体")
    alpha = candidate.getchannel("A")
    # Basic damage guard: never replace a product with an almost empty mask.
    thumbnail = alpha.resize((64, 64), Image.Resampling.BOX)
    # L-mode alpha pixels are already one byte each; avoid Pillow's
    # deprecated Image.getdata() (removed in the upcoming Pillow release).
    values = thumbnail.tobytes()
    coverage = sum(v > 96 for v in values) / len(values)
    if not 0.01 <= coverage <= 0.98:
        raise CutoutRejected("抠图候选主体面积异常，请检查边缘并保留原图")
    return candidate


def local_model_identity() -> dict[str, str]:
    """Validate the exact operator-pinned ONNX and local CLI before any batch write.

    This is a local configuration integrity check, not independent execution
    attestation or a claim that real model output quality was human reviewed.
    """
    home_raw = os.getenv("POSTER_CUTOUT_REMBG_HOME", "").strip()
    if not home_raw:
        raise CutoutUnavailable("请先设置 POSTER_CUTOUT_REMBG_HOME 并预装 u2netp 模型")
    home = Path(home_raw).expanduser().resolve()
    model = home / "models" / "u2netp" / "u2netp.onnx"
    if (not model.is_file() or model.is_symlink()
            or model.stat().st_size == 0):
        raise CutoutUnavailable("找不到预装的 u2netp.onnx 本地模型，已停止抠图")
    expected_sha = os.getenv("POSTER_CUTOUT_MODEL_SHA256", "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", expected_sha):
        raise CutoutUnavailable("请先配置预装模型的 POSTER_CUTOUT_MODEL_SHA256")
    with model.open("rb") as handle:
        digest = hashlib.file_digest(handle, "sha256").hexdigest()
    if digest != expected_sha:
        raise CutoutUnavailable("模型 SHA-256 与固定值不一致，拒绝启动本地推理")
    executable = shutil.which("rembg")
    if not executable:
        raise CutoutUnavailable("未安装可执行的 rembg CLI，请在独立环境中安装 rembg[cpu,cli]")
    return {
        "model_sha256": digest,
        "model_id": "u2netp",
        "executable": executable,
    }


def local_preview(original: Image.Image) -> bytes:
    """Return transparent PNG; no asset mutation, no cloud backend fallback."""
    identity = local_model_identity()
    home = Path(os.environ["POSTER_CUTOUT_REMBG_HOME"]).expanduser().resolve()
    if not _CUTOUT_SLOT.acquire(blocking=False):
        raise CutoutUnavailable("已有本地抠图请求运行，请稍后重试")
    try:
        return _execute_preview(original, home, identity["executable"])
    finally:
        _CUTOUT_SLOT.release()


def _execute_preview(original: Image.Image, home: Path, executable: str) -> bytes:
    with tempfile.TemporaryDirectory(prefix="poster-cutout-") as directory:
        source = Path(directory) / "original.png"
        target = Path(directory) / "cutout.png"
        original.save(source, format="PNG")
        env = {
            **os.environ, "REMBG_HOME": str(home),
            # Integrity was already checked against a pinned, operator-set
            # SHA-256. Tell rembg to reuse the local ONNX without re-fetch.
            "MODEL_CHECKSUM_DISABLED": "1",
            # Route standard HTTP clients to an unreachable local endpoint
            # rather than allow accidental first-run model downloads.
            "HTTP_PROXY": "http://127.0.0.1:9",
            "HTTPS_PROXY": "http://127.0.0.1:9",
            "ALL_PROXY": "http://127.0.0.1:9",
            "http_proxy": "http://127.0.0.1:9",
            "https_proxy": "http://127.0.0.1:9",
            "all_proxy": "http://127.0.0.1:9",
            "NO_PROXY": "", "no_proxy": "",
        }
        command = [executable, "i", "-m", "u2netp", str(source), str(target)]
        try:
            result = subprocess.run(command, env=env, timeout=60,
                                    capture_output=True, check=False)
        except (subprocess.TimeoutExpired, OSError) as exc:
            raise CutoutExecutionError("本地背景分离执行失败或超过 60 秒") from exc
        if result.returncode != 0 or not target.is_file():
            raise CutoutExecutionError("本地背景分离失败，请检查 rembg 环境与模型")
        if target.stat().st_size > 40 * 1024 * 1024:
            raise CutoutRejected("抠图输出过大，请检查输入")
        try:
            with Image.open(target) as image:
                if image.format != "PNG" or image.size != original.size:
                    raise CutoutRejected("抠图输出格式或尺寸异常")
                image.load()
                if image.mode != "RGBA":
                    raise CutoutRejected("抠图结果缺少透明通道")
                validated = verify_candidate(original, image)
                output = io.BytesIO()
                validated.save(output, format="PNG")
                return output.getvalue()
        except (OSError, Image.DecompressionBombError) as exc:
            raise CutoutRejected("本地模型返回了损坏的 PNG") from exc


def png_sha256(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()
