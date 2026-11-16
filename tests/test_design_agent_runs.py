import asyncio
import json
import os
import time
from decimal import Decimal
from uuid import uuid4

import pytest

from app.designs.agent_contracts import AgentRequest, AgentScope
from app.designs.agent_runs import (
    AgentExecutionError,
    AgentWorker,
    DesignAgentManager,
    verify_trace,
)
from app.designs.errors import StorageError
from app.designs.service import DesignManager
from tests.fake_codex import write_fake_codex
from tests.server_support import local_backend
from tests.test_codex_design_acceptance import trace


def create(client, product_bytes):
    result = client.post(
        "/api/designs",
        data={"title": "原始标题", "price": "99.90"},
        files={"product": ("product.png", product_bytes, "image/png")},
    )
    assert result.status_code == 201, result.text
    return result.json()["document"]["document_id"]


def wait(client, url):
    for _ in range(160):
        result = client.get(url)
        assert result.status_code == 200, result.text
        value = result.json()
        if value["status"] != "RUNNING":
            return value
        time.sleep(0.1)
    raise AssertionError("Agent task did not finish")


def test_real_http_admission_replay_cancel_and_restart(tmp_path, product_bytes):
    pytest.importorskip("mcp")
    binary = write_fake_codex(tmp_path / "bin")
    directory = tmp_path / "service"
    with local_backend(directory, enable_agent=True, codex_bin=binary) as client:
        doc = create(client, product_bytes)
        other = create(client, product_bytes)
        url = f"/api/designs/{doc}/agent-runs"
        body = {"request_id": str(uuid4()), "expected_revision": 1, "prompt": "调整标题与字号"}
        response = client.post(url, json=body)
        assert response.status_code == 202, response.text
        assert client.post(url, json=body).status_code == 202
        assert client.post(url, json={**body, "prompt": "different"}).status_code == 409
        result = wait(client, url + "/" + body["request_id"])
        assert result["status"] == "SUCCEEDED", result
        assert result["proposal_id"]
        assert client.get(f"/api/designs/{doc}").json()["document"]["revision"] == 1
        assert (
            client.get(f"/api/designs/{other}/agent-runs/{body['request_id']}").status_code == 404
        )
        assert (
            client.post(f"/api/designs/{other}/agent-runs/{body['request_id']}/cancel").status_code
            == 404
        )
        assert client.post(url, json=body).json()["status"] == "SUCCEEDED"
        hold = {**body, "request_id": str(uuid4()), "prompt": "hold-task"}
        assert client.post(url, json=hold).status_code == 202
        assert client.post(url, json={**body, "request_id": str(uuid4())}).status_code == 409
        cancelled = client.post(url + "/" + hold["request_id"] + "/cancel")
        assert cancelled.status_code == 200 and cancelled.json()["status"] == "CANCELLED"
        assert client.post(url, json=hold).json()["status"] == "CANCELLED"
        assert len(client.get(url).json()["runs"]) == 2
        assert (
            client.post(
                url,
                json={**body, "request_id": str(uuid4())},
                headers={"Origin": "https://example.invalid"},
            ).status_code
            == 403
        )
    with local_backend(directory, enable_agent=True, codex_bin=binary) as client:
        assert client.get(url + "/" + body["request_id"]).json()["status"] == "SUCCEEDED"
        assert client.post(url, json=body).json()["proposal_id"] == result["proposal_id"]
    calls = (binary / "calls.log").read_text()
    assert calls.count(body["request_id"]) == 1


def test_disabled_and_stale_task_admission_do_not_launch(tmp_path, product_bytes):
    with local_backend(tmp_path) as client:
        doc = create(client, product_bytes)
        url = f"/api/designs/{doc}/agent-runs"
        result = client.post(
            url, json={"request_id": str(uuid4()), "expected_revision": 1, "prompt": "修改"}
        )
        assert result.status_code == 503 and result.json()["error"]["code"] == "AGENT_UNAVAILABLE"
        assert client.get(url).json()["runs"] == []


def test_timeout_and_interrupted_run_recovery(tmp_path, monkeypatch, product_bytes):
    pytest.importorskip("mcp")
    binary = write_fake_codex(tmp_path / "bin")
    monkeypatch.setenv("PATH", str(binary))
    monkeypatch.setattr("app.designs.agent_runs.AGENT_TIMEOUT_SECONDS", 0.05)
    from app.designs.agent_runs import _stop

    stopped = []

    async def stop(process):
        await _stop(process)
        stopped.append(process.pid)

    monkeypatch.setattr("app.designs.agent_runs._stop", stop)
    designs = DesignManager(tmp_path / "designs.sqlite3", tmp_path / "assets")
    repo = designs.repository()
    doc = repo.create(title="测试", price=Decimal("99"), product_bytes=product_bytes).document_id

    async def scenario():
        manager = DesignAgentManager(designs, enabled=True, origin="http://127.0.0.1:8000")
        body = AgentRequest(request_id=str(uuid4()), expected_revision=1, prompt="hold-task")
        manager.submit(doc, body)
        await asyncio.gather(*(worker.task for worker in list(manager.tasks.values())))
        result = manager.get(doc, body.request_id)
        assert result["status"] == "FAILED" and result["error_code"] == "MODEL_TIMEOUT"
        assert manager.submit(doc, body)["status"] == "FAILED"
        assert stopped
        for pid in stopped:
            with pytest.raises(ProcessLookupError):
                os.kill(pid, 0)
        with repo._write_transaction() as db:
            db.execute(
                "UPDATE design_agent_runs SET status='RUNNING',finished_at=NULL WHERE request_id=?",
                (body.request_id,),
            )
        restarted = DesignAgentManager(designs, enabled=True, origin="http://127.0.0.1:8000")
        assert restarted.get(doc, body.request_id)["status"] == "UNKNOWN"
        await manager.close()
        await restarted.close()

    try:
        asyncio.run(scenario())
    finally:
        designs.close()


@pytest.mark.parametrize(
    "mutation",
    [
        "other_document",
        "other_proposal",
        "other_request",
        "extra_tool",
        "command",
        "multiple_turns",
        "out_of_order",
    ],
)
def test_agent_trace_rejects_scope_and_terminal_violations(mutation):
    before = {"document_id": "doc_scope", "revision": 1}
    proposal = {"document_id": "doc_scope", "proposal_id": "prop_" + "a" * 32, "status": "PENDING"}
    scope = AgentScope(
        document_id="doc_scope", expected_revision=1, request_id="acceptance-test-001"
    )
    events = trace(before, proposal)
    encode = lambda: "\n".join(json.dumps(event) for event in events).encode()
    verify_trace(encode(), scope, proposal["proposal_id"])
    if mutation == "other_document":
        events[0]["item"]["arguments"]["document_id"] = "doc_other"
    elif mutation == "other_proposal":
        events[2]["item"]["arguments"]["proposal_id"] = "prop_" + "b" * 32
    elif mutation == "other_request":
        events[1]["item"]["arguments"]["proposal"]["request_id"] = "another-request"
    elif mutation == "extra_tool":
        events[0]["item"]["tool"] = "list_designs"
    elif mutation == "command":
        events.insert(0, {"type": "item.completed", "item": {"type": "command_execution"}})
    elif mutation == "multiple_turns":
        events.append({"type": "turn.completed"})
    else:
        events[0], events[1] = events[1], events[0]
    with pytest.raises(AgentExecutionError):
        verify_trace(encode(), scope, proposal["proposal_id"])


def test_cancel_stops_owned_worker_even_if_storage_is_unavailable(tmp_path, monkeypatch):
    designs = DesignManager(tmp_path / "designs.sqlite3", tmp_path / "assets")

    async def scenario():
        manager = DesignAgentManager(designs, enabled=True, origin="http://127.0.0.1:8000")
        stopped = asyncio.Event()
        cleaning = asyncio.Event()
        release = asyncio.Event()

        async def running():
            try:
                await asyncio.sleep(60)
            finally:
                cleaning.set()
                await release.wait()
                stopped.set()

        task = asyncio.create_task(running())
        manager.tasks["request-001"] = AgentWorker("doc_owned", task)
        await asyncio.sleep(0)

        def fail(*args, **kwargs):
            raise StorageError()

        monkeypatch.setattr(manager, "_finish", fail)
        monkeypatch.setattr(manager, "_row", fail)
        first = asyncio.create_task(manager.cancel("doc_owned", "request-001"))
        await asyncio.wait_for(cleaning.wait(), 1)
        second = asyncio.create_task(manager.cancel("doc_owned", "request-001"))
        await asyncio.sleep(0)
        release.set()
        results = await asyncio.gather(first, second, return_exceptions=True)
        assert all(isinstance(result, StorageError) for result in results)
        assert task.cancelling() == 1
        assert stopped.is_set() and task.done()
        manager.tasks.clear()
        await manager.close()

    try:
        asyncio.run(scenario())
    finally:
        designs.close()
