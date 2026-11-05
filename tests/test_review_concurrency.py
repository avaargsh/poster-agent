"""Concurrency regressions for per-run review serialization.

Use the real SQLite checkpointer and Graph nodes; only the remote Qwen
compiler is replaced with a deterministic, explicitly gated local stub.
"""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event

from app import brief_request_store
from app.brief_compiler import BriefRequest, compile_brief
from app.review_service import ReviewConflict, create_run, decide_run, get_run
from app.schemas import PosterSpec
from app.workflows import review_graph

BRIEF = "主标题：双十一电动牙刷，活动价199元，原价399元，风格：科技蓝色"
SPEC = {
    "title": "双十一电动牙刷",
    "subtitle": "品质生活 · 限时优惠",
    "price": "199",
    "original_price": "399",
    "style": "科技蓝色",
    "cta": "立即抢购",
    "background_mode": "local",
}


def _configure(monkeypatch, tmp_path):
    monkeypatch.setenv("POSTER_CHECKPOINT_DB", str(tmp_path / "checkpoints.sqlite3"))


def _qwen_request() -> BriefRequest:
    return BriefRequest(brief=BRIEF, compiler="qwen")


def test_slow_qwen_does_not_block_other_run_reads_or_approval(tmp_path, monkeypatch):
    _configure(monkeypatch, tmp_path)
    existing = create_run(BriefRequest(brief=BRIEF, compiler="demo"))
    assert existing["pending_approval"] is True
    started = Event()
    release = Event()

    async def gated_compiler(request):
        if request.compiler == "qwen":
            started.set()
            assert release.wait(timeout=12), "Test release signal was not delivered"
        return await compile_brief(BriefRequest(brief=request.brief, compiler="demo"))

    monkeypatch.setattr(review_graph, "compile_brief", gated_compiler)
    with ThreadPoolExecutor(max_workers=3) as pool:
        slow = pool.submit(create_run, _qwen_request(), idempotency_key="review-slow-001")
        try:
            assert started.wait(timeout=10), "Qwen test compiler did not start"
            # A slow call on one run must not monopolize the entire review API.
            read = pool.submit(get_run, existing["run_id"])
            assert read.result(timeout=4)["pending_approval"] is True
            approval = pool.submit(
                decide_run, existing["run_id"], True, PosterSpec.model_validate(SPEC)
            )
            assert approval.result(timeout=4)["status"] == "APPROVED"
        finally:
            release.set()
        result = slow.result(timeout=10)
    assert result["pending_approval"] is True
    assert result["run_id"] != existing["run_id"]


def test_parallel_qwen_runs_can_compile_without_cross_run_lock(tmp_path, monkeypatch):
    _configure(monkeypatch, tmp_path)
    entered = Barrier(2, timeout=12)
    calls = []

    async def parallel_compiler(request):
        calls.append(request.brief)
        entered.wait()
        return await compile_brief(BriefRequest(brief=request.brief, compiler="demo"))

    monkeypatch.setattr(review_graph, "compile_brief", parallel_compiler)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(create_run, _qwen_request(), idempotency_key="review-parallel-01")
        second = pool.submit(create_run, _qwen_request(), idempotency_key="review-parallel-02")
        first_result, second_result = first.result(timeout=20), second.result(timeout=20)
    assert first_result["run_id"] != second_result["run_id"]
    assert len(calls) == 2
    assert first_result["pending_approval"] is True
    assert second_result["pending_approval"] is True


def test_inflight_same_key_replay_waits_and_never_reextracts(tmp_path, monkeypatch):
    _configure(monkeypatch, tmp_path)
    started = Event()
    release = Event()
    calls = []

    async def gated_compiler(request):
        calls.append(request.brief)
        started.set()
        assert release.wait(timeout=12), "Test release signal was not delivered"
        return await compile_brief(BriefRequest(brief=request.brief, compiler="demo"))

    monkeypatch.setattr(review_graph, "compile_brief", gated_compiler)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(create_run, _qwen_request(), idempotency_key="review-same-key-01")
        try:
            assert started.wait(timeout=10)
            second = pool.submit(create_run, _qwen_request(), idempotency_key="review-same-key-01")
        finally:
            release.set()
        first_result, second_result = first.result(timeout=10), second.result(timeout=10)
    assert first_result["run_id"] == second_result["run_id"]
    assert len(calls) == 1
    assert first_result["draft"] == second_result["draft"]


def test_parallel_conflicting_approvals_have_one_terminal_winner(tmp_path, monkeypatch):
    _configure(monkeypatch, tmp_path)
    original = create_run(BriefRequest(brief=BRIEF, compiler="demo"))
    first_spec = PosterSpec.model_validate(SPEC)
    second_spec = PosterSpec.model_validate({**SPEC, "price": "188"})
    with ThreadPoolExecutor(max_workers=2) as pool:
        choices = (
            pool.submit(decide_run, original["run_id"], True, first_spec),
            pool.submit(decide_run, original["run_id"], True, second_spec),
        )
        outcomes = []
        for future in choices:
            try:
                outcomes.append(future.result(timeout=10))
            except ReviewConflict:
                outcomes.append("CONFLICT")

    accepted = [result for result in outcomes if isinstance(result, dict)]
    assert len(accepted) == 1
    assert outcomes.count("CONFLICT") == 1
    final = get_run(original["run_id"])
    assert final["status"] == "APPROVED"
    assert final["confirmed_spec"] == accepted[0]["confirmed_spec"]


def test_same_key_replay_cannot_overtake_original_claim(tmp_path, monkeypatch):
    _configure(monkeypatch, tmp_path)
    claimed = Event()
    allow_original = Event()
    second_entered = Event()
    replay_claimed = Event()
    calls = []
    original_claim = brief_request_store.claim

    def gated_claim(key, request_hash, run_id):
        result = original_claim(key, request_hash, run_id)
        if result["claimed"]:
            claimed.set()
            assert allow_original.wait(timeout=12), "Original admission gate not released"
        else:
            replay_claimed.set()
        return result

    async def compiler(request):
        calls.append(request.brief)
        return await compile_brief(BriefRequest(brief=request.brief, compiler="demo"))

    def retry():
        second_entered.set()
        return create_run(_qwen_request(), idempotency_key="review-claim-race-01")

    monkeypatch.setattr(brief_request_store, "claim", gated_claim)
    monkeypatch.setattr(review_graph, "compile_brief", compiler)

    with ThreadPoolExecutor(max_workers=2) as pool:
        original = pool.submit(create_run, _qwen_request(), idempotency_key="review-claim-race-01")
        try:
            assert claimed.wait(timeout=10)
            duplicate = pool.submit(retry)
            assert second_entered.wait(timeout=10)
            # The retry must wait on the request key, not observe an empty
            # checkpoint while the first claimant has yet to enter its Graph.
            assert not replay_claimed.wait(timeout=0.5)
            assert not duplicate.done()
        finally:
            allow_original.set()
        original_result = original.result(timeout=10)
        duplicate_result = duplicate.result(timeout=10)

    assert replay_claimed.is_set()
    assert original_result["run_id"] == duplicate_result["run_id"]
    assert original_result["pending_approval"] is True
    assert len(calls) == 1
    assert brief_request_store.get("review-claim-race-01")["status"] == "COMPLETED"
