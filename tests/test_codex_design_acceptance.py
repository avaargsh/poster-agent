from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest

from app.designs import DesignRepository
from app.designs.proposals import EditProposalRequest
from scripts.verify_codex_design import (
    TARGET_TITLE,
    TOOLS,
    codex_command,
    verify_candidate,
    verify_tool_trace,
)


@pytest.fixture
def fixture(tmp_path, product_bytes):
    repo = DesignRepository(tmp_path / "designs.sqlite3", tmp_path / "assets")
    before = repo.create(title="原始标题", price=Decimal("199.90"), product_bytes=product_bytes)
    proposal = repo.propose_edit(
        before.document_id,
        EditProposalRequest.model_validate(
            {
                "request_id": "acceptance-test-001",
                "expected_revision": 1,
                "summary": "调整标题与价格字号",
                "operations": [
                    {"op": "set_text", "node_id": "title", "text": TARGET_TITLE},
                    {"op": "set_style", "node_id": "price", "font_size": 100},
                ],
            }
        ),
    )
    return before.model_dump(mode="json"), proposal


def test_candidate_acceptance_is_readonly_and_checks_collateral_changes(fixture):
    before, proposal = fixture
    original = deepcopy(proposal)
    verify_candidate(before, proposal, before)
    assert proposal == original
    candidate = proposal["candidate_document"]
    next(node for node in candidate["nodes"] if node["role"] == "product")["x"] += 5
    with pytest.raises(ValueError, match="outside"):
        verify_candidate(before, proposal, before)


def test_candidate_acceptance_rejects_saved_writes_and_extra_operations(fixture):
    before, proposal = fixture
    with pytest.raises(ValueError, match="saved"):
        verify_candidate(before, proposal, {**before, "revision": 2})
    proposal["operations"].append({"op": "transform_node", "node_id": "product", "x": 10})
    with pytest.raises(ValueError, match="two requested"):
        verify_candidate(before, proposal, before)


def test_candidate_acceptance_rejects_price_amount_and_line_position_changes(fixture):
    before, proposal = fixture
    price = next(
        node for node in proposal["candidate_document"]["nodes"] if node["role"] == "price"
    )
    price["lines"][0]["x"] += 5
    with pytest.raises(ValueError, match="price style or amount"):
        verify_candidate(before, proposal, before)


def test_candidate_acceptance_preserves_title_line_positions(fixture):
    before, proposal = fixture
    title = next(
        node for node in proposal["candidate_document"]["nodes"] if node["role"] == "title"
    )
    title["lines"][0]["x"] += 9999
    with pytest.raises(ValueError, match="outside"):
        verify_candidate(before, proposal, before)


def trace(before, proposal):
    events = []
    for tool in TOOLS:
        arguments = {"document_id": before["document_id"]}
        result = proposal
        if tool == "read_design":
            result = {"document": before}
        elif tool == "propose_design_edit":
            arguments["proposal"] = {"request_id": "acceptance-test-001", "expected_revision": 1}
        else:
            arguments["proposal_id"] = proposal["proposal_id"]
        events.append(
            {
                "type": "item.completed",
                "item": {
                    "type": "mcp_tool_call",
                    "tool": tool,
                    "server": "poster_agent",
                    "status": "completed",
                    "arguments": arguments,
                    "result": {"structured_content": result},
                    "error": None,
                },
            }
        )
    return events + [{"type": "turn.completed"}]


@pytest.mark.parametrize(
    "mutation",
    [
        "other_design",
        "other_proposal",
        "other_request",
        "bad_result",
        "wrong_order",
        "incomplete_turn",
    ],
)
def test_tool_trace_requires_correlated_successful_ordered_calls(fixture, mutation):
    before, proposal = fixture
    events = trace(before, proposal)
    verify_tool_trace(events, before, proposal["proposal_id"], "acceptance-test-001")
    if mutation == "other_design":
        events[0]["item"]["arguments"]["document_id"] = "doc_other"
    elif mutation == "other_proposal":
        events[2]["item"]["arguments"]["proposal_id"] = "prop_other"
    elif mutation == "other_request":
        events[1]["item"]["arguments"]["proposal"]["request_id"] = "other-request"
    elif mutation == "bad_result":
        events[2]["item"]["result"] = {"structured_content": {"error": "unavailable"}}
    elif mutation == "wrong_order":
        events[0], events[1] = events[1], events[0]
    else:
        events[-1]["type"] = "turn.failed"
    with pytest.raises(ValueError):
        verify_tool_trace(events, before, proposal["proposal_id"], "acceptance-test-001")


def test_codex_invocation_limits_tools_without_persistent_configuration():
    args = codex_command("codex", "http://127.0.0.1:8133", Path("/repo"), Path("/tmp/host"))
    assert "--ignore-user-config" in args and "--ephemeral" in args and "--strict-config" in args
    assert args[args.index("--sandbox") + 1] == "read-only"
    assert (
        'mcp_servers.poster_agent.enabled_tools=["read_design", "propose_design_edit", "read_edit_proposal"]'
        in args
    )
    assert all(
        feature in args
        for feature in [
            "shell_tool",
            "plugins",
            "apps",
            "browser_use",
            "computer_use",
            "view_image",
        ]
    )
