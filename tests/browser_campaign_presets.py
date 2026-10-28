"""Opt-in real Chromium smoke for reusable campaign copy presets.

Tests the actual local FastAPI page and API. No image generation, payment,
OCR, customer assets or automatic acceptance occurs.
"""
from __future__ import annotations

import csv
import io
import socket
import subprocess
import sys
import time
import tempfile
from urllib.request import urlopen

from playwright.sync_api import sync_playwright

from browser_support import browser_test_environment, chromium_launch_options


def port_available():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def verify(condition, description):
    if not condition:
        raise AssertionError(description)


def _run(env):
    port = port_available()
    origin = f"http://127.0.0.1:{port}"
    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app",
         "--host", "127.0.0.1", "--port", str(port), "--no-access-log"],
        stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT, env=env,
    )
    try:
        for _ in range(80):
            if server.poll() is not None:
                raise RuntimeError("FastAPI exited before browser smoke")
            try:
                with urlopen(origin + "/", timeout=1) as response:
                    if response.status == 200:
                        break
            except OSError:
                time.sleep(0.15)
        else:
            raise RuntimeError("FastAPI did not start")

        with sync_playwright() as pw:
            options = chromium_launch_options()
            browser = pw.chromium.launch(
                headless=True, args=["--no-sandbox"], **options
            )
            try:
                page = browser.new_page(
                    viewport={"width": 1440, "height": 1000},
                    accept_downloads=True,
                )
                page.goto(origin + "/", wait_until="domcontentloaded")
                page.locator("#nav-batch").click()
                page.wait_for_function(
                    "() => !document.querySelector('#campaignPreset').disabled"
                )
                manifest = (
                    "sku_id,product_image,title,price,original_price,subtitle,cta,style,logo_image\n"
                    "SK01,one.png,真实标题,199,399,,,,\n"
                )
                page.locator("#batchManifest").set_input_files({
                    "name": "products.csv", "mimeType": "text/csv",
                    "buffer": manifest.encode("utf-8"),
                })
                page.locator("#campaignPreset").select_option("nature")
                with page.expect_download() as handle:
                    page.locator("#applyCampaignPreset").click()
                csv_data = handle.value.path().read_text(encoding="utf-8-sig")
                records = list(csv.DictReader(io.StringIO(csv_data)))
                verify(len(records) == 1, "expected one SKU in download")
                verify(records[0]["title"] == "真实标题", "template rewrote title")
                verify(records[0]["price"] == "199", "template rewrote price")
                verify(records[0]["original_price"] == "399", "template rewrote original price")
                verify(records[0]["style"] == "绿色自然", "template did not fill style")
                verify("原 CSV 未修改" in page.locator(
                    "#campaignPresetStatus"
                ).inner_text(), "download handoff did not complete")

                page.locator("#nav-create").click()
                page.wait_for_function(
                    "() => !document.querySelector('#createCampaignPreset').disabled"
                )
                page.locator("#createCampaignPreset").select_option("tech")
                page.locator("#applySingleCampaignPreset").click()
                verify(page.locator("#create-style").input_value() == "蓝色科技风",
                       "single preset style missing")
                verify(page.locator("#create-cta").input_value() == "查看详情",
                       "single preset CTA missing")
                verify(page.locator("#create-price").input_value() == "199",
                       "single preset changed price")
                page.evaluate("() => { activeReviewRunId='synthetic-run'; activeReviewApproved=true; }")
                page.locator("#createCampaignPreset").select_option("fashion")
                page.locator("#applySingleCampaignPreset").click()
                verify(page.locator("#create-cta").input_value() == "查看详情",
                       "approved Brief guard failed")
                verify("清除任务" in page.locator(
                    "#createCampaignPresetStatus"
                ).inner_text(), "missing saved-Brief warning")
                print("PASS: campaign CSV download, immutable price/title, "
                      "single-poster form preset and Brief guard in Chromium")
            finally:
                browser.close()
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=5)


def main():
    with tempfile.TemporaryDirectory(prefix="poster-browser-") as directory:
        _run(browser_test_environment(directory))


if __name__ == "__main__":
    main()
