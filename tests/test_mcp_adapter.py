"""Real stdio SDK handshake and HTTP bridge against a disposable backend."""

import asyncio
import sys
from uuid import uuid4

import pytest

from app.mcp_server import validate_base_url
from tests.server_support import local_backend


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
    with local_backend(tmp_path) as client:
        yield client


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


def test_scoped_mcp_enforces_document_request_revision_and_budget(backend, product_bytes):
    sdk = pytest.importorskip("mcp")

    def create():
        return backend.post(
            "/api/designs",
            data={"title": "范围测试", "price": "99"},
            files={"product": ("product.png", product_bytes, "image/png")},
        ).json()["document"]["document_id"]

    document_id, other_id = create(), create()
    request_id = str(uuid4())
    body = {
        "request_id": request_id,
        "expected_revision": 1,
        "summary": "调整字号",
        "operations": [{"op": "set_style", "node_id": "price", "font_size": 100}],
    }

    async def scenario():
        parameters = sdk.StdioServerParameters(
            command=sys.executable,
            args=[
                "-m",
                "app.mcp_server",
                "--base-url",
                str(backend.base_url).rstrip("/"),
                "--document-id",
                document_id,
                "--request-id",
                request_id,
                "--expected-revision",
                "1",
            ],
        )
        async with sdk.Client(parameters, mode="legacy", read_timeout_seconds=25) as client:
            assert (await client.call_tool("list_designs", {})).is_error
            assert (await client.call_tool("read_design", {"document_id": other_id})).is_error
            assert (
                await client.call_tool(
                    "propose_design_edit",
                    {"document_id": document_id, "proposal": {**body, "request_id": str(uuid4())}},
                )
            ).is_error
            assert not (
                await client.call_tool("read_design", {"document_id": document_id})
            ).is_error
            result = await client.call_tool(
                "propose_design_edit", {"document_id": document_id, "proposal": body}
            )
            assert not result.is_error, result
            proposal_id = result.structured_content["proposal_id"]
            assert (
                await client.call_tool(
                    "read_edit_proposal",
                    {"document_id": document_id, "proposal_id": "prop_" + "0" * 32},
                )
            ).is_error
            assert not (
                await client.call_tool(
                    "read_edit_proposal", {"document_id": document_id, "proposal_id": proposal_id}
                )
            ).is_error
            results = [
                await client.call_tool("read_design", {"document_id": document_id})
                for _ in range(7)
            ]
            assert not results[0].is_error and results[-1].is_error

    asyncio.run(scenario())
    assert backend.get("/api/designs/" + document_id).json()["document"]["revision"] == 1
    assert backend.get("/api/designs/" + other_id + "/proposals").json()["proposals"] == []
