"""Historical preview and revision restore through the actual canvas UI."""

import tempfile
from pathlib import Path
from uuid import uuid4

from browser_canvas_workspace import disposable_server, product_png
from browser_support import chromium_launch_options
from playwright.sync_api import expect, sync_playwright


def run(origin, output):
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, **chromium_launch_options())
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            created = page.request.post(
                origin + "/api/designs",
                multipart={
                    "title": "最初的海报",
                    "price": "99.90",
                    "product": {
                        "name": "product.png",
                        "mimeType": "image/png",
                        "buffer": product_png(),
                    },
                },
            )
            assert created.status == 201
            original = created.json()["document"]
            doc = original["document_id"]
            api = origin + "/api/designs/" + doc

            def current():
                return page.request.get(api).json()["document"]

            def edit(revision, text):
                response = page.request.post(
                    api + "/commands",
                    data={
                        "command_id": str(uuid4()),
                        "actor": "user",
                        "expected_revision": revision,
                        "operations": [{"op": "set_text", "node_id": "title", "text": text}],
                    },
                )
                assert response.ok, response.text()

            for revision in range(1, 22):
                edit(revision, f"第{revision + 1}版文案")
            page.goto(origin + "/canvas?document=" + doc, wait_until="networkidle")
            page.get_by_role("button", name="选择主标题图层", exact=True).click()
            page.get_by_label("图层文案", exact=True).fill("未保存的属性草稿")
            page.get_by_role("button", name="历史版本", exact=True).click()
            panel = page.get_by_role("complementary", name="历史版本", exact=True)
            expect(panel.locator(".canvas-history-list button")).to_have_count(20)
            panel.get_by_role("button", name="更早版本", exact=True).click()
            expect(panel.locator(".canvas-history-list button")).to_have_count(22)

            def fail(route):
                route.fulfill(
                    status=503,
                    content_type="application/json",
                    body='{"error":{"code":"DESIGN_STORAGE_UNAVAILABLE","message":"preview unavailable"}}',
                )

            page.route("**/revisions/1/preview.svg", fail)
            panel.get_by_role("button", name="预览版本 1", exact=True).click()
            expect(panel.get_by_role("alert")).to_be_visible()
            expect(
                panel.get_by_role("button", name="放弃属性草稿并恢复此版本", exact=True)
            ).to_have_count(0)
            page.unroute("**/revisions/1/preview.svg", fail)
            panel.get_by_role("button", name="预览版本 1", exact=True).click()
            expect(page.locator(".canvas-save-status")).to_have_text(
                "历史预览 · 版本 1 · 当前版本 22"
            )
            expect(page.locator(".canvas-surface")).to_contain_text("最初的海报")
            expect(page.get_by_role("button", name="审核与导出", exact=True)).to_be_disabled()
            page.locator(".canvas-surface").focus()
            page.keyboard.press("ArrowRight")
            assert current()["revision"] == 22
            panel.get_by_role("button", name="关闭历史", exact=True).click()
            expect(page.get_by_label("图层文案", exact=True)).to_have_value("未保存的属性草稿")
            page.get_by_role("button", name="历史版本", exact=True).click()
            panel.get_by_role("button", name="更早版本", exact=True).click()
            panel.get_by_role("button", name="预览版本 1", exact=True).click()
            expect(page.locator(".canvas-save-status")).to_contain_text("历史预览 · 版本 1")
            writes = []

            def lose_ack(route):
                writes.append(route.request.post_data_json)
                if len(writes) == 1:
                    assert route.fetch().ok
                    route.abort("failed")
                else:
                    route.continue_()

            page.route("**/commands", lose_ack)
            panel.get_by_role("button", name="放弃属性草稿并恢复此版本", exact=True).click()
            expect(page.get_by_role("alert")).to_be_visible()
            assert current()["revision"] == 23
            page.get_by_role("button", name="重试原保存请求", exact=True).click()
            expect(page.locator(".canvas-save-status")).to_have_text("已保存 · 版本 23")
            assert writes[0]["command_id"] == writes[1]["command_id"]
            assert current()["revision"] == 23
            assert current()["nodes"] == original["nodes"]
            page.unroute("**/commands", lose_ack)
            panel.get_by_role("button", name="关闭历史", exact=True).click()
            expect(page.get_by_label("图层文案", exact=True)).to_have_value("最初的海报")
            page.get_by_role("button", name="撤销修改", exact=True).click()
            expect(page.locator(".canvas-save-status")).to_have_text("已保存 · 版本 24")
            expect(page.locator(".canvas-surface")).to_contain_text("第22版文案")
            page.get_by_role("button", name="重做修改", exact=True).click()
            expect(page.locator(".canvas-save-status")).to_have_text("已保存 · 版本 25")
            page.get_by_role("button", name="历史版本", exact=True).click()
            panel.get_by_role("button", name="预览版本 24", exact=True).click()
            expect(page.locator(".canvas-save-status")).to_contain_text("历史预览 · 版本 24")
            edit(25, "另一个会话的最新修改")
            panel.get_by_role("button", name="恢复为新版本", exact=True).click()
            expect(page.get_by_role("alert")).to_be_visible()
            assert current()["revision"] == 26
            page.get_by_role("button", name="放弃草稿并载入最新版本", exact=True).click()
            expect(page.locator(".canvas-save-status")).to_have_text("已保存 · 版本 26")
            expect(panel.get_by_text("当前保存版本 26", exact=True)).to_be_visible()
            panel.get_by_role("button", name="预览版本 25", exact=True).click()
            expect(page.locator(".canvas-save-status")).to_contain_text("历史预览 · 版本 25")
            page.set_viewport_size({"width": 390, "height": 844})
            assert not page.evaluate("document.documentElement.scrollWidth > innerWidth")
            page.screenshot(path=str(output / "history-mobile.png"), full_page=True)
            assert not errors, errors
            print(
                "PASS: history pagination/read-only preview/draft/restore/ACK-loss/undo/redo/conflict/mobile"
            )
        finally:
            browser.close()


if __name__ == "__main__":
    output = Path(tempfile.mkdtemp(prefix="poster-history-shots-"))
    print("History screenshots:", output)
    with disposable_server() as origin:
        run(origin, output)
