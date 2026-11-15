"""Local stdio bridge from MCP hosts to the running Poster Agent service."""

from __future__ import annotations

import argparse
import ipaddress
import json
import re
from urllib.parse import urlsplit

import httpx

from app.designs.models import DocumentId
from app.designs.proposals import EditProposalRequest


def validate_base_url(value: str) -> str:
    parsed = urlsplit(value)
    try:
        local = (
            parsed.hostname == "localhost"
            or ipaddress.ip_address(parsed.hostname or "").is_loopback
        )
        valid_port = parsed.port is None or 1 <= parsed.port <= 65535
    except ValueError:
        local, valid_port = False, False
    if not (
        parsed.scheme == "http"
        and local
        and valid_port
        and parsed.path in {"", "/"}
        and parsed.username is None
        and parsed.password is None
        and not parsed.query
        and not parsed.fragment
    ):
        raise ValueError("MCP backend must be a plain loopback HTTP origin")
    return value.rstrip("/")


def build_server(base_url: str):
    # Optional SDK is imported only when explicitly launching the MCP adapter.
    from mcp.server import MCPServer
    from mcp.server.mcpserver.exceptions import ToolError
    from mcp.types import ToolAnnotations

    base_url = validate_base_url(base_url)
    server = MCPServer("Poster Agent")

    async def call(method: str, path: str, body: dict | None = None) -> dict:
        try:
            async with httpx.AsyncClient(
                base_url=base_url, timeout=20, trust_env=False, follow_redirects=False
            ) as client:
                async with client.stream(method, path, json=body) as response:
                    chunks = bytearray()
                    async for chunk in response.aiter_bytes():
                        chunks.extend(chunk)
                        if len(chunks) > 2 * 1024 * 1024:
                            raise ToolError("BACKEND_RESPONSE_TOO_LARGE")
                    try:
                        result = json.loads(chunks)
                    except (ValueError, UnicodeError):
                        raise ToolError("INVALID_BACKEND_RESPONSE") from None
                    if not response.is_success:
                        error = result.get("error") if isinstance(result, dict) else None
                        code = error.get("code") if isinstance(error, dict) else None
                        if not isinstance(code, str) or not re.fullmatch(r"[A-Z_]{1,64}", code):
                            code = "BACKEND_REQUEST_FAILED"
                        raise ToolError(code)
                    if not isinstance(result, dict):
                        raise ToolError("INVALID_BACKEND_RESPONSE")
                    return result
        except httpx.HTTPError:
            raise ToolError(
                "BACKEND_UNAVAILABLE: start the local Poster Agent HTTP service"
            ) from None

    @server.tool(
        structured_output=True,
        annotations=ToolAnnotations(
            readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
        ),
    )
    async def read_design(document_id: DocumentId) -> dict[str, object]:
        """Read an existing design and its current revision before proposing edits. Asset metadata is returned without image bytes."""
        return await call("GET", f"/api/designs/{document_id}")

    @server.tool(
        structured_output=True,
        annotations=ToolAnnotations(
            readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False
        ),
    )
    async def propose_design_edit(
        document_id: DocumentId, proposal: EditProposalRequest
    ) -> dict[str, object]:
        """Create a bounded draft suggestion. Reuse request_id for retries. The design stays unchanged until the user reviews and applies it in the canvas. Locked layers cannot be edited."""
        result = await call(
            "POST", f"/api/designs/{document_id}/proposals", proposal.model_dump(mode="json")
        )
        result.pop("candidate_document", None)
        result.pop("base_document", None)
        result["confirmation_url"] = base_url + result["confirmation_url"]
        return result

    @server.tool(
        structured_output=True,
        annotations=ToolAnnotations(
            readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
        ),
    )
    async def read_edit_proposal(document_id: DocumentId, proposal_id: str) -> dict[str, object]:
        """Read the status and operations of a prior suggestion. APPLIED records an edit, not quality approval or publication permission."""
        if not re.fullmatch(r"prop_[0-9a-f]{32}", proposal_id):
            raise ToolError("INVALID_PROPOSAL_ID")
        result = await call("GET", f"/api/designs/{document_id}/proposals/{proposal_id}")
        result.pop("candidate_document", None)
        result.pop("base_document", None)
        result["confirmation_url"] = base_url + result["confirmation_url"]
        return result

    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    try:
        server = build_server(args.base_url)
    except ImportError:
        parser.error("Install the MCP extra first: uv sync --locked --extra mcp")
    except ValueError as exc:
        parser.error(str(exc))
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
