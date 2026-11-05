"""Two-process, two-version migration gate for persisted LangGraph reviews.

Run 'seed' with the currently locked dependencies, then 'verify' with the
proposed upgraded dependencies on THE SAME SQLite database. No paid models,
network calls, production data, or temporary in-memory checkpointers.
"""

from __future__ import annotations

import argparse
import json
import os
from importlib.metadata import version
from pathlib import Path

BRIEF = "主标题：双十一电动牙刷，活动价199元，原价399元，风格：科技蓝色"
INCOMPLETE = "主标题：双十一电动牙刷，请设计有高级感的促销海报"
KEY = "compat-old-checkpoint-brief-001"
SPEC = {
    "title": "双十一电动牙刷",
    "subtitle": "品质生活 · 限时优惠",
    "price": "199",
    "original_price": "399",
    "style": "科技蓝色",
    "cta": "立即抢购",
    "background_mode": "local",
}

BASELINE = {
    "langgraph": "1.2.16",
    "langgraph-checkpoint": "4.3.0",
    "langgraph-checkpoint-sqlite": "3.1.2",
}
TARGET = {
    "langgraph": "1.2.19",
    "langgraph-checkpoint": "4.3.1",
    "langgraph-checkpoint-sqlite": "3.1.3",
}


def assert_versions(expected: dict[str, str]) -> None:
    current = {name: version(name) for name in expected}
    if current != expected:
        raise AssertionError(f"Incorrect migration environment: {current} != {expected}")
    print(f"Resolved LangGraph versions: {current}", flush=True)


def seed(database: Path, manifest: Path) -> None:
    assert_versions(BASELINE)
    from app.brief_compiler import BriefRequest
    from app.review_service import create_run, decide_run
    from app.schemas import PosterSpec

    if database.exists() or manifest.exists():
        raise RuntimeError("Refusing to overwrite pre-existing checkpoint fixture")

    stable_request = BriefRequest(brief=BRIEF, compiler="demo")
    pending = create_run(stable_request, idempotency_key=KEY)
    assert pending["pending_approval"] is True
    assert pending["status"] == "WAITING_CONFIRMATION"

    rejected = create_run(stable_request)
    rejected = decide_run(rejected["run_id"], False, None)
    assert rejected["status"] == "REJECTED"

    approved = create_run(stable_request)
    approved = decide_run(approved["run_id"], True, PosterSpec.model_validate(SPEC))
    assert approved["status"] == "APPROVED"

    incomplete = create_run(BriefRequest(brief=INCOMPLETE, compiler="demo"))
    assert incomplete["status"] == "WAITING_INPUT"
    assert incomplete["pending_approval"] is True

    records = {
        "pending": pending["run_id"],
        "rejected": rejected["run_id"],
        "approved": approved["run_id"],
        "incomplete": incomplete["run_id"],
        "key": KEY,
        "request": stable_request.model_dump(mode="json"),
        "confirmed_spec": approved["confirmed_spec"],
    }
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    assert database.exists()
    print("Baseline persisted: pending/rejected/approved/missing-field checkpoints", flush=True)


def verify(database: Path, manifest: Path) -> None:
    assert_versions(TARGET)
    if not database.is_file() or not manifest.is_file():
        raise RuntimeError("Old-version SQLite checkpoint fixture is missing")

    from app.brief_compiler import BriefRequest
    from app.review_service import ReviewConflict, create_run, decide_run, get_run
    from app.schemas import PosterSpec
    from app.workflows import review_graph

    saved = json.loads(manifest.read_text(encoding="utf-8"))
    stable_request = BriefRequest.model_validate(saved["request"])

    async def no_second_extraction(_request):
        raise AssertionError("Already-checkpointed extract node ran again across version upgrade")

    # Fail closed: new version may read and resume an approval, but must not
    # repeat previously checkpointed extraction or issue an external call.
    review_graph.compile_brief = no_second_extraction

    pending = get_run(saved["pending"])
    assert pending["status"] == "WAITING_CONFIRMATION"
    assert pending["pending_approval"] is True
    assert pending["background_generation_started"] is False
    assert pending["draft"]["fields"]["price"] == "199"

    replayed = create_run(stable_request, idempotency_key=KEY)
    assert replayed["run_id"] == saved["pending"]
    assert replayed["draft"] == pending["draft"]

    approved = get_run(saved["approved"])
    assert approved["status"] == "APPROVED"
    assert approved["pending_approval"] is False
    assert approved["confirmed_spec"] == saved["confirmed_spec"]
    assert decide_run(saved["approved"], True, PosterSpec.model_validate(SPEC)) == approved
    try:
        decide_run(
            saved["approved"],
            True,
            PosterSpec.model_validate({**SPEC, "price": "188"}),
        )
    except ReviewConflict:
        pass
    else:
        raise AssertionError("Modified approval unexpectedly accepted on an old checkpoint")

    rejected = get_run(saved["rejected"])
    assert rejected["status"] == "REJECTED"
    assert rejected["pending_approval"] is False
    assert decide_run(saved["rejected"], False, None) == rejected

    incomplete = get_run(saved["incomplete"])
    assert incomplete["status"] == "WAITING_INPUT"
    assert incomplete["pending_approval"] is True

    resumed = decide_run(saved["pending"], True, PosterSpec.model_validate(SPEC))
    assert resumed["status"] == "APPROVED"
    assert resumed["pending_approval"] is False
    assert resumed["confirmed_spec"]["price"] == "199"
    assert decide_run(saved["pending"], True, PosterSpec.model_validate(SPEC)) == resumed
    assert get_run(saved["pending"])["status"] == "APPROVED"
    print("Upgraded version read/replayed/resumed original SQLite checkpoints", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("seed", "verify"))
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    args = parser.parse_args()
    os.environ["POSTER_CHECKPOINT_DB"] = str(args.database.resolve())
    if args.phase == "seed":
        seed(args.database, args.manifest)
    else:
        verify(args.database, args.manifest)


if __name__ == "__main__":
    main()
