"""Natural-language Brief compilation: paid Qwen paths use durable admission."""

from fastapi import APIRouter, Header, HTTPException
from starlette.concurrency import run_in_threadpool

from app import brief_request_store
from app.brief_compiler import BriefCompilerError, BriefRequest
from app.review_service import ReviewConflict, create_run
from app.workflows.brief_graph import brief_graph

router = APIRouter(prefix="/api/briefs", tags=["briefs"])


@router.post("/compile")
async def compile_brief_endpoint(
    request: BriefRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    if request.compiler == "qwen":
        if not idempotency_key:
            raise HTTPException(
                status_code=428,
                detail="Qwen Brief 编译可能计费，请提供 Idempotency-Key",
            )
        try:
            # Sync Graph extraction runs in a worker thread; never block the
            # FastAPI event loop with the network-backed compilation node.
            result = await run_in_threadpool(create_run, request, idempotency_key=idempotency_key)
        except brief_request_store.InvalidReviewRequestKey as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except (brief_request_store.ReviewRequestConflict, ReviewConflict) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except BriefCompilerError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return {"draft": result["draft"], "status": result["status"], "run_id": result["run_id"]}

    try:
        result = await brief_graph.ainvoke({"request": request.model_dump()})
        return {"draft": result["draft"], "status": result["status"]}
    except BriefCompilerError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
