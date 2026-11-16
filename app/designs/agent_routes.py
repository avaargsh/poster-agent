"""Local, opt-in natural-language task admission and durable status retrieval."""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from .agent_contracts import AgentRequest
from .agent_runs import AgentBusy, AgentUnavailable, DesignAgentManager
from .errors import DesignError
from .routes import _design_error_response, _unexpected_error_response

router = APIRouter(tags=["design-agent"])


def _agent(request: Request) -> DesignAgentManager:
    manager = getattr(request.app.state, "design_agent", None)
    if not isinstance(manager, DesignAgentManager):
        raise AgentUnavailable()
    return manager


@router.get("/api/design-agent/capabilities")
async def capabilities(request: Request):
    return JSONResponse(_agent(request).capabilities(), headers={"Cache-Control": "no-store"})


@router.get("/api/designs/{document_id}/agent-runs")
async def list_agent_runs(document_id: str, request: Request):
    try:
        return JSONResponse(
            {"runs": _agent(request).list(document_id)}, headers={"Cache-Control": "no-store"}
        )
    except DesignError as exc:
        return _design_error_response(exc)
    except Exception:
        return _unexpected_error_response()


@router.post("/api/designs/{document_id}/agent-runs", status_code=202)
async def submit_agent_run(document_id: str, payload: AgentRequest, request: Request):
    try:
        result = _agent(request).submit(document_id, payload)
        return JSONResponse(result, status_code=202, headers={"Cache-Control": "no-store"})
    except AgentBusy as exc:
        return JSONResponse({"error": exc.as_dict()}, status_code=409)
    except AgentUnavailable as exc:
        return JSONResponse({"error": exc.as_dict()}, status_code=503)
    except DesignError as exc:
        return _design_error_response(exc)
    except Exception:
        return _unexpected_error_response()


@router.get("/api/designs/{document_id}/agent-runs/{request_id}")
async def get_agent_run(document_id: str, request_id: str, request: Request):
    try:
        return JSONResponse(
            _agent(request).get(document_id, request_id), headers={"Cache-Control": "no-store"}
        )
    except DesignError as exc:
        return _design_error_response(exc)
    except Exception:
        return _unexpected_error_response()


@router.post("/api/designs/{document_id}/agent-runs/{request_id}/cancel")
async def cancel_agent_run(document_id: str, request_id: str, request: Request):
    try:
        return JSONResponse(
            await _agent(request).cancel(document_id, request_id),
            headers={"Cache-Control": "no-store"},
        )
    except DesignError as exc:
        return _design_error_response(exc)
    except Exception:
        return _unexpected_error_response()
