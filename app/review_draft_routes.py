"""Progress endpoints never submit labels or run an OCR/model provider."""
from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.concurrency import run_in_threadpool
from pydantic import ValidationError

from . import review_drafts
from .human_annotations import AnnotationConflict, REVIEWER_PATTERN
from .native_human_reviews import NativeReviewConflict
from .review_drafts import ReviewVariant

router = APIRouter(prefix="/api/posters", tags=["review-progress"])
_save_schema = review_drafts.SaveDraft.model_json_schema()
# Keep the bounded streaming parser while publishing its real Pydantic schema.
# Inline the sole nested type so Swagger does not receive a dangling $ref.
_save_schema["properties"]["fields"]["additionalProperties"] = _save_schema.pop("$defs")["DraftField"]


def _perform(operation, *args):
    try:
        return operation(*args)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="海报版本不存在") from exc
    except (review_drafts.DraftConflict, AnnotationConflict, NativeReviewConflict) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=409, detail="无法读写本机审核进度，原有草稿已保留，请重试") from exc


@router.get("/{poster_id}/versions/{version}/review-draft")
def read_draft(
    poster_id: str, version: int, response: Response,
    reviewer: str = Query(min_length=2, max_length=40, pattern=REVIEWER_PATTERN),
    variant: ReviewVariant = "square",
):
    response.headers["Cache-Control"] = "no-store"
    return _perform(review_drafts.load, poster_id, version, variant, reviewer)


@router.put("/{poster_id}/versions/{version}/review-draft", openapi_extra={
    "requestBody": {"required": True, "content": {"application/json": {"schema": _save_schema}}},
})
async def save_draft(
    poster_id: str, version: int, request: Request, response: Response,
    reviewer: str = Query(min_length=2, max_length=40, pattern=REVIEWER_PATTERN),
    variant: ReviewVariant = "square",
):
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > review_drafts.MAX_DRAFT_BYTES:
            raise HTTPException(status_code=413, detail="审核草稿请求超过 16 KiB")
        raw.extend(chunk)
    try:
        payload = review_drafts.SaveDraft.model_validate_json(bytes(raw))
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail="审核草稿字段或版本不合法") from exc
    response.headers["Cache-Control"] = "no-store"
    return await run_in_threadpool(
        _perform, review_drafts.save, poster_id, version, variant, reviewer, payload,
    )
