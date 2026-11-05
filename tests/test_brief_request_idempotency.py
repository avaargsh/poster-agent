"""HTTP contract: Qwen requests are admitted once, and replay never re-extracts."""

from uuid import uuid4

from fastapi.testclient import TestClient

from app import brief_request_store
from app.brief_compiler import BriefRequest, compile_brief
from app.main import app
from app.review_service import config, open_graph
from app.workflows import review_graph

BRIEF = "主标题：双十一电动牙刷，活动价199元，原价399元，风格：科技蓝色"


def configure(monkeypatch, tmp_path):
    monkeypatch.setenv("POSTER_CHECKPOINT_DB", str(tmp_path / "checkpoints.sqlite3"))
    monkeypatch.setenv("POSTER_EXECUTION_DB", str(tmp_path / "executions.sqlite3"))
    monkeypatch.setenv("POSTER_DATA_DIR", str(tmp_path / "posters"))
    monkeypatch.setenv("POSTER_DESIGN_DB", str(tmp_path / "designs.sqlite3"))
    monkeypatch.setenv("POSTER_DESIGN_ASSET_DIR", str(tmp_path / "design-assets"))


def test_qwen_requires_key_before_external_compiler(tmp_path, monkeypatch):
    configure(monkeypatch, tmp_path)
    with TestClient(app) as client:
        response = client.post("/api/briefs/runs", json={"brief": BRIEF, "compiler": "qwen"})
    assert response.status_code == 428
    assert not (tmp_path / "checkpoints.sqlite3").exists()


def test_qwen_key_replay_and_conflict_without_duplicate_compiler(tmp_path, monkeypatch):
    configure(monkeypatch, tmp_path)
    calls = []

    async def fake_compiler(request):
        calls.append(request.brief)
        return await compile_brief(BriefRequest(brief=request.brief, compiler="demo"))

    monkeypatch.setattr(review_graph, "compile_brief", fake_compiler)
    headers = {"Idempotency-Key": "brief-qwen-test-001"}
    with TestClient(app) as client:
        first = client.post(
            "/api/briefs/runs", json={"brief": BRIEF, "compiler": "qwen"}, headers=headers
        )
        assert first.status_code == 201, first.text
        second = client.post(
            "/api/briefs/runs", json={"brief": BRIEF, "compiler": "qwen"}, headers=headers
        )
        assert second.status_code == 201, second.text
        assert second.json()["run_id"] == first.json()["run_id"]
        assert len(calls) == 1
        record = client.get("/api/briefs/runs/by-key/brief-qwen-test-001")
        assert record.status_code == 200
        assert record.json()["status"] == "COMPLETED"
        assert record.json()["run_id"] == first.json()["run_id"]
        conflicting = client.post(
            "/api/briefs/runs",
            json={"brief": BRIEF + "（变更版本）", "compiler": "qwen"},
            headers=headers,
        )
        assert conflicting.status_code == 409
        assert len(calls) == 1


def test_unresolved_claim_never_dispatches_provider(tmp_path, monkeypatch):
    configure(monkeypatch, tmp_path)
    request = BriefRequest(brief=BRIEF, compiler="qwen")
    run_id = uuid4().hex
    digest = brief_request_store.fingerprint(request.model_dump(mode="json"))
    brief_request_store.claim("brief-qwen-unresolved-001", digest, run_id)
    calls = []

    async def should_not_call(_):
        calls.append(1)
        raise AssertionError("Qwen must not be re-dispatched")

    monkeypatch.setattr(review_graph, "compile_brief", should_not_call)
    with TestClient(app) as client:
        response = client.post(
            "/api/briefs/runs",
            json=request.model_dump(mode="json"),
            headers={"Idempotency-Key": "brief-qwen-unresolved-001"},
        )
        assert response.status_code == 409, response.text
        assert run_id in response.json()["detail"]
        record = client.get("/api/briefs/runs/by-key/brief-qwen-unresolved-001")
        assert record.json()["status"] == "UNKNOWN"
        assert record.json()["run_id"] == run_id
    assert calls == []


def test_lost_response_after_checkpoint_reuses_original_run(tmp_path, monkeypatch):
    configure(monkeypatch, tmp_path)
    request = BriefRequest(brief=BRIEF, compiler="qwen")
    run_id = uuid4().hex
    digest = brief_request_store.fingerprint(request.model_dump(mode="json"))
    key = "brief-qwen-lost-ack-001"
    brief_request_store.claim(key, digest, run_id)
    calls = []

    async def fake_compiler(payload):
        calls.append(payload.brief)
        return await compile_brief(BriefRequest(brief=payload.brief, compiler="demo"))

    monkeypatch.setattr(review_graph, "compile_brief", fake_compiler)
    # Simulate a crash after LangGraph committed the approval checkpoint, but
    # before the request ledger was marked COMPLETED or the HTTP reply delivered.
    with open_graph() as graph:
        graph.invoke({"request": request.model_dump(mode="json")}, config(run_id))
    with TestClient(app) as client:
        reused = client.post(
            "/api/briefs/runs",
            json=request.model_dump(mode="json"),
            headers={"Idempotency-Key": key},
        )
        assert reused.status_code == 201, reused.text
        assert reused.json()["run_id"] == run_id
        assert reused.json()["pending_approval"] is True
        assert client.get("/api/briefs/runs/by-key/" + key).json()["status"] == "COMPLETED"
    assert len(calls) == 1
