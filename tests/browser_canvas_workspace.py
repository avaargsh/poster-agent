"""Real Chrome acceptance for the independent canvas with disposable design data.

Run without arguments to start an isolated service, or pass an origin and an
optional screenshot directory to test an already isolated service.
"""

from __future__ import annotations

import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from urllib.request import urlopen
from uuid import uuid4

from browser_support import chromium_launch_options
from PIL import Image, ImageDraw
from playwright.sync_api import Error, expect, sync_playwright


@contextmanager
def disposable_server():
    with tempfile.TemporaryDirectory(prefix="poster-canvas-browser-") as directory:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        env = {
            **os.environ,
            "POSTER_DATA_DIR": directory + "/posters",
            "POSTER_EXECUTION_DB": directory + "/executions.sqlite3",
            "POSTER_CHECKPOINT_DB": directory + "/checkpoints.sqlite3",
            "POSTER_DESIGN_DB": directory + "/designs.sqlite3",
            "POSTER_DESIGN_ASSET_DIR": directory + "/design-assets",
        }
        with (Path(directory) / "server.log").open("wb") as log:
            server = subprocess.Popen(
                [sys.executable, "-m", "app.run_local", "--port", str(port)],
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
            try:
                origin = f"http://127.0.0.1:{port}"
                for _ in range(80):
                    if server.poll() is not None:
                        raise RuntimeError("Canvas service exited during startup")
                    try:
                        with urlopen(origin + "/health", timeout=1) as response:
                            if response.status == 200:
                                break
                    except OSError:
                        time.sleep(0.15)
                else:
                    raise RuntimeError("Canvas service did not start")
                yield origin
            finally:
                server.terminate()
                try:
                    server.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait(timeout=5)


def product_png():
    image = Image.new("RGBA", (360, 480), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((110, 55, 250, 425), radius=30, fill="#d1e9fa")
    draw.rounded_rectangle((125, 15, 235, 90), radius=15, fill="#6f9cc5")
    draw.rectangle((120, 200, 240, 285), fill="#7399c0")
    stream = io.BytesIO()
    image.save(stream, format="PNG")
    return stream.getvalue()


def run(origin: str, screenshots: Path):
    screenshots.mkdir(parents=True, exist_ok=True)
    print(f"Canvas screenshots: {screenshots}", flush=True)
    product = product_png()
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, **chromium_launch_options())
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(origin + "/canvas", wait_until="networkidle")
            expect(page.get_by_role("heading", name="图层画布")).to_be_visible()
            page.screenshot(path=str(screenshots / "empty.png"), full_page=True)
            page.get_by_label("商品图片", exact=True).set_input_files(
                {"name": "synthetic.png", "mimeType": "image/png", "buffer": product}
            )
            page.get_by_label("新设计主标题", exact=True).fill("夏日清凉特惠")
            page.get_by_label("新设计活动价", exact=True).fill("99.90")
            page.get_by_label("新设计按钮文案", exact=True).fill("立即选购")
            page.get_by_role("button", name="创建画布", exact=True).click()
            expect(page.locator(".canvas-save-status")).to_have_text("已保存 · 版本 1")
            document_id = parse_qs(urlparse(page.url).query)["document"][0]
            endpoint = origin + "/api/designs/" + document_id

            def current():
                response = page.request.get(endpoint)
                assert response.ok, response.text()
                return response.json()["document"]

            def saved(revision):
                expect(page.locator(".canvas-save-status")).to_have_text(
                    f"已保存 · 版本 {revision}", timeout=15000
                )
                expect(page.locator(".canvas-surface > svg")).to_be_visible()

            def node(role):
                return next(item for item in current()["nodes"] if item["role"] == role)

            page.get_by_role("button", name="选择主标题图层", exact=True).click()
            page.get_by_label("图层文案", exact=True).fill("清凉一夏 新品上市")
            page.get_by_role("button", name="保存修改", exact=True).click()
            saved(2)
            assert "".join(line["text"] for line in node("title")["lines"]) == "清凉一夏 新品上市"

            page.get_by_role("button", name="选择活动价图层", exact=True).click()
            page.get_by_label("活动价", exact=True).fill("88.80")
            page.get_by_role("button", name="保存修改", exact=True).click()
            saved(3)
            assert current()["facts"]["price"] == "88.80"
            page.get_by_role("button", name="撤销修改", exact=True).click()
            saved(4)
            assert current()["facts"]["price"] == "99.90"
            page.get_by_role("button", name="重做修改", exact=True).click()
            saved(5)
            assert current()["facts"]["price"] == "88.80"

            page.get_by_role("button", name="选择商品图层", exact=True).click()
            before = node("product")
            target = page.locator('g[data-node-id="product"]')
            box = target.bounding_box()
            assert box
            page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
            page.mouse.down()
            page.mouse.move(
                box["x"] + box["width"] / 2 + 35, box["y"] + box["height"] / 2 + 20, steps=5
            )
            page.mouse.up()
            saved(6)
            assert node("product")["x"] > before["x"]
            assert node("product")["y"] > before["y"]

            # Escape cancels a drag without creating another revision.
            box = target.bounding_box()
            assert box
            page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
            page.mouse.down()
            page.mouse.move(box["x"] + box["width"] / 2 + 20, box["y"] + box["height"] / 2)
            page.keyboard.press("Escape")
            page.mouse.up()
            assert current()["revision"] == 6

            page.get_by_role("button", name="锁定图层", exact=True).click()
            saved(7)
            expect(page.get_by_label("图层 X", exact=True)).to_be_disabled()
            assert node("product")["locked"] is True
            page.get_by_role("button", name="解锁图层", exact=True).click()
            saved(8)
            previous_index = next(
                index
                for index, item in enumerate(current()["nodes"])
                if item["node_id"] == "product"
            )
            page.get_by_role("button", name="上移一层", exact=True).click()
            saved(9)
            assert current()["nodes"][previous_index + 1]["node_id"] == "product"
            page.reload(wait_until="networkidle")
            saved(9)
            assert page.get_by_label("当前设计 ID", exact=True).input_value() == document_id
            paper_box = page.locator(".canvas-paper").bounding_box()
            viewport_box = page.locator(".canvas-scrollport").bounding_box()
            assert paper_box and viewport_box
            assert (
                paper_box["y"] + paper_box["height"] <= viewport_box["y"] + viewport_box["height"]
            )
            page.screenshot(path=str(screenshots / "desktop.png"), full_page=True)

            # An external edit must conflict with the still-open local version.
            page.get_by_role("button", name="选择主标题图层", exact=True).click()
            page.get_by_label("图层文案", exact=True).fill("这页尚未保存的文案")
            external = page.request.post(
                endpoint + "/commands",
                data={
                    "command_id": str(uuid4()),
                    "expected_revision": 9,
                    "actor": "user",
                    "operations": [
                        {"op": "set_text", "node_id": "title", "text": "另一页面更新的文案"}
                    ],
                },
            )
            assert external.ok, external.text()
            page.get_by_role("button", name="保存修改", exact=True).click()
            expect(page.get_by_role("alert")).to_contain_text("其他页面更新")
            expect(page.get_by_label("图层文案", exact=True)).to_have_value("这页尚未保存的文案")
            page.get_by_role("button", name="放弃草稿并载入最新版本", exact=True).click()
            saved(10)
            expect(page.get_by_label("图层文案", exact=True)).to_have_value("另一页面更新的文案")

            # Commit once but drop the response. The explicit retry uses the same receipt.
            requests = []

            def lose_first_ack(route):
                requests.append(route.request.post_data_json)
                if len(requests) == 1:
                    response = route.fetch()
                    assert response.ok
                    route.abort("failed")
                else:
                    route.continue_()

            page.route("**/api/designs/*/commands", lose_first_ack)
            page.get_by_label("图层文案", exact=True).fill("保留相同保存请求")
            page.get_by_role("button", name="保存修改", exact=True).click()
            expect(page.get_by_role("alert")).to_contain_text("保存结果待确认")
            assert len(requests) == 1
            assert current()["revision"] == 11
            page.get_by_role("button", name="重试原保存请求", exact=True).click()
            saved(11)
            assert len(requests) == 2
            assert requests[0]["command_id"] == requests[1]["command_id"]
            assert current()["revision"] == 11
            page.unroute("**/api/designs/*/commands", lose_first_ack)

            # A definitive validation response must clear an uncertain receipt.
            rejected_requests = []

            def lose_rejection(route):
                rejected_requests.append(route.request.post_data_json)
                if len(rejected_requests) == 1:
                    response = route.fetch()
                    assert response.status == 422
                    route.abort("failed")
                else:
                    route.continue_()

            page.route("**/api/designs/*/commands", lose_rejection)
            page.get_by_label("图层文案", exact=True).fill("不支持的\n换行")
            page.get_by_role("button", name="保存修改", exact=True).click()
            expect(page.get_by_role("alert")).to_contain_text("保存结果待确认")
            page.get_by_role("button", name="重试原保存请求", exact=True).click()
            expect(page.get_by_role("alert")).to_contain_text("422")
            expect(page.get_by_role("button", name="重试原保存请求", exact=True)).to_have_count(0)
            expect(page.get_by_label("图层文案", exact=True)).to_be_enabled()
            assert current()["revision"] == 11
            page.unroute("**/api/designs/*/commands", lose_rejection)
            page.get_by_label("图层文案", exact=True).fill("可以继续保存")
            page.get_by_role("button", name="保存修改", exact=True).click()
            saved(12)

            # An unrelated command must preserve unsaved inspector fields.
            draft_copy = "尚未保存的文案继续保留"
            page.get_by_label("图层文案", exact=True).fill(draft_copy)
            page.get_by_role("button", name="下移一层", exact=True).click()
            expect(page.locator(".canvas-save-status")).to_contain_text("版本 13")
            expect(page.get_by_label("图层文案", exact=True)).to_have_value(draft_copy)
            assert "".join(line["text"] for line in node("title")["lines"]) == "可以继续保存"
            old_x = node("title")["x"]
            title_box = page.locator('g[data-node-id="title"]').bounding_box()
            assert title_box
            page.mouse.move(title_box["x"] + 10, title_box["y"] + title_box["height"] / 2)
            page.mouse.down()
            page.mouse.move(title_box["x"] + 30, title_box["y"] + title_box["height"] / 2, steps=3)
            page.mouse.up()
            expect(page.locator(".canvas-save-status")).to_contain_text("版本 14")
            moved_x = node("title")["x"]
            assert moved_x > old_x
            expect(page.get_by_label("图层 X", exact=True)).to_have_value(str(int(moved_x)))
            expect(page.get_by_label("图层文案", exact=True)).to_have_value(draft_copy)
            page.get_by_role("button", name="选择活动价图层", exact=True).click()
            page.get_by_role("button", name="选择主标题图层", exact=True).click()
            expect(page.get_by_label("图层文案", exact=True)).to_have_value(draft_copy)
            page.get_by_role("button", name="保存修改", exact=True).click()
            saved(15)
            assert node("title")["x"] == moved_x
            assert "".join(line["text"] for line in node("title")["lines"]) == draft_copy
            page.get_by_label("图层文案", exact=True).press("ArrowLeft")
            assert current()["revision"] == 15
            page.locator(".canvas-surface").focus()
            page.keyboard.press("Shift+ArrowRight")
            saved(16)
            assert node("title")["x"] == moved_x + 10

            previous_zoom = int(page.locator(".canvas-zoom output").inner_text().rstrip("%"))
            page.get_by_role("button", name="放大画布", exact=True).click()
            expect(page.locator(".canvas-zoom output")).to_have_text(
                f"{min(200, previous_zoom + 25)}%"
            )
            assert current()["revision"] == 16
            page.get_by_role("button", name="适应窗口", exact=True).click()

            # Delayed reads cannot replace a subsequently opened project.
            def another(title):
                response = page.request.post(
                    origin + "/api/designs",
                    multipart={
                        "title": title,
                        "price": "99.90",
                        "product": {
                            "name": "synthetic.png",
                            "mimeType": "image/png",
                            "buffer": product,
                        },
                    },
                )
                assert response.ok, response.text()
                return response.json()

            old = another("较慢的设计")
            new = another("稍后打开的设计")
            held = []
            old_id = old["document"]["document_id"]
            new_id = new["document"]["document_id"]
            page.route(f"**/api/designs/{old_id}", lambda route: held.append(route))
            page.locator(".canvas-open summary").click()
            page.get_by_label("打开设计 ID", exact=True).fill(old_id)
            page.get_by_role("button", name="打开设计", exact=True).click()
            page.wait_for_function(
                "document.querySelector('.canvas-save-status').textContent.includes('正在载入')"
            )
            assert len(held) == 1
            page.evaluate(
                "id => { history.pushState(null, '', '/canvas?document=' + id); dispatchEvent(new PopStateEvent('popstate')); }",
                new_id,
            )
            expect(page.get_by_label("当前设计 ID", exact=True)).to_have_value(new_id)
            saved(1)
            for route in held:
                try:
                    route.fulfill(status=200, content_type="application/json", body=json.dumps(old))
                except Error:
                    pass  # The superseded browser request may already be aborted.
            expect(page.get_by_label("当前设计 ID", exact=True)).to_have_value(new_id)

            # A failed open leaves the current project visible and can be retried.
            page.context.set_offline(True)
            page.get_by_label("打开设计 ID", exact=True).fill(document_id)
            page.get_by_role("button", name="打开设计", exact=True).click()
            expect(page.get_by_role("alert")).to_contain_text("连接中断")
            expect(page.get_by_label("当前设计 ID", exact=True)).to_have_value(new_id)
            page.context.set_offline(False)
            page.get_by_role("button", name="打开设计", exact=True).click()
            saved(16)

            page.set_viewport_size({"width": 390, "height": 844})
            expect(page.get_by_role("link", name="返回工作台", exact=True)).to_be_visible()
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), (
                "mobile page overflows"
            )
            page.screenshot(path=str(screenshots / "mobile.png"), full_page=True)
            assert not errors, errors
            print(
                "PASS: canvas create/edit/drag/cancel/lock/order/undo/redo/reload/conflict/ACK-loss/stale-read/offline/mobile"
            )
        finally:
            browser.close()


if __name__ == "__main__":
    output = (
        Path(sys.argv[2])
        if len(sys.argv) > 2
        else Path(tempfile.mkdtemp(prefix="poster-canvas-shots-"))
    )
    if len(sys.argv) > 1:
        run(sys.argv[1].rstrip("/"), output)
    else:
        with disposable_server() as origin:
            run(origin, output)
