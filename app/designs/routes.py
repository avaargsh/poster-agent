"""Local HTTP adapter for the persistent AI canvas documents.

The routes are intentionally thin: validation, command semantics and asset
integrity live in the design repository.  This module owns only HTTP concerns
such as upload limits, status mapping and the stable error envelope.

Blocking SQLite and SVG work runs in Starlette's thread pool.  The service
manager is read from ``request.app.state`` and is never shared through a
module-level repository.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
from decimal import Decimal, InvalidOperation
from typing import Annotated

from fastapi import APIRouter, File, Form, Path, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field

from app.review_service import ReviewNotFound, get_run
from app.schemas import PosterSpec

from .approvals import ApprovalRequest
from .commands import CommandBatch
from .errors import (
    AssetInvalidError,
    DesignError,
    NotFoundError,
    StorageError,
)
from .models import MAX_ASSET_BYTES, DesignDocument
from .ocr_model_policy import OCRModelProfileError
from .ocr_observations import OCRObservationRequest, assess_unattested
from .ocr_runs import OCRRunJournal, OCRRunRequest, submit_ocr_run
from .rasterization import RasterInvalid, RasterUnavailable, rasterize_svg
from .rendering import render_svg
from .reviewer_auth import ReviewerAccessError, reviewer_for_request
from .service import (
    DesignManager,
    DesignStorageBusy,
    DesignStorageUnavailable,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["designs"])

_PREVIEW_CSP = "default-src 'none'; img-src data:; sandbox"

_DOMAIN_STATUS = {
    "NOT_FOUND": 404,
    "REVISION_CONFLICT": 409,
    "LOCKED_NODE": 409,
    "IDEMPOTENCY_CONFLICT": 409,
    "NOTHING_TO_UNDO": 409,
    "NOTHING_TO_REDO": 409,
    "INVALID_COMMAND": 422,
    "APPROVAL_REQUIRED": 409,
    "APPROVAL_DIGEST_CONFLICT": 409,
    "APPROVAL_CONFLICT": 409,
    "OCR_TARGET_INCOMPLETE": 409,
    "OCR_SOURCE_MISMATCH": 409,
}


def _error_response(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message}},
    )


def _design_error_response(
    exc: DesignError,
    *,
    asset_invalid_status: int = 409,
) -> JSONResponse:
    if isinstance(exc, DesignStorageBusy):
        return _error_response(503, exc.code, exc.message)
    if isinstance(exc, DesignStorageUnavailable):
        return _error_response(503, exc.code, exc.message)
    if isinstance(exc, StorageError) or exc.code == "STORAGE_ERROR":
        return _error_response(
            503,
            "DESIGN_STORAGE_UNAVAILABLE",
            "design storage is unavailable",
        )
    if isinstance(exc, AssetInvalidError):
        return _error_response(asset_invalid_status, exc.code, exc.message)
    status = _DOMAIN_STATUS.get(exc.code, 422)
    return _error_response(status, exc.code, exc.message)


def _unexpected_error_response() -> JSONResponse:
    logger.exception("unexpected design storage failure")
    return _error_response(
        503,
        "DESIGN_STORAGE_UNAVAILABLE",
        "design storage is unavailable",
    )


def _manager(request: Request) -> DesignManager:
    manager = getattr(request.app.state, "design_manager", None)
    if not isinstance(manager, DesignManager):
        raise DesignStorageUnavailable("design service lifecycle is not running")
    return manager


def _preview_url(document: DesignDocument) -> str:
    return f"/api/designs/{document.document_id}/revisions/{document.revision}/preview.svg"


def _create_document(
    manager: DesignManager,
    title: str,
    price: Decimal,
    product_bytes: bytes,
    cta: str | None = None,
) -> DesignDocument:
    return manager.repository().create(
        title=title,
        price=price,
        product_bytes=product_bytes,
        cta=cta,
    )


@router.post("/api/designs", status_code=201)
async def create_design(
    request: Request,
    product: Annotated[UploadFile, File(description="商品图片")],
    title: Annotated[str, Form()],
    price: Annotated[str, Form()],
    cta: Annotated[str | None, Form()] = None,
):
    try:
        manager = _manager(request)
    except DesignError as exc:
        return _design_error_response(exc)

    try:
        price_value = Decimal(price.strip())
    except (InvalidOperation, ValueError):
        return _error_response(422, "INVALID_COMMAND", "price must be a decimal number")

    try:
        raw = await product.read(MAX_ASSET_BYTES + 1)
    finally:
        await product.close()

    if not raw:
        return _error_response(422, "ASSET_INVALID", "product image is empty")
    if len(raw) > MAX_ASSET_BYTES:
        return _error_response(
            413,
            "ASSET_TOO_LARGE",
            f"product image exceeds {MAX_ASSET_BYTES} bytes",
        )

    try:
        document = await run_in_threadpool(
            _create_document,
            manager,
            title,
            price_value,
            raw,
            cta,
        )
    except DesignError as exc:
        # Every ASSET_INVALID raised during create concerns the uploaded bytes;
        # persisted asset corruption is only possible on later reads.
        return _design_error_response(exc, asset_invalid_status=422)
    except Exception:
        return _unexpected_error_response()

    return {
        "document": document.model_dump(mode="json"),
        "preview_url": _preview_url(document),
    }


@router.get("/api/designs/{document_id}")
def get_design(document_id: str, request: Request):
    try:
        document = _manager(request).repository().get(document_id)
    except DesignError as exc:
        return _design_error_response(exc)
    except Exception:
        return _unexpected_error_response()

    return {
        "document": document.model_dump(mode="json"),
        "preview_url": _preview_url(document),
    }


@router.get("/api/designs/{document_id}/revisions/{revision}")
def get_design_revision(
    document_id: str,
    request: Request,
    revision: Annotated[int, Path(ge=1)],
):
    try:
        document = _manager(request).repository().get(document_id, revision=revision)
    except NotFoundError as exc:
        return _design_error_response(exc)
    except DesignError as exc:
        return _design_error_response(exc)
    except Exception:
        return _unexpected_error_response()

    return {
        "document": document.model_dump(mode="json"),
        "preview_url": _preview_url(document),
    }


class BriefLinkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    brief_run_id: str = Field(pattern=r"^[a-f0-9]{32}$")


def _brief_link_token_valid(request: Request) -> bool:
    """Local operator credential, not an authenticated reviewer identity."""
    expected = os.environ.get("POSTER_BRIEF_LINK_TOKEN", "")
    authorization = request.headers.get("authorization", "")
    if len(expected) < 32 or not authorization.startswith("Bearer "):
        return False
    provided = authorization.removeprefix("Bearer ")
    return hmac.compare_digest(provided, expected)


@router.post("/api/designs/{document_id}/brief-link", status_code=201)
def bind_design_brief(document_id: str, payload: BriefLinkRequest, request: Request):
    """Link only an approved Brief checkpoint; never infer design approval."""
    if not _brief_link_token_valid(request):
        return _error_response(401, "BRIEF_LINK_UNAUTHORIZED", "operator token required")
    try:
        run = get_run(payload.brief_run_id)
        if run["status"] != "APPROVED" or run["pending_approval"]:
            return _error_response(409, "BRIEF_NOT_APPROVED", "Brief is not approved")
        spec = PosterSpec.model_validate(run["confirmed_spec"])
        digest = hashlib.sha256(spec.model_dump_json().encode("utf-8")).hexdigest()
        result = (
            _manager(request)
            .repository()
            .bind_brief_run(
                document_id,
                run_id=payload.brief_run_id,
                confirmed_spec_digest=digest,
                confirmed_title=spec.title,
                confirmed_price=spec.price,
                confirmed_cta=spec.cta,
            )
        )
    except ReviewNotFound:
        return _error_response(404, "BRIEF_NOT_FOUND", "Brief checkpoint not found")
    except DesignError as exc:
        return _design_error_response(exc)
    except Exception:
        return _unexpected_error_response()
    if result["replayed"]:
        return JSONResponse(status_code=200, content=result)
    return result


@router.get("/api/designs/{document_id}/brief-link")
def get_design_brief_link(document_id: str, request: Request):
    if not _brief_link_token_valid(request):
        return _error_response(401, "BRIEF_LINK_UNAUTHORIZED", "operator token required")
    try:
        return {"link": _manager(request).repository().brief_link(document_id)}
    except DesignError as exc:
        return _design_error_response(exc)
    except Exception:
        return _unexpected_error_response()


@router.get("/api/designs/{document_id}/approval-target")
def get_design_approval_target(document_id: str, request: Request):
    """Return current server-calculated digests for explicit local review."""
    try:
        return _manager(request).repository().approval_target(document_id)
    except DesignError as exc:
        return _design_error_response(exc)
    except Exception:
        return _unexpected_error_response()


@router.get("/api/designs/{document_id}/approvals")
def get_design_approval_history(document_id: str, request: Request):
    """Inspect recorded local approvals without granting export authorization."""
    try:
        reviewer_for_request(request, "audit.read")
        return _manager(request).repository().approval_history(document_id)
    except ReviewerAccessError as exc:
        return _error_response(exc.status, exc.code, exc.message)
    except DesignError as exc:
        return _design_error_response(exc)
    except Exception:
        return _unexpected_error_response()


@router.post("/api/designs/{document_id}/approvals", status_code=201)
def approve_design(document_id: str, decision: ApprovalRequest, request: Request):
    """Approve one exact current revision; same ID replay returns HTTP 200."""
    try:
        reviewer = reviewer_for_request(request, "approve")
        result = _manager(request).repository().approve(document_id, decision, reviewer=reviewer)
    except ReviewerAccessError as exc:
        return _error_response(exc.status, exc.code, exc.message)
    except DesignError as exc:
        return _design_error_response(exc)
    except Exception:
        return _unexpected_error_response()
    if result["replayed"]:
        return JSONResponse(status_code=200, content=result)
    return result


@router.post("/api/designs/{document_id}/commands")
def execute_design_commands(
    document_id: str,
    batch: CommandBatch,
    request: Request,
):
    try:
        manager = _manager(request)
    except DesignError as exc:
        return _design_error_response(exc)

    if batch.actor != "user":
        # Actor is audit metadata in the repository.  This manual HTTP entry
        # grants only user edits; future engines and external agents need a
        # separate authorization path instead of asserting a different actor.
        return _error_response(
            422,
            "INVALID_COMMAND",
            "this HTTP entry only accepts actor=user",
        )

    try:
        result = manager.repository().execute(document_id, batch)
    except DesignError as exc:
        return _design_error_response(exc)
    except Exception:
        return _unexpected_error_response()

    return {
        "document": result.document.model_dump(mode="json"),
        "command_id": result.command_id,
        "replayed": result.replayed,
        "preview_url": _preview_url(result.document),
    }


@router.get("/api/designs/{document_id}/revisions/{revision}/preview.svg")
def preview_design_revision(
    document_id: str,
    request: Request,
    revision: Annotated[int, Path(ge=1)],
):
    try:
        manager = _manager(request)
        repository = manager.repository()
        document = repository.get(document_id, revision=revision)
        assets = repository.read_assets(document)
        svg = render_svg(document, assets=assets)
    except DesignError as exc:
        # A persisted asset that no longer matches its index/hash is a storage
        # integrity conflict, not an invalid user upload.
        return _design_error_response(exc, asset_invalid_status=409)
    except Exception:
        return _unexpected_error_response()

    return Response(
        content=svg,
        media_type="image/svg+xml; charset=utf-8",
        headers={
            "Content-Security-Policy": _PREVIEW_CSP,
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
        },
    )


@router.get("/api/designs/{document_id}/revisions/{revision}/approved.svg")
def export_approved_design(
    document_id: str,
    request: Request,
    revision: Annotated[int, Path(ge=1)],
):
    """Protected export, separate from the public draft preview endpoint."""
    try:
        reviewer = reviewer_for_request(request, "export")
        svg = (
            _manager(request)
            .repository()
            .approved_svg(
                document_id,
                revision,
                required_policy_digest=reviewer.policy_digest if reviewer is not None else None,
            )
        )
    except ReviewerAccessError as exc:
        return _error_response(exc.status, exc.code, exc.message)
    except DesignError as exc:
        return _design_error_response(exc, asset_invalid_status=409)
    except Exception:
        return _unexpected_error_response()

    return Response(
        content=svg,
        media_type="image/svg+xml; charset=utf-8",
        headers={
            "Content-Security-Policy": _PREVIEW_CSP,
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
        },
    )


@router.get("/api/designs/{document_id}/revisions/{revision}/ocr-target")
def get_design_ocr_target(
    document_id: str,
    request: Request,
    revision: Annotated[int, Path(ge=1)],
):
    """Read a frozen, approved SVG input description; never claim OCR acceptance."""
    try:
        reviewer = reviewer_for_request(request, "export")
        target = (
            _manager(request)
            .repository()
            .ocr_target(
                document_id,
                revision,
                required_policy_digest=reviewer.policy_digest if reviewer is not None else None,
            )
        )
    except ReviewerAccessError as exc:
        return _error_response(exc.status, exc.code, exc.message)
    except DesignError as exc:
        return _design_error_response(exc, asset_invalid_status=409)
    except Exception:
        return _unexpected_error_response()
    return JSONResponse(
        content=target,
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )


@router.post("/api/designs/{document_id}/revisions/{revision}/ocr-evaluation-preview")
def evaluate_design_ocr_observations(
    document_id: str,
    payload: OCRObservationRequest,
    request: Request,
    revision: Annotated[int, Path(ge=1)],
):
    """Compare declared observations only; never attest OCR execution or publish."""
    try:
        reviewer = reviewer_for_request(request, "export")
        target = (
            _manager(request)
            .repository()
            .ocr_target(
                document_id,
                revision,
                required_policy_digest=reviewer.policy_digest if reviewer is not None else None,
            )
        )
        report = assess_unattested(target, payload)
    except ReviewerAccessError as exc:
        return _error_response(exc.status, exc.code, exc.message)
    except DesignError as exc:
        return _design_error_response(exc, asset_invalid_status=409)
    except Exception:
        return _unexpected_error_response()
    return JSONResponse(
        content=report,
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )


@router.get("/api/designs/{document_id}/revisions/{revision}/approved.png")
def export_approved_design_raster(
    document_id: str,
    request: Request,
    revision: Annotated[int, Path(ge=1)],
):
    """Protected pixels of a current approval; never an OCR or release receipt."""
    try:
        reviewer = reviewer_for_request(request, "export")
        repository = _manager(request).repository()
        policy_digest = reviewer.policy_digest if reviewer is not None else None
        svg = repository.approved_svg(document_id, revision, required_policy_digest=policy_digest)
        raster = rasterize_svg(svg)
        # Rasterization happens outside the SQLite read transaction. Re-check
        # current approval, revision, policy and asset integrity before returning.
        # Consumers must still revalidate before any downstream release decision.
        checked_svg = repository.approved_svg(
            document_id, revision, required_policy_digest=policy_digest
        )
        if not hmac.compare_digest(
            hashlib.sha256(checked_svg.encode("utf-8")).hexdigest(), raster.svg_sha256
        ):
            return _error_response(409, "RASTER_SOURCE_CHANGED", "approved SVG changed")
    except ReviewerAccessError as exc:
        return _error_response(exc.status, exc.code, exc.message)
    except DesignError as exc:
        return _design_error_response(exc, asset_invalid_status=409)
    except RasterUnavailable:
        return _error_response(503, "DESIGN_RASTER_UNAVAILABLE", "rasterizer unavailable")
    except RasterInvalid:
        return _error_response(503, "DESIGN_RASTER_INVALID", "approved raster unavailable")
    except Exception:
        return _unexpected_error_response()

    return Response(
        content=raster.png,
        media_type="image/png",
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "X-Approved-SVG-SHA256": raster.svg_sha256,
            "X-Raster-PNG-SHA256": raster.png_sha256,
            "X-Design-Rasterizer": raster.rasterizer,
            "X-Design-OCR-Status": "NOT_RUN",
            "X-Design-Release-Ready": "false",
        },
    )


@router.post("/api/designs/{document_id}/revisions/{revision}/ocr-runs")
def submit_design_ocr_execution(
    document_id: str,
    payload: OCRRunRequest,
    request: Request,
    revision: Annotated[int, Path(ge=1)],
):
    """Explicit opt-in local OCR. A journaled observation is NOT attestation."""
    if getattr(request.app.state, "design_ocr_runs_enabled", False) is not True:
        return _error_response(403, "OCR_EXECUTION_DISABLED", "local OCR execution disabled")
    try:
        reviewer = reviewer_for_request(request, "export")
        if reviewer is None:
            return _error_response(
                403, "OCR_REVIEWER_REQUIRED", "named export reviewer policy required"
            )
        result, created = submit_ocr_run(
            _manager(request).repository(),
            document_id,
            revision,
            payload,
            principal=reviewer.principal_id,
            policy=reviewer.policy_digest,
            model_config_raw=getattr(request.app.state, "design_ocr_model_profile_raw", None),
        )
    except ReviewerAccessError as exc:
        return _error_response(exc.status, exc.code, exc.message)
    except DesignError as exc:
        return _design_error_response(exc, asset_invalid_status=409)
    except OCRModelProfileError:
        return _error_response(503, "OCR_LOCAL_MODEL_PROFILE_UNAVAILABLE", "pinned local OCR model profile unavailable")
    except RasterUnavailable:
        return _error_response(503, "DESIGN_RASTER_UNAVAILABLE", "rasterizer unavailable")
    except RasterInvalid:
        return _error_response(503, "DESIGN_RASTER_INVALID", "approved raster unavailable")
    except Exception:
        return _unexpected_error_response()
    return JSONResponse(
        content=result,
        status_code=201 if created else 200,
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )


@router.get("/api/designs/{document_id}/revisions/{revision}/ocr-runs/{operation_id}")
def get_design_ocr_execution(
    document_id: str,
    request: Request,
    revision: Annotated[int, Path(ge=1)],
    operation_id: Annotated[str, Path(pattern=r"^ocr_[0-9a-f]{32}$")],
):
    """Query a local journal record without repeating a provider invocation."""
    try:
        reviewer = reviewer_for_request(request, "export")
        if reviewer is None:
            return _error_response(
                403, "OCR_REVIEWER_REQUIRED", "named export reviewer policy required"
            )
        result = OCRRunJournal(_manager(request).repository()).get(
            document_id,
            revision,
            operation_id,
            principal=reviewer.principal_id,
            policy=reviewer.policy_digest,
        )
        if result is None:
            raise NotFoundError()
    except ReviewerAccessError as exc:
        return _error_response(exc.status, exc.code, exc.message)
    except DesignError as exc:
        return _design_error_response(exc, asset_invalid_status=409)
    except Exception:
        return _unexpected_error_response()
    return JSONResponse(
        content=result,
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )
