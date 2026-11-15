"""Persistent design discovery, pagination and failure recovery in actual Chrome."""

import tempfile
from pathlib import Path

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
            page.goto(origin + "/canvas", wait_until="networkidle")
            library = page.get_by_role("region", name="设计库", exact=True)
            expect(
                library.get_by_text("还没有设计，上传商品图片即可创建。", exact=True)
            ).to_be_visible()
            docs = []
            for i in range(12):
                response = page.request.post(
                    origin + "/api/designs",
                    multipart={
                        "title": f"持久设计{i:02d}",
                        "price": "99.90",
                        "product": {
                            "name": "product.png",
                            "mimeType": "image/png",
                            "buffer": product_png(),
                        },
                    },
                )
                assert response.status == 201, response.text()
                docs.append(response.json()["document"])
            # No recent browser history is needed for discovery.
            page.evaluate("localStorage.clear()")
            page.reload(wait_until="networkidle")
            expect(library.locator(".canvas-library-item")).to_have_count(10)
            library.get_by_role("button", name="加载更多设计", exact=True).click()
            expect(library.locator(".canvas-library-item")).to_have_count(12)
            expect(library.get_by_role("button", name="加载更多设计", exact=True)).to_have_count(0)
            library.get_by_role("button", name="持久设计00 版本 1", exact=True).click()
            expect(page.locator(".canvas-save-status")).to_have_text("已保存 · 版本 1")
            assert docs[0]["document_id"] in page.url
            page.get_by_role("button", name="选择主标题图层", exact=True).click()
            page.get_by_label("图层文案", exact=True).fill("从设计库打开后编辑")
            page.get_by_role("button", name="保存修改", exact=True).click()
            expect(page.locator(".canvas-save-status")).to_have_text("已保存 · 版本 2")
            library.get_by_role("button", name="加载更多设计", exact=True).click()
            expect(
                library.get_by_role("button", name="从设计库打开后编辑 版本 2", exact=True)
            ).to_be_visible()
            page.get_by_label("图层文案", exact=True).fill("待保存草稿")
            page.once("dialog", lambda dialog: dialog.dismiss())
            library.get_by_role("button", name="持久设计11 版本 1", exact=True).click()
            expect(page.get_by_label("图层文案", exact=True)).to_have_value("待保存草稿")
            assert docs[0]["document_id"] in page.url
            page.once("dialog", lambda dialog: dialog.accept())
            library.get_by_role("button", name="持久设计11 版本 1", exact=True).click()
            expect(page.locator(".canvas-save-status")).to_have_text("已保存 · 版本 1")
            assert docs[11]["document_id"] in page.url
            library.get_by_role("button", name="加载更多设计", exact=True).click()
            expect(library.locator(".canvas-library-item")).to_have_count(12)

            def fail(route):
                route.fulfill(
                    status=503,
                    content_type="application/json",
                    body='{"error":{"code":"DESIGN_STORAGE_UNAVAILABLE","message":"暂时不可用"}}',
                )

            page.route("**/api/designs?*", fail)
            library.get_by_role("button", name="刷新列表", exact=True).click()
            expect(library.get_by_role("alert")).to_be_visible()
            expect(library.locator(".canvas-library-item")).to_have_count(12)
            page.unroute("**/api/designs?*", fail)
            library.get_by_role("button", name="重试载入设计库", exact=True).click()
            expect(library.get_by_role("alert")).to_have_count(0)
            expect(library.locator(".canvas-library-item")).to_have_count(10)
            page.set_viewport_size({"width": 390, "height": 844})
            library.get_by_role("button", name="加载更多设计", exact=True).click()
            expect(library.locator(".canvas-library-item")).to_have_count(12)
            library.screenshot(path=str(output / "library-mobile.png"))
            assert not page.evaluate("document.documentElement.scrollWidth > innerWidth")
            assert not errors, errors
            print("PASS: persistent library empty/discovery/pagination/edit/retry/mobile")
        finally:
            browser.close()


if __name__ == "__main__":
    output = Path(tempfile.mkdtemp(prefix="poster-library-shots-"))
    print("Library screenshots:", output)
    with disposable_server() as origin:
        run(origin, output)
