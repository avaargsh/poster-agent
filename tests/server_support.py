"""Actual loopback service with isolated stores for protocol and browser tests."""

import os
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

import httpx


@contextmanager
def local_backend(directory: Path, *, enable_agent: bool = False, codex_bin: Path | None = None):
    directory.mkdir(parents=True, exist_ok=True)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    origin = f"http://127.0.0.1:{port}"
    env = {
        **os.environ,
        **{
            key: str(directory / name)
            for key, name in [
                ("POSTER_DATA_DIR", "posters"),
                ("POSTER_EXECUTION_DB", "executions.sqlite3"),
                ("POSTER_CHECKPOINT_DB", "checkpoints.sqlite3"),
                ("POSTER_DESIGN_DB", "designs.sqlite3"),
                ("POSTER_DESIGN_ASSET_DIR", "assets"),
            ]
        },
        "POSTER_DESIGN_AGENT_ENABLED": "1" if enable_agent else "0",
        "POSTER_DESIGN_AGENT_BASE_URL": origin,
    }
    if codex_bin is not None:
        env["PATH"] = str(codex_bin) + os.pathsep + env.get("PATH", "")
    with (directory / "server.log").open("wb") as log:
        server = subprocess.Popen(
            [sys.executable, "-m", "app.run_local", "--port", str(port)],
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        try:
            with httpx.Client(base_url=origin, trust_env=False, timeout=5) as client:
                for _ in range(80):
                    if server.poll() is not None:
                        raise RuntimeError("Disposable backend exited during startup")
                    try:
                        if client.get("/health").status_code == 200:
                            break
                    except httpx.HTTPError:
                        time.sleep(0.15)
                else:
                    raise RuntimeError("Disposable backend did not start")
                yield client
        finally:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)
