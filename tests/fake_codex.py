"""Deterministic CLI stand-in. No model call or account access occurs here."""

import sys
from pathlib import Path

SCRIPT = r"""
import json, sys, time
from pathlib import Path
import httpx

args = sys.argv[1:]
settings = {}
for index, argument in enumerate(args):
    if argument == "-c":
        key, value = args[index + 1].split("=", 1)
        settings[key] = json.loads(value)
server = settings["mcp_servers.poster_agent.args"]


def argument(name):
    return server[server.index(name) + 1]


origin = argument("--base-url")
document = argument("--document-id")
request_id = argument("--request-id")
revision = int(argument("--expected-revision"))
prompt = sys.stdin.read()
with Path(__file__).with_name("calls.log").open("a") as log:
    log.write(request_id + "\n")
if "hold-task" in prompt:
    time.sleep(60)


def event(tool, arguments, result):
    print(
        json.dumps(
            {
                "type": "item.completed",
                "item": {
                    "type": "mcp_tool_call",
                    "server": "poster_agent",
                    "tool": tool,
                    "arguments": arguments,
                    "result": {"structured_content": result},
                    "error": None,
                    "status": "completed",
                },
            }
        ),
        flush=True,
    )


with httpx.Client(base_url=origin, trust_env=False, timeout=5) as client:
    response = client.get("/api/designs/" + document)
    response.raise_for_status()
    event("read_design", {"document_id": document}, response.json())
    body = {
        "request_id": request_id,
        "expected_revision": revision,
        "summary": "调整标题与活动价字号",
        "operations": [
            {"op": "set_text", "node_id": "title", "text": "AI 测试标题"},
            {"op": "set_style", "node_id": "price", "font_size": 100},
        ],
    }
    response = client.post("/api/designs/" + document + "/proposals", json=body)
    response.raise_for_status()
    proposal = response.json()
    event("propose_design_edit", {"document_id": document, "proposal": body}, proposal)
    response = client.get("/api/designs/" + document + "/proposals/" + proposal["proposal_id"])
    response.raise_for_status()
    event(
        "read_edit_proposal",
        {"document_id": document, "proposal_id": proposal["proposal_id"]},
        response.json(),
    )
print(
    json.dumps({"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 5}}),
    flush=True,
)
"""


def write_fake_codex(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "codex"
    path.write_text("#!" + sys.executable + "\n" + SCRIPT)
    path.chmod(0o755)
    return directory
