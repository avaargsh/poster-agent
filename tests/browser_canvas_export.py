"""Exact-version canvas acknowledgement and file download in actual Chrome."""

import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path
from uuid import uuid4

from browser_canvas_workspace import disposable_server, product_png
from browser_support import chromium_launch_options
from playwright.sync_api import expect, sync_playwright

TOKEN = "synthetic-browser-export-token-00000000000001"


def run(origin, output, named, require_png):
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, **chromium_launch_options())
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 1000}, accept_downloads=True)
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            created = page.request.post(
                origin + "/api/designs",
                multipart={
                    "title": "导出核对海报",
                    "price": "199.90",
                    "product": {
                        "name": "product.png",
                        "mimeType": "image/png",
                        "buffer": product_png(),
                    },
                },
            )
            assert created.status == 201
            doc = created.json()["document"]
            api = origin + "/api/designs/" + doc["document_id"]

            def fail_preview(route):
                route.fulfill(
                    status=503,
                    content_type="application/json",
                    body='{"error":{"code":"DESIGN_STORAGE_UNAVAILABLE","message":"preview unavailable"}}',
                )

            page.route("**/preview.svg", fail_preview)
            page.goto(origin + "/canvas?document=" + doc["document_id"], wait_until="networkidle")
            expect(page.get_by_role("button", name="审核与导出", exact=True)).to_be_disabled()
            expect(page.get_by_role("button", name="重试预览", exact=True)).to_be_visible()
            page.unroute("**/preview.svg", fail_preview)
            page.get_by_role("button", name="重试预览", exact=True).click()
            expect(page.get_by_role("button", name="审核与导出", exact=True)).to_be_enabled()
            page.get_by_role("button", name="选择主标题图层", exact=True).click()
            page.get_by_label("图层文案", exact=True).fill("未保存的属性草稿")
            expect(page.get_by_role("button", name="审核与导出", exact=True)).to_be_disabled()
            page.get_by_role("button", name="保存修改", exact=True).click()
            expect(page.locator(".canvas-save-status")).to_have_text("已保存 · 版本 2")
            page.get_by_role("button", name="审核与导出", exact=True).click()
            panel = page.get_by_role("complementary", name="审核与导出", exact=True)
            expect(panel.get_by_role("button", name="下载 SVG", exact=True)).to_be_disabled()
            expect(panel.get_by_role("button", name="确认当前版本", exact=True)).to_be_disabled()
            panel.get_by_role("checkbox").check()
            if named:
                panel.get_by_role("button", name="确认当前版本", exact=True).click()
                expect(panel.get_by_role("alert")).to_contain_text("需要审核凭据")
                panel.get_by_label("审核凭据", exact=True).fill(TOKEN)
            approvals = []

            def lose_ack(route):
                approvals.append(route.request.post_data_json)
                if len(approvals) == 1:
                    assert route.fetch().status == 201
                    route.abort("failed")
                else:
                    route.continue_()

            page.route("**/approvals", lose_ack)
            panel.get_by_role("button", name="确认当前版本", exact=True).click()
            expect(panel.get_by_role("alert")).to_be_visible()
            panel.get_by_role("button", name="确认当前版本", exact=True).click()
            expect(panel.get_by_text("已确认 · 版本 2", exact=True)).to_be_visible()
            assert approvals[0]["approval_id"] == approvals[1]["approval_id"]
            page.unroute("**/approvals", lose_ack)
            with page.expect_download() as event:
                panel.get_by_role("button", name="下载 SVG", exact=True).click()
            download = event.value
            assert download.suggested_filename == doc["document_id"] + "-v2.svg"
            svg = Path(download.path()).read_bytes()
            expected = page.request.get(api + "/revisions/2/preview.svg").body()
            assert svg == expected
            headers = {"Authorization": "Bearer " + TOKEN} if named else {}
            raster = page.request.get(api + "/revisions/2/approved.png", headers=headers)
            if raster.ok:
                with page.expect_download() as event:
                    panel.get_by_role("button", name="下载 PNG", exact=True).click()
                assert Path(event.value.path()).read_bytes() == raster.body()
                assert raster.body().startswith(b"\x89PNG\r\n\x1a\n")
                (output / f"approved-{named}.png").write_bytes(raster.body())
                png = "available"
            else:
                assert not require_png, raster.text()
                assert raster.status == 503 and raster.json()["error"]["code"] in {
                    "DESIGN_RASTER_UNAVAILABLE",
                    "DESIGN_RASTER_FONT_UNAVAILABLE",
                }
                panel.get_by_role("button", name="下载 PNG", exact=True).click()
                expect(panel.get_by_role("alert")).to_contain_text("缺少")
                png = "unavailable with recovery message"
            # A later edit invalidates the old export, even with the panel still open.
            changed = page.request.post(
                api + "/commands",
                data={
                    "command_id": str(uuid4()),
                    "actor": "user",
                    "expected_revision": 2,
                    "operations": [
                        {"op": "set_text", "node_id": "title", "text": "另一个会话已更新"}
                    ],
                },
            )
            assert changed.ok, changed.text()
            panel.get_by_role("button", name="下载 SVG", exact=True).click()
            expect(panel.get_by_role("alert")).to_be_visible()
            page.reload(wait_until="networkidle")
            page.get_by_role("button", name="审核与导出", exact=True).click()
            expect(panel.get_by_text("当前保存版本 3", exact=True)).to_be_visible()
            expect(panel.get_by_role("button", name="下载 SVG", exact=True)).to_be_disabled()
            if named:
                expect(panel.get_by_label("审核凭据", exact=True)).to_have_value("")
            panel.get_by_role("checkbox").check()
            page.route("**/preview.svg", fail_preview)
            page.get_by_role("region", name="设计库", exact=True).get_by_role(
                "button", name="另一个会话已更新 版本 3", exact=True
            ).click()
            expect(page.get_by_role("button", name="重试预览", exact=True)).to_be_visible()
            expect(panel.get_by_role("button", name="确认当前版本", exact=True)).to_be_disabled()
            page.unroute("**/preview.svg", fail_preview)
            page.get_by_role("button", name="重试预览", exact=True).click()
            expect(panel.get_by_role("button", name="确认当前版本", exact=True)).to_be_enabled()
            page.set_viewport_size({"width": 390, "height": 844})
            assert not page.evaluate("document.documentElement.scrollWidth > innerWidth")
            page.screenshot(path=str(output / f"export-mobile-{named}.png"), full_page=True)
            assert not errors, errors
            print(
                f"PASS: canvas exact-version confirmation/SVG/ACK-loss/stale/mobile named={named} PNG={png}"
            )
        finally:
            browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--require-png", action="store_true")
    args = parser.parse_args()
    output = Path(tempfile.mkdtemp(prefix="poster-export-shots-"))
    print("Export screenshots:", output)
    for named in [False, True]:
        os.environ.pop("POSTER_DESIGN_REVIEWERS_JSON", None)
        if named:
            os.environ["POSTER_DESIGN_REVIEWERS_JSON"] = json.dumps(
                {
                    "synthetic-reviewer": {
                        "token_sha256": hashlib.sha256(TOKEN.encode()).hexdigest(),
                        "scopes": ["approve", "export"],
                    }
                }
            )
        with disposable_server() as origin:
            run(origin, output, named, args.require_png)
