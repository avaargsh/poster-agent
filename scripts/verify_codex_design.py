"""Opt-in, real Codex account acceptance against a local Poster Agent backend.

Uses a programmatic product and a new design. The model receives only that
design through three MCP tools. Successful verification leaves a pending
suggestion for explicit review in the canvas.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

import httpx

from app.mcp_server import validate_base_url
from scripts.design_demo import _programmatic_product_png

TOOLS = ["read_design", "propose_design_edit", "read_edit_proposal"]
TARGET_TITLE = "轻松开启亮白微笑"


def codex_command(executable: str, origin: str, root: Path, work: Path) -> list[str]:
    command = [
        executable,
        "exec",
        "--ignore-user-config",
        "--ephemeral",
        "--json",
        "--strict-config",
        "--sandbox",
        "read-only",
        "--skip-git-repo-check",
        "--cd",
        str(work),
    ]
    settings = {
        "approval_policy": "never",
        "web_search": "disabled",
        "mcp_servers.poster_agent.command": sys.executable,
        "mcp_servers.poster_agent.args": ["-m", "app.mcp_server", "--base-url", origin],
        "mcp_servers.poster_agent.cwd": str(root),
        "mcp_servers.poster_agent.required": True,
        "mcp_servers.poster_agent.enabled_tools": TOOLS,
    }
    for key, value in settings.items():
        command.extend(["-c", key + "=" + json.dumps(value, ensure_ascii=False)])
    for feature in [
        "shell_tool",
        "plugins",
        "apps",
        "multi_agent",
        "multi_agent_v2",
        "browser_use",
        "computer_use",
        "view_image",
    ]:
        command.extend(["--disable", feature])
    return command


def verify_candidate(before: dict, proposal: dict, current: dict) -> None:
    if current != before or proposal["status"] != "PENDING" or proposal["base_revision"] != 1:
        raise ValueError("model task changed the saved design or returned a non-pending proposal")
    expected_operations = [
        {"op": "set_text", "node_id": "title", "text": TARGET_TITLE},
        {"op": "set_style", "node_id": "price", "font_size": 100},
    ]
    operations = [
        {key: value for key, value in operation.items() if value is not None}
        for operation in proposal["operations"]
    ]
    if sorted(operations, key=lambda item: item["node_id"]) != sorted(
        expected_operations, key=lambda item: item["node_id"]
    ):
        raise ValueError("suggestion operations differ from the two requested edits")
    candidate = deepcopy(proposal["candidate_document"])
    baseline = deepcopy(before)
    for expected, actual in zip(baseline["nodes"], candidate["nodes"], strict=True):
        if expected["role"] == "title":
            if "".join(line["text"] for line in actual["lines"]) != TARGET_TITLE:
                raise ValueError("candidate title differs from the requested title")
            for expected_line, actual_line in zip(expected["lines"], actual["lines"], strict=True):
                actual_line["text"] = expected_line["text"]
        elif expected["role"] == "price":
            if actual["font_size"] != 100 or actual["lines"] != expected["lines"]:
                raise ValueError("candidate price style or amount differs from the request")
            actual["font_size"] = expected["font_size"]
    if candidate != baseline:
        raise ValueError("candidate changed content outside the requested fields")


def verify_tool_trace(
    events: list[dict], before: dict, proposal_id: str, request_id: str
) -> list[str]:
    if not any(event.get("type") == "turn.completed" for event in events) or any(
        event.get("type") in {"turn.failed", "error"} for event in events
    ):
        raise ValueError("Codex did not complete a successful turn")
    calls = iter(
        event["item"]
        for event in events
        if event.get("type") == "item.completed"
        and event.get("item", {}).get("type") == "mcp_tool_call"
    )
    for tool in TOOLS:
        call = next((item for item in calls if item.get("tool") == tool), None)
        if (
            call is None
            or call.get("server") != "poster_agent"
            or call.get("status") != "completed"
            or call.get("error") is not None
        ):
            raise ValueError("successful ordered MCP read/propose/status calls were not observed")
        arguments = call.get("arguments") or {}
        result = (call.get("result") or {}).get("structured_content") or {}
        if arguments.get("document_id") != before["document_id"]:
            raise ValueError("MCP call targets another design")
        if tool == "read_design":
            if result.get("document") != before:
                raise ValueError("MCP read did not return the initial design")
        else:
            if (
                result.get("proposal_id") != proposal_id
                or result.get("document_id") != before["document_id"]
                or result.get("status") != "PENDING"
            ):
                raise ValueError("MCP result does not match the pending suggestion")
            if tool == "propose_design_edit":
                request = arguments.get("proposal") or {}
                if request.get("request_id") != request_id or request.get("expected_revision") != 1:
                    raise ValueError("MCP proposal request does not match the acceptance request")
            elif arguments.get("proposal_id") != proposal_id:
                raise ValueError("MCP status query targets another suggestion")
    return list(TOOLS)


def run(origin: str, output: Path, timeout: int) -> dict:
    executable = shutil.which("codex")
    if executable is None:
        raise ValueError("codex CLI is required")
    login = subprocess.run(
        [executable, "login", "status"], capture_output=True, text=True, timeout=10
    )
    if login.returncode:
        raise ValueError("log in with codex login before running this acceptance")
    auth_mode = "chatgpt" if "chatgpt" in (login.stdout + login.stderr).lower() else "configured"
    output.mkdir(parents=True, exist_ok=False)
    work = output / "host"
    work.mkdir()
    root = Path(__file__).resolve().parents[1]
    with httpx.Client(base_url=origin, trust_env=False, timeout=20) as client:
        response = client.post(
            "/api/designs",
            data={"title": "清新洁净，从齿开始", "price": "199.90"},
            files={"product": ("product.png", _programmatic_product_png(), "image/png")},
        )
        response.raise_for_status()
        before = response.json()["document"]
        document_id = before["document_id"]
        request_id = str(uuid4())
        prompt = (
            f"Use only the poster_agent MCP tools. Read design {document_id}, then propose "
            f"exactly these two changes: set the title to {TARGET_TITLE!r} and set the price "
            "font size to 100. Preserve the price amount, product image, positions and all "
            f"other fields. Use request_id {request_id}. Base your proposal on the version "
            "you read. Read the resulting suggestion status. Do not apply it. Return its "
            "confirmation URL. This is a synthetic integration acceptance task."
        )
        command = codex_command(executable, origin, root, work)
        with (
            (output / "events.jsonl").open("w") as events,
            (output / "stderr.log").open("w") as errors,
        ):
            process = subprocess.Popen(
                command + [prompt], stdout=events, stderr=errors, start_new_session=True
            )
            try:
                code = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                raise ValueError("Codex task exceeded its time limit") from None
        if code:
            raise ValueError("Codex task failed; inspect the local stderr and events")
        events = [
            json.loads(line)
            for line in (output / "events.jsonl").read_text().splitlines()
            if line.strip()
        ]
        response = client.get(f"/api/designs/{document_id}/proposals")
        response.raise_for_status()
        summaries = response.json()["proposals"]
        if len(summaries) != 1:
            raise ValueError("expected exactly one stored suggestion")
        proposal_id = summaries[0]["proposal_id"]
        response = client.get(f"/api/designs/{document_id}/proposals/{proposal_id}")
        response.raise_for_status()
        proposal = response.json()
        current = client.get(f"/api/designs/{document_id}")
        current.raise_for_status()
        verify_candidate(before, proposal, current.json()["document"])
        completed = verify_tool_trace(events, before, proposal_id, request_id)
        report = {
            "status": "PASS_PENDING_CONFIRMATION",
            "synthetic_asset": True,
            "document_id": document_id,
            "proposal_id": proposal_id,
            "base_revision": 1,
            "current_revision": 1,
            "confirmation_url": origin + proposal["confirmation_url"],
            "observed_tools": sorted(completed),
            "usage": [
                event.get("usage") for event in events if event.get("type") == "turn.completed"
            ],
            "codex_version": subprocess.check_output([executable, "--version"], text=True).strip(),
            "auth_mode": auth_mode,
        }
        (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run-model", action="store_true", help="explicitly use the logged-in Codex account"
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument(
        "--output-dir", type=Path, required=True, help="new local evidence directory"
    )
    parser.add_argument("--timeout", type=int, default=120)
    args = parser.parse_args()
    if not args.run_model:
        parser.error("--run-model is required to authorize the real model call")
    if not 10 <= args.timeout <= 300:
        parser.error("--timeout must be between 10 and 300 seconds")
    try:
        report = run(validate_base_url(args.base_url), args.output_dir.resolve(), args.timeout)
    except (ValueError, OSError, httpx.HTTPError) as exc:
        parser.exit(1, f"Acceptance incomplete: {exc}\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
