"""Real stdio SDK handshake and HTTP bridge against a disposable backend."""

import asyncio
import os
import socket
import subprocess
import sys
import time
from uuid import uuid4

import httpx
import pytest

from app.mcp_server import validate_base_url


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com",
        "http://10.0.0.1",
        "http://127.0.0.1/api",
        "http://user:pass@localhost",
        "http://localhost?token=anything",
        "http://127.0.0.1:0",
    ],
)
def test_mcp_backend_rejects_nonlocal_or_ambiguous_origins(url):
    with pytest.raises(ValueError):
        validate_base_url(url)


@pytest.fixture
def backend(tmp_path):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = {
        **os.environ,
        **{
            key: str(tmp_path / name)
            for key, name in [
                ("POSTER_DATA_DIR", "posters"),
                ("POSTER_EXECUTION_DB", "executions.sqlite3"),
                ("POSTER_CHECKPOINT_DB", "checkpoints.sqlite3"),
                ("POSTER_DESIGN_DB", "designs.sqlite3"),
                ("POSTER_DESIGN_ASSET_DIR", "assets"),
            ]
        },
    }
    with (tmp_path / "backend.log").open("wb") as log:
        process = subprocess.Popen(
            [sys.executable, "-m", "app.run_local", "--port", str(port)],
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        try:
            with httpx.Client(
                base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=3
            ) as client:
                for _ in range(80):
                    if process.poll() is not None:
                        raise AssertionError("MCP test backend exited")
                    try:
                        if client.get("/health").status_code == 200:
                            break
                    except httpx.HTTPError:
                        time.sleep(0.15)
                else:
                    raise AssertionError("MCP test backend did not start")
                yield client
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


@pytest.mark.parametrize("mode", ["auto", "legacy"])
def test_real_stdio_tools_create_only_a_draft_and_return_confirmation_url(
    backend, product_bytes, mode
):
    sdk = pytest.importorskip("mcp")
    response = backend.post(
        "/api/designs",
        data={"title": "MCP演示", "price": "99.90"},
        files={"product": ("product.png", product_bytes, "image/png")},
    )
    assert response.status_code == 201
    document_id = response.json()["document"]["document_id"]
    body = {
        "request_id": str(uuid4()),
        "expected_revision": 1,
        "summary": "放大活动价",
        "operations": [{"op": "set_style", "node_id": "price", "font_size": 100}],
    }

    async def scenario():
        parameters = sdk.StdioServerParameters(
            command=sys.executable,
            args=["-m", "app.mcp_server", "--base-url", str(backend.base_url).rstrip("/")],
        )
        async with sdk.Client(parameters, mode=mode, read_timeout_seconds=25) as client:
            tools = (await client.list_tools()).tools
            assert {tool.name for tool in tools} == {
                "list_designs",
                "read_design",
                "propose_design_edit",
                "read_edit_proposal",
            }
            assert next(t for t in tools if t.name == "read_design").annotations.read_only_hint
            listed = await client.call_tool("list_designs", {"limit": 1})
            assert not listed.is_error, listed
            item = listed.structured_content["designs"][0]
            assert item["document_id"] == document_id
            assert item["canvas_url"].startswith(str(backend.base_url).rstrip("/") + "/canvas?")
            assert (await client.call_tool("list_designs", {"limit": 51})).is_error
            read = await client.call_tool("read_design", {"document_id": document_id})
            assert not read.is_error
            assert read.structured_content["document"]["revision"] == 1
            proposal = await client.call_tool(
                "propose_design_edit", {"document_id": document_id, "proposal": body}
            )
            assert not proposal.is_error, proposal
            data = proposal.structured_content
            assert data["status"] == "PENDING"
            assert data["confirmation_url"].startswith(
                str(backend.base_url).rstrip("/") + "/canvas?"
            )
            assert "candidate_document" not in data
            again = await client.call_tool(
                "propose_design_edit", {"document_id": document_id, "proposal": body}
            )
            assert again.structured_content["proposal_id"] == data["proposal_id"]
            status = await client.call_tool(
                "read_edit_proposal",
                {"document_id": document_id, "proposal_id": data["proposal_id"]},
            )
            assert status.structured_content["status"] == "PENDING"
            invalid = await client.call_tool("read_design", {"document_id": "../private"})
            assert invalid.is_error
            missing = await client.call_tool("read_design", {"document_id": "doc_missing"})
            assert missing.is_error

    asyncio.run(scenario())
    assert backend.get("/api/designs/" + document_id).json()["document"]["revision"] == 1
