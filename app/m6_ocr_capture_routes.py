"""Explicit, local-only PaddleOCR launch for a frozen M6 web batch.

Never accepts paths, commands, models or flags from an HTTP body. Operators
configure existing local OCR model directories through process environment
variables and trigger the already established non-clobber capture function.
The in-memory job state is advisory only and is lost on server restart.
"""
from __future__ import annotations

import importlib.metadata
import logging
import os
import threading
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import JSONResponse

from .m6_overlay_routes import _package

router = APIRouter(prefix="/api/batches", tags=["m6-real-ocr"])
logger = logging.getLogger(__name__)

_lock = threading.Lock()
_active_batch: str | None = None
_jobs: dict[str, str] = {}

_ENV_DET = "POSTER_M6_OCR_DET_MODEL_DIR"
_ENV_REC = "POSTER_M6_OCR_REC_MODEL_DIR"
_ENV_DEVICE = "POSTER_M6_OCR_DEVICE"
_DEVICES = frozenset(("cpu", "gpu", "gpu:0"))


def _runtime_available() -> bool:
    try:
        return (
            importlib.metadata.version("paddleocr").startswith("3.")
            and bool(importlib.metadata.version("paddlepaddle"))
        )
    except importlib.metadata.PackageNotFoundError:
        return False


def _configuration() -> tuple[Path, Path, str] | None:
    """Use only absolute local server-selected paths; never accept browser input."""
    det, rec = os.getenv(_ENV_DET, ""), os.getenv(_ENV_REC, "")
    device = os.getenv(_ENV_DEVICE, "cpu")
    if not det or not rec or device not in _DEVICES:
        return None
    paths = (Path(det).expanduser(), Path(rec).expanduser())
    if any(not p.is_absolute() or p.is_symlink() or not p.is_dir() for p in paths):
        return None
    if paths[0].resolve() == paths[1].resolve():
        return None
    if not _runtime_available():
        return None
    return paths[0], paths[1], device


def _unsafe_state(batch_id: str, package: Path) -> dict:
    """Read-only local status; a matching OCR JSON is still not runtime attestation."""
    from scripts.capture_m6_paddle_ocr import (
        OCREvidenceError, _identities, _review_rows,
    )
    from scripts.verify_m6_ocr_capture import (
        OCRCaptureEvidenceError, verify_ocr_capture,
    )

    output = package / "m6_paddle_ocr_evidence.json"
    if output.is_symlink():
        state, ready = "BLOCKED", False
    elif output.exists():
        try:
            review_sha, initial = _review_rows(package)
            cohort, rows, count = _identities(initial)
            verified = verify_ocr_capture(
                output, rows, cohort_id=cohort, reviews_sha256=review_sha,
            )
            # This is a local declaration plus hash/structural consistency,
            # never proof that a model actually executed.
            state = "EVIDENCE_PRESENT_UNATTESTED"
            return {
                "state": state, "ready_to_capture": False,
                "expected_sku_count": count,
                "declared_capture_consistency": verified["status"],
                "automatically_accepted": False,
                "quality_verified": False, "runtime_attested": False,
            }
        except (OSError, OCREvidenceError, OCRCaptureEvidenceError, ValueError):
            state, ready = "BLOCKED", False
    else:
        with _lock:
            active = _active_batch
            local = _jobs.get(batch_id)
        if active == batch_id:
            state, ready = "RUNNING", False
        elif active is not None:
            state, ready = "BUSY", False
        elif local in ("FAILED", "FAILED_NO_EVIDENCE"):
            state, ready = local, False
        elif _configuration() is None:
            state, ready = "NOT_CONFIGURED", False
        else:
            state, ready = "READY", True
    return {
        "state": state, "ready_to_capture": ready,
        "automatically_accepted": False,
        "quality_verified": False, "runtime_attested": False,
    }


def _run_ocr(batch_id: str, package: Path, det: Path, rec: Path, device: str) -> None:
    global _active_batch
    from scripts.capture_m6_paddle_ocr import capture

    state = "FAILED"
    try:
        capture(package, det, rec, device=device)
        if (package / "m6_paddle_ocr_evidence.json").is_file():
            state = "FINISHED_CHECK_EVIDENCE"
        else:
            state = "FAILED_NO_EVIDENCE"
    except Exception:
        # The exact exception may contain paths or operator-sensitive data;
        # only write it to the server's local diagnostic logs.
        logger.exception("Local M6 PaddleOCR capture failed")
    finally:
        with _lock:
            _jobs[batch_id] = state
            if _active_batch == batch_id:
                _active_batch = None


@router.get("/{batch_id}/m6-ocr-capture")
def inspect_m6_ocr_capture(batch_id: str):
    """Tell the workbench if locally configured capture can be started."""
    package = _package(batch_id)
    return _unsafe_state(batch_id, package)


@router.post("/{batch_id}/m6-ocr-capture", status_code=202)
def start_m6_ocr_capture(batch_id: str, background: BackgroundTasks):
    """Explicit request, at most one active local OCR capture in this process."""
    global _active_batch
    package = _package(batch_id)
    output = package / "m6_paddle_ocr_evidence.json"
    if output.exists() or output.is_symlink():
        raise HTTPException(status_code=409, detail="OCR 证据已存在；禁止重复采集或覆盖")
    config = _configuration()
    if config is None:
        raise HTTPException(
            status_code=409,
            detail="本机未配置 PaddleOCR 3.x/PaddlePaddle 与绝对路径的本地检测、识别模型目录",
        )
    with _lock:
        if _active_batch is not None:
            raise HTTPException(status_code=409, detail="已有本机 OCR 任务正在执行")
        _active_batch = batch_id
        _jobs[batch_id] = "RUNNING"
    # Starlette runs synchronous background work in its thread pool.
    # A process restart interrupts the job: only the atomic evidence file is durable.
    background.add_task(_run_ocr, batch_id, package, *config)
    return JSONResponse(
        status_code=202,
        content={
            "state": "RUNNING", "automatically_accepted": False,
            "quality_verified": False, "runtime_attested": False,
            "message": "已提交本机 OCR 采集，刷新状态前不表示 OCR 成功或可投放",
        },
    )
