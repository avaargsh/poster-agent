"""Shared ephemeral Codex host configuration for account-backed design tasks."""

import json
import sys
from pathlib import Path

from .agent_contracts import AgentScope

TOOLS = ["read_design", "propose_design_edit", "read_edit_proposal"]


def codex_command(
    executable: str, origin: str, root: Path, work: Path, scope: AgentScope | None = None
) -> list[str]:
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
    if scope is not None:
        settings["mcp_servers.poster_agent.args"] += [
            "--document-id",
            scope.document_id,
            "--request-id",
            scope.request_id,
            "--expected-revision",
            str(scope.expected_revision),
        ]
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
