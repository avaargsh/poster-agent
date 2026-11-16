"""UI task lifecycle with a deterministic CLI stand-in and real HTTP backend."""

import tempfile
from pathlib import Path

from browser_canvas_workspace import product_png
from browser_support import chromium_launch_options
from fake_codex import write_fake_codex
from playwright.sync_api import expect, sync_playwright
from server_support import local_backend


def run(origin, output):
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, **chromium_launch_options())
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            response = page.request.post(
                origin + "/api/designs",
                multipart={
                    "title": "原始标题",
                    "price": "99.90",
                    "product": {
                        "name": "product.png",
                        "mimeType": "image/png",
                        "buffer": product_png(),
                    },
                },
            )
            assert response.status == 201
            doc = response.json()["document"]["document_id"]
            api = origin + "/api/designs/" + doc
            page.goto(origin + "/canvas?document=" + doc, wait_until="networkidle")
            page.get_by_role("button", name="AI 修改", exact=True).click()
            panel = page.get_by_role("complementary", name="AI 修改", exact=True)
            panel.get_by_label("AI 修改需求", exact=True).fill(
                "把标题改为AI 测试标题，活动价字号改为100"
            )
            requests = []

            def lose_ack(route):
                requests.append(route.request.post_data_json)
                if len(requests) == 1:
                    assert route.fetch().status == 202
                    route.abort("failed")
                else:
                    route.continue_()

            page.route("**/agent-runs", lose_ack)
            panel.get_by_role("button", name="使用 Codex 生成建议", exact=True).click()
            expect(panel.get_by_role("button", name="重试原 AI 请求", exact=True)).to_be_visible()
            panel.get_by_role("button", name="重试原 AI 请求", exact=True).click()
            expect(panel.get_by_role("button", name="查看并预览建议", exact=True)).to_be_visible(
                timeout=15000
            )
            assert requests[0]["request_id"] == requests[1]["request_id"]
            page.unroute("**/agent-runs", lose_ack)
            assert page.request.get(api).json()["document"]["revision"] == 1
            panel.get_by_role("button", name="查看并预览建议", exact=True).click()
            proposals = page.get_by_role("complementary", name="修改建议", exact=True)
            proposals.get_by_role("button", name="预览建议", exact=True).click()
            expect(page.locator(".canvas-surface")).to_contain_text("AI 测试标题")
            assert page.request.get(api).json()["document"]["revision"] == 1
            proposals.get_by_role("button", name="应用修改建议", exact=True).click()
            expect(page.locator(".canvas-save-status")).to_have_text("已保存 · 版本 2")
            page.get_by_role("button", name="AI 修改", exact=True).click()
            panel.get_by_label("AI 修改需求", exact=True).fill("hold-task")
            panel.get_by_role("button", name="使用 Codex 生成建议", exact=True).click()
            expect(panel.get_by_role("button", name="停止 AI 任务", exact=True)).to_be_visible()
            panel.get_by_role("button", name="关闭 AI", exact=True).click()
            page.get_by_role("button", name="AI 修改", exact=True).click()
            expect(panel.get_by_role("button", name="停止 AI 任务", exact=True)).to_be_visible()
            panel.get_by_role("button", name="停止 AI 任务", exact=True).click()
            task = panel.get_by_role("region", name="当前 AI 任务", exact=True)
            expect(task.get_by_role("status")).to_contain_text("已停止")
            page.reload(wait_until="networkidle")
            page.get_by_role("button", name="AI 修改", exact=True).click()
            expect(task.get_by_role("status")).to_contain_text("已停止")
            page.set_viewport_size({"width": 390, "height": 844})
            assert not page.evaluate("document.documentElement.scrollWidth > innerWidth")
            page.screenshot(path=str(output / "agent-mobile.png"), full_page=True)
            assert not errors, errors
            print(
                "PASS: AI task submit/ACK-loss/preview/explicit apply/close/reopen/cancel/reload/mobile (CLI stand-in)"
            )
        finally:
            browser.close()


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="poster-agent-browser-") as directory:
        root = Path(directory)
        output = Path(tempfile.mkdtemp(prefix="poster-agent-shots-"))
        print("Agent screenshots:", output)
        binary = write_fake_codex(root / "bin")
        with local_backend(root / "server", enable_agent=True, codex_bin=binary) as client:
            run(str(client.base_url).rstrip("/"), output)
