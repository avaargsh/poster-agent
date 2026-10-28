"""Opt-in Chromium acceptance for versioned square layout editing on real FastAPI.

Uses synthetic SKU pixels, local background, and temporary storage. Not a
real-customer image, OCR/visual quality attestation, or production benchmark.
"""

from __future__ import annotations

import io
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.request import urlopen

from browser_support import chromium_launch_options
from PIL import Image, ImageDraw
from playwright.sync_api import sync_playwright


def _port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _check(condition, label):
    if not condition:
        raise AssertionError(label)


def main():
    with tempfile.TemporaryDirectory(prefix="poster-layout-browser-") as directory:
        port = _port()
        origin = f"http://127.0.0.1:{port}"
        env = {
            **os.environ,
            "POSTER_DATA_DIR": directory + "/posters",
            "POSTER_EXECUTION_DB": directory + "/executions.sqlite3",
            "POSTER_CHECKPOINT_DB": directory + "/checkpoint.sqlite3",
        }
        log_path = Path(directory) / "server.log"
        server_log = log_path.open("wb")
        server = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "app.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--no-access-log",
            ],
            env=env,
            stdout=server_log,
            stderr=subprocess.STDOUT,
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

            product = io.BytesIO()
            Image.new("RGBA", (160, 240), (110, 186, 226, 255)).save(product, format="PNG")
            processed = Image.new("RGBA", (160, 240), (0, 0, 0, 0))
            ImageDraw.Draw(processed).rectangle((38, 22, 133, 215), fill=(110, 186, 226, 255))
            processed_buffer = io.BytesIO()
            processed.save(processed_buffer, format="PNG")
            with sync_playwright() as pw:
                opts = chromium_launch_options()
                browser = pw.chromium.launch(headless=True, args=["--no-sandbox"], **opts)
                try:
                    page = browser.new_page(viewport={"width": 1440, "height": 960})
                    page_errors = []
                    post_responses = []
                    page.on("pageerror", lambda error: page_errors.append(str(error)))
                    page.on(
                        "response",
                        lambda response: (
                            post_responses.append((response.status, response.url))
                            if "/api/posters" in response.url
                            else None
                        ),
                    )
                    page.goto(origin + "/", wait_until="domcontentloaded")
                    # The model boundary is isolated; the actual browser and
                    # FastAPI poster creation/storage calls remain real.
                    page.route(
                        "**/api/product-cutouts/preview",
                        lambda route: route.fulfill(
                            status=200,
                            content_type="image/png",
                            body=processed_buffer.getvalue(),
                        ),
                    )
                    page.locator("#create-product").set_input_files(
                        {
                            "name": "synthetic-sku.png",
                            "mimeType": "image/png",
                            "buffer": product.getvalue(),
                        }
                    )
                    page.locator("#create-layout_id").select_option("hero")
                    page.locator("#create-product_align").select_option("bottom")
                    page.locator("#create-product_scale").select_option("compact")
                    page.locator("#cutoutPreviewButton").click()
                    page.locator("#acceptCutout").click(timeout=15000)
                    _check(
                        page.locator("#cutoutResultPreview").is_visible(),
                        "Local cutout preview comparison missing",
                    )
                    page.locator("#create-layer-editor summary").click()
                    page.locator("#create-layer-title_dx").fill("10")
                    page.locator("#create-layer-logo_dy").fill("-8")
                    page.locator("#generate").click()
                    try:
                        page.wait_for_function(
                            "() => (!!current && current.version === 1) || "
                            "document.querySelector('#createStatus').classList.contains('bad')",
                            timeout=15000,
                        )
                    except Exception as exc:
                        raise AssertionError(
                            "Create did not complete: "
                            + page.locator("#createStatus").inner_text()
                            + " | requests="
                            + repr(post_responses)
                            + " | browser errors="
                            + repr(page_errors)
                            + " | server log="
                            + log_path.read_text(errors="replace")[-3500:]
                        ) from exc
                    _check(
                        page.evaluate("() => !!current && current.version === 1"),
                        "Create failed: "
                        + page.locator("#createStatus").inner_text()
                        + " | requests="
                        + repr(post_responses)
                        + " | browser errors="
                        + repr(page_errors)
                        + " | server log="
                        + log_path.read_text(errors="replace")[-3500:],
                    )
                    first = page.evaluate("() => structuredClone(current)")
                    _check(first["spec"]["layout_id"] == "hero", "hero create failed")
                    _check(first["spec"]["product_align"] == "bottom", "product align lost")
                    _check(first["spec"]["product_scale"] == "compact", "scale lost")
                    _check(
                        first["spec"]["product_source_mode"] == "cutout",
                        "Explicit cutout selection lost on create",
                    )
                    _check(first["product_cutout_available"], "Cutout source was not retained")
                    _check(
                        first["spec"]["layer_layout"]["title_dx"] == 10,
                        "title layer lost on create",
                    )
                    _check(
                        first["spec"]["layer_layout"]["logo_dy"] == -8, "logo layer lost on create"
                    )
                    old_image = page.request.get(origin + first["image_url"]).body()
                    page.locator("#editLayout").select_option("classic")
                    page.locator("#editProductAlign").select_option("top")
                    page.locator("#editProductScale").select_option("full")
                    page.locator("#editProductSourceMode").select_option("original")
                    page.locator("#edit-layer-editor summary").click()
                    page.locator("#edit-layer-price_dx").fill("8")
                    page.locator("#edit-layer-price_dy").fill("-6")
                    page.locator("#edit-layer-cta_dx").fill("-10")
                    page.locator("#saveEdit").click()
                    page.wait_for_function("() => !!current && current.version === 2")
                    latest = page.evaluate("() => structuredClone(current)")
                    _check(latest["spec"]["layout_id"] == "classic", "edit lost template")
                    _check(latest["spec"]["product_align"] == "top", "edit lost alignment")
                    _check(latest["spec"]["product_scale"] == "full", "edit lost scale")
                    _check(
                        latest["spec"]["product_source_mode"] == "original",
                        "Switch back to original source was not versioned",
                    )
                    _check(latest["product_cutout_available"], "Revision discarded prior cutout")
                    _check(latest["spec"]["layer_layout"]["price_dx"] == 8, "price layer edit lost")
                    _check(
                        latest["spec"]["layer_layout"]["price_dy"] == -6,
                        "price layer vertical edit lost",
                    )
                    _check(latest["spec"]["layer_layout"]["cta_dx"] == -10, "CTA layer edit lost")
                    new_image = page.request.get(origin + latest["image_url"]).body()
                    _check(new_image != old_image, "layout produced identical PNG")
                    _check(
                        page.request.get(origin + first["image_url"]).body() == old_image,
                        "editing rewrote immutable old PNG",
                    )
                    page.locator("#versionGallery summary").click()
                    page.locator("#historyVersionSelect").select_option("1")
                    page.locator("#viewHistoryVersion").click()
                    page.locator("#historyVersionStatus").filter(has_text="只读历史 v1").wait_for()
                    print(
                        "PASS: real Chromium create hero with title/logo layer offsets, "
                        "edit price/CTA layers, new image, retained v1 and history gallery"
                    )

                    # An intentionally crowded two-line heading must be
                    # detected with the actual CJK font, repaired only on
                    # explicit click, then saved as another immutable version.
                    page.locator("#editLayout").select_option("hero")
                    page.locator("#editTitle").fill(
                        "智能电动牙刷限时优惠购买新品即刻体验强劲清洁能力"
                    )
                    page.locator("#editSubtitle").fill("高效清洁 · 品质生活")
                    page.locator("#edit-layer-title_dy").fill("20")
                    page.locator("#edit-layer-price_dy").fill("-8")
                    if page.locator("#edit-layer-editor").get_attribute("open") is None:
                        page.locator("#edit-layer-editor summary").click()
                    page.locator("#checkEditLayout").click()
                    page.locator("#editLayoutStatus").filter(has_text="发现遮挡").wait_for(
                        timeout=15000
                    )
                    _check(
                        page.locator("#fixEditLayout").is_enabled(),
                        "Missing bounded repair suggestion",
                    )
                    page.locator("#fixEditLayout").click()
                    _check(
                        page.locator("#editLayoutStatus").inner_text().startswith("已应用安全偏移"),
                        "Repair was not explicitly applied",
                    )
                    page.locator("#saveEdit").click()
                    page.wait_for_function(
                        "() => !!current && current.version === 3",
                        timeout=20000,
                    )
                    safe = page.evaluate("() => structuredClone(current)")
                    _check(
                        safe["spec"]["layer_layout"]["title_dy"] != 20
                        or safe["spec"]["layer_layout"]["price_dy"] != -8,
                        "Repair did not modify colliding geometry",
                    )
                    _check(
                        page.request.get(origin + first["image_url"]).body() == old_image,
                        "Repair overwrote an immutable earlier poster",
                    )
                    print(
                        "PASS: Chromium detected colliding text, applied safe offsets "
                        "only on explicit click and saved v3 without rewriting v1"
                    )

                    # Reuse the immutable v1 layout after the editor already
                    # changed the source poster to a different v2 layout.
                    page.locator("#nav-batch").click()
                    page.locator("#batchUseSavedLayout").check()
                    page.locator("#batchTemplateVersion").select_option("1")
                    manifest_text = (
                        "sku_id,product_image,title,price,original_price,subtitle,cta,style\n"
                        "sku-one,one.png,牙刷新品,199,399,品质生活,查看商品,蓝色科技风\n"
                        "sku-two,two.png,护肤单品,89,119,精选好物,立即查看,粉色浪漫\n"
                    )
                    page.locator("#batchManifest").set_input_files(
                        {
                            "name": "batch.csv",
                            "mimeType": "text/csv",
                            "buffer": manifest_text.encode("utf-8"),
                        }
                    )
                    page.locator("#batchImages").set_input_files(
                        [
                            {
                                "name": "one.png",
                                "mimeType": "image/png",
                                "buffer": product.getvalue(),
                            },
                            {
                                "name": "two.png",
                                "mimeType": "image/png",
                                "buffer": product.getvalue(),
                            },
                        ]
                    )
                    page.locator("#preflightBatch").click()
                    page.wait_for_function(
                        "() => lastBatchStatus === 'READY' || "
                        "document.querySelector('#batchStatus').classList.contains('bad')",
                        timeout=30000,
                    )
                    _check(
                        page.evaluate("() => lastBatchStatus === 'READY'"),
                        "Batch preflight failed: "
                        + page.locator("#batchStatus").inner_text()
                        + " | server log="
                        + log_path.read_text(errors="replace")[-2400:],
                    )
                    batch_id = page.evaluate("() => currentBatchId")
                    staged = page.request.get(origin + "/api/batches/" + batch_id).json()
                    _check(
                        staged["plan"]["layout_source"]["poster_id"] == first["poster_id"],
                        "Wrong source poster",
                    )
                    _check(
                        staged["plan"]["layout_source"]["version"] == 1,
                        "Historical source version was not frozen",
                    )
                    page.locator("#applyBatch").click()
                    page.wait_for_function(
                        "() => lastBatchStatus === 'COMPLETED' || "
                        "document.querySelector('#batchStatus').classList.contains('bad')",
                        timeout=45000,
                    )
                    _check(
                        page.evaluate("() => lastBatchStatus === 'COMPLETED'"),
                        "Batch application failed: "
                        + page.locator("#batchStatus").inner_text()
                        + " | server log="
                        + log_path.read_text(errors="replace")[-2400:],
                    )
                    batch_state = page.request.get(origin + "/api/batches/" + batch_id).json()
                    _check(len(batch_state["rows"]) == 2, "Wrong batch size")
                    for row in batch_state["rows"]:
                        saved = page.request.get(
                            origin + "/api/posters/" + row["poster_id"]
                        ).json()["spec"]
                        _check(saved["layout_id"] == "hero", "Lost historical hero template")
                        _check(saved["layer_layout"]["title_dx"] == 10, "Lost frozen title offset")
                        _check(saved["layer_layout"]["logo_dy"] == -8, "Lost frozen logo offset")
                    print(
                        "PASS: browser reused historical source v1 layer template "
                        "across 2 SKU, preflighted, generated and preserved frozen layout"
                    )
                finally:
                    browser.close()
        finally:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)
            server_log.close()


if __name__ == "__main__":
    main()
