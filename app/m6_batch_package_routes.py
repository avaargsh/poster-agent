"""Expose the existing M6 offline review-package exporter for completed web batches.

The API chooses all paths server-side: no caller-controlled journal/output
paths, no OCR or paid calls, and no overwrite of prior human review work.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from .batch_import_routes import _batch_dir, batch_review_scope

router = APIRouter(prefix="/api/batches", tags=["m6-web-package"])

_PACKAGE_DIR = "m6_review_package"


def _verified_source(batch_id: str):
    # The shared review-scope gate independently checks completed batch state,
    # full journal SKU membership and the ORIGINAL v1 receipt for every SKU.
    batch_review_scope(batch_id)
    batch_dir = _batch_dir(batch_id)
    journal = batch_dir / "batch_import_results.json"
    if journal.is_symlink():
        raise HTTPException(status_code=409, detail="M5g 账本不能是符号链接")
    return journal, batch_dir / _PACKAGE_DIR


@router.get("/{batch_id}/m6-package")
def inspect_m6_package(batch_id: str):
    """Compute read-only plan; never render or create review files."""
    journal, destination = _verified_source(batch_id)
    from scripts.prepare_m6_review import PreparationError, prepare_package

    try:
        plan = prepare_package(journal, destination)
    except (OSError, ValueError, KeyError, PreparationError) as exc:
        raise HTTPException(status_code=409, detail=f"M6 审核包预检失败：{exc}") from exc
    return {
        **plan,
        "prepared": destination.exists() or destination.is_symlink(),
        "relative_directory": f"{batch_id}/{_PACKAGE_DIR}",
    }


@router.post("/{batch_id}/m6-package", status_code=201)
def export_m6_package(batch_id: str):
    """Create one new local offline package with UNVERIFIED labels only."""
    journal, destination = _verified_source(batch_id)
    if destination.exists() or destination.is_symlink():
        raise HTTPException(
            status_code=409,
            detail="M6 审核包目录已存在，禁止覆盖图片或人工评审 CSV",
        )
    from scripts.prepare_m6_review import PreparationError, prepare_package

    try:
        result = prepare_package(journal, destination, apply=True)
    except (OSError, ValueError, KeyError, PreparationError) as exc:
        raise HTTPException(status_code=409, detail=f"M6 审核包生成受阻：{exc}") from exc
    return {
        **result,
        "prepared": True,
        "relative_directory": f"{batch_id}/{_PACKAGE_DIR}",
    }
