"""Explicit real-browser M6 OCR capture control smoke (synthetic API only).

Exercise the actual FastAPI HTML and JavaScript in headless Chromium.
Only the OCR capture status/launch and overlay status endpoints are stubbed.
Never runs PaddleOCR, touches user assets or counts as model attestation.
"""
from __future__ import annotations

import json
import socket
import subprocess
import sys
import time
import tempfile
from urllib.request import urlopen

from playwright.sync_api import sync_playwright

from browser_support import browser_test_environment, chromium_launch_options


def require(condition: bool, message: str):
    if not condition:
        raise AssertionError(message)


def available_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _run(env):
    port = available_port()
    base_url = f"http://127.0.0.1:{port}"
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app",
         "--host", "127.0.0.1", "--port", str(port), "--no-access-log"],
        stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT, env=env,
    )
    try:
        for attempt in range(90):
            if process.poll() is not None:
                raise RuntimeError("FastAPI server exited before the OCR browser smoke")
            try:
                with urlopen(base_url + "/", timeout=1) as response:
                    if response.status == 200:
                        break
            except OSError:
                time.sleep(0.1)
        else:
            raise RuntimeError("FastAPI server did not start")

        batch_id = "m6-browser-capture-A"
        state = {"kind": "NOT_CONFIGURED", "ready": False, "done": None,
                 "expected": None, "posts": 0, "overlay_gets": 0, "status_gets": 0}

        def fake_capture(route):
            if route.request.method == "POST":
                state["posts"] += 1
                require(state["ready"], "POST unexpectedly sent when capture unavailable")
                state.update(kind="RUNNING", ready=False, done=None, expected=None)
                route.fulfill(
                    status=202, content_type="application/json",
                    body=json.dumps({"state": "RUNNING", "quality_verified": False,
                                     "automatically_accepted": False,
                                     "runtime_attested": False}),
                )
                return
            state["status_gets"] += 1
            response = {"state": state["kind"], "ready_to_capture": state["ready"],
                        "quality_verified": False, "automatically_accepted": False,
                        "runtime_attested": False}
            if state["kind"] == "RUNNING" and state["done"] is not None:
                response.update(completed_variants=state["done"],
                                expected_variants=state["expected"])
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps(response))

        def fake_overlay(route):
            state["overlay_gets"] += 1
            route.fulfill(
                status=200, content_type="application/json",
                body=json.dumps({"ocr_capture_present": True,
                                 "overlay_prepared": False}),
            )

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=True, args=["--no-sandbox"],
                **chromium_launch_options(),
            )
            try:
                page = browser.new_page(viewport={"width": 1440, "height": 1000})
                page.route("**/api/batches/*/m6-ocr-capture", fake_capture)
                page.route("**/api/batches/*/m6-overlay", fake_overlay)
                page.goto(base_url + "/", wait_until="domcontentloaded")
                page.locator("#nav-batch").click()
                page.evaluate("""() => {
                    currentBatchId='m6-browser-capture-A';
                    lastBatchStatus='COMPLETED';
                    document.querySelector('#batchResults').hidden=false;
                    document.querySelector('#captureM6OCR').closest('details').open=true;
                }""")
                capture_button = page.locator("#captureM6OCR")
                label = page.locator("#m6OCRCaptureStatus")

                page.evaluate(f"() => refreshM6OCRCaptureStatus('{batch_id}')")
                require(capture_button.is_disabled(), "unconfigured OCR must not launch")
                require("无法运行" in label.inner_text(), "missing-model guidance absent")

                state.update(kind="READY", ready=True)
                page.evaluate(f"() => refreshM6OCRCaptureStatus('{batch_id}')")
                require(capture_button.is_enabled(), "ready OCR must allow explicit launch")
                require(state["posts"] == 0, "status check must not launch inference")

                capture_button.click()
                page.wait_for_function(
                    "() => document.getElementById('m6OCRCaptureStatus')"
                    ".textContent.includes('正在初始化模型')"
                )
                require(state["posts"] == 1, "explicit click must submit one capture request")
                require(capture_button.is_disabled(), "running OCR cannot be launched twice")

                state.update(done=2, expected=4)
                page.evaluate(f"() => refreshM6OCRCaptureStatus('{batch_id}')")
                require("2/4" in label.inner_text(), "completed variant count missing")
                require("不是质量验收" in label.inner_text(),
                        "in-memory progress incorrectly described as verified")

                state.update(kind="FAILED_NO_EVIDENCE", ready=True,
                             done=None, expected=None)
                page.evaluate(f"() => refreshM6OCRCaptureStatus('{batch_id}')")
                require(capture_button.is_enabled(), "failed no-evidence capture must retry")
                require("显式重试" in label.inner_text(), "retry requires operator guidance")
                require(state["posts"] == 1, "failed capture must never auto-retry")
                capture_button.click()
                page.wait_for_function(
                    "() => document.getElementById('m6OCRCaptureStatus')"
                    ".textContent.includes('正在初始化模型')"
                )
                require(state["posts"] == 2, "operator retry should start exactly once")

                state.update(kind="FAILED", ready=False)
                page.evaluate(f"() => refreshM6OCRCaptureStatus('{batch_id}')")
                require(capture_button.is_disabled(), "unavailable OCR must prevent retry")

                state.update(kind="BLOCKED", ready=False)
                page.evaluate(f"() => refreshM6OCRCaptureStatus('{batch_id}')")
                require(capture_button.is_disabled(), "blocked immutable evidence forbids overwrite")
                require("禁止覆盖" in label.inner_text(), "blocked evidence warning absent")

                state.update(kind="EVIDENCE_PRESENT_UNATTESTED", ready=False)
                page.evaluate(f"() => refreshM6OCRCaptureStatus('{batch_id}')")
                page.wait_for_function(
                    "() => document.getElementById('m6OCRCaptureStatus')"
                    ".textContent.includes('不能据此证明真实执行')"
                )
                require(capture_button.is_disabled(), "saved evidence forbids duplicate OCR")
                require(state["overlay_gets"] >= 1, "recorded OCR must refresh overlay controls")
                require(state["posts"] == 2, "proof recognition cannot launch OCR")

                # In-flight responses from a prior batch must not mutate the new batch UI.
                previous_gets = state["status_gets"]
                page.evaluate("""() => {
                    currentBatchId='different-batch';
                    return refreshM6OCRCaptureStatus('m6-browser-capture-A');
                }""")
                require(state["status_gets"] == previous_gets,
                        "stale batch should not query the old capture endpoint")
                print("PASS: real Chromium + FastAPI OCR capture readiness, operator launch, "
                      "progress, no-evidence retry, failed config, immutable block, "
                      "unattested evidence, and batch isolation")
            finally:
                browser.close()
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def main():
    with tempfile.TemporaryDirectory(prefix="poster-browser-") as directory:
        _run(browser_test_environment(directory))


if __name__ == "__main__":
    main()
