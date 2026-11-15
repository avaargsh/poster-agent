"""Actual stdio MCP suggestion followed by explicit review in real Chrome."""

import asyncio
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

from browser_canvas_workspace import disposable_server, product_png
from browser_support import chromium_launch_options
from mcp import Client, StdioServerParameters
from playwright.sync_api import expect, sync_playwright


async def propose(origin, document_id, revision, operations, summary):
    parameters = StdioServerParameters(
        command=sys.executable, args=["-m", "app.mcp_server", "--base-url", origin]
    )
    async with Client(parameters, mode="legacy", read_timeout_seconds=25) as client:
        result = await client.call_tool(
            "propose_design_edit",
            {
                "document_id": document_id,
                "proposal": {
                    "request_id": str(uuid4()),
                    "expected_revision": revision,
                    "summary": summary,
                    "operations": operations,
                },
            },
        )
        assert not result.is_error, result
        return result.structured_content


def submit_proposal(origin, document_id, revision, operations, summary):
    # Playwright's sync interface owns its event loop on the calling thread.
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(
            asyncio.run, propose(origin, document_id, revision, operations, summary)
        ).result(timeout=30)


def run(origin, output):
    output.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, **chromium_launch_options())
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            response = page.request.post(
                origin + "/api/designs",
                multipart={
                    "title": "原始海报标题",
                    "price": "99.90",
                    "product": {
                        "name": "product.png",
                        "mimeType": "image/png",
                        "buffer": product_png(),
                    },
                },
            )
            assert response.ok, response.text()
            doc = response.json()["document"]
            doc_id = doc["document_id"]
            url = origin + "/api/designs/" + doc_id
            proposal = submit_proposal(
                origin,
                doc_id,
                1,
                [
                    {"op": "set_text", "node_id": "title", "text": "Agent 提议的新标题"},
                    {"op": "set_style", "node_id": "price", "font_size": 100},
                ],
                "更新标题并放大价格",
            )
            assert page.request.get(url).json()["document"]["revision"] == 1
            page.goto(proposal["confirmation_url"], wait_until="networkidle")
            panel = page.get_by_role("complementary", name="修改建议", exact=True)
            expect(panel.get_by_text("更新标题并放大价格", exact=True)).to_be_visible()
            expect(panel.get_by_role("button", name="应用修改建议", exact=True)).to_be_disabled()
            panel.get_by_role("button", name="预览建议", exact=True).click()
            expect(page.locator(".canvas-save-status")).to_have_text("建议预览 · 基于版本 1")
            expect(page.locator(".canvas-surface")).to_contain_text("Agent 提议的新标题")
            assert page.request.get(url).json()["document"]["revision"] == 1
            # Preview is read-only, even when the canvas has keyboard focus.
            page.locator(".canvas-surface").focus()
            page.keyboard.press("ArrowRight")
            assert page.request.get(url).json()["document"]["revision"] == 1
            page.screenshot(path=str(output / "proposal-preview.png"), full_page=True)
            panel.get_by_role("button", name="应用修改建议", exact=True).click()
            expect(page.locator(".canvas-save-status")).to_have_text("已保存 · 版本 2")
            expect(panel.get_by_text("已应用于版本 2", exact=True)).to_be_visible()
            current = page.request.get(url).json()["document"]
            assert next(n for n in current["nodes"] if n["role"] == "price")["font_size"] == 100
            assert (
                "".join(
                    line["text"]
                    for n in current["nodes"]
                    if n["role"] == "title"
                    for line in n["lines"]
                )
                == "Agent 提议的新标题"
            )
            page.reload(wait_until="networkidle")
            expect(panel.get_by_text("已应用于版本 2", exact=True)).to_be_visible()
            assert page.request.post(
                url + "/proposals/" + proposal["proposal_id"] + "/apply"
            ).json()["replayed"]

            stale = submit_proposal(
                origin,
                doc_id,
                2,
                [{"op": "transform_node", "node_id": "product", "x": 600}],
                "移动商品",
            )
            changed = page.request.post(
                url + "/commands",
                data={
                    "command_id": str(uuid4()),
                    "expected_revision": 2,
                    "actor": "user",
                    "operations": [{"op": "set_style", "node_id": "price", "color": "#123456"}],
                },
            )
            assert changed.ok
            page.goto(stale["confirmation_url"], wait_until="networkidle")
            expect(
                panel.get_by_text("建议已过期，请基于最新版本重新提出。", exact=True)
            ).to_be_visible()
            panel.get_by_role("button", name="预览建议", exact=True).click()
            expect(panel.get_by_role("button", name="应用修改建议", exact=True)).to_be_disabled()
            assert page.request.get(url).json()["document"]["revision"] == 3

            # Human editing can coexist with a suggestion until explicit application.
            fresh = submit_proposal(
                origin,
                doc_id,
                3,
                [
                    {
                        "op": "set_price",
                        "price": "88.80",
                        "source_text": "演示操作员指定活动价88.80元",
                    }
                ],
                "活动价调整为88.80元",
            )
            page.goto(fresh["confirmation_url"], wait_until="networkidle")
            expect(panel.get_by_text("活动价调整为88.80元", exact=True)).to_be_visible()
            panel.get_by_role("button", name="返回属性", exact=True).click()
            page.get_by_role("button", name="选择主标题图层", exact=True).click()
            page.get_by_label("图层文案", exact=True).fill("还未保存的本地草稿")
            page.get_by_role("button", name="修改建议", exact=True).click()
            expect(panel.get_by_text("活动价调整为88.80元", exact=True)).to_be_visible()
            panel.get_by_role("button", name="预览建议", exact=True).click()
            expect(
                panel.get_by_role("button", name="放弃属性草稿并应用建议", exact=True)
            ).to_be_enabled()
            calls = []

            def lose_ack(route):
                calls.append(route.request.url)
                if len(calls) == 1:
                    result = route.fetch()
                    assert result.ok
                    route.abort("failed")
                else:
                    route.continue_()

            page.route("**/proposals/*/apply", lose_ack)
            panel.get_by_role("button", name="放弃属性草稿并应用建议", exact=True).click()
            expect(page.get_by_role("alert")).to_contain_text("保存结果待确认")
            page.get_by_role("button", name="重试原保存请求", exact=True).click()
            expect(page.locator(".canvas-save-status")).to_have_text("已保存 · 版本 4")
            assert len(calls) == 2 and calls[0] == calls[1]
            assert page.request.get(url).json()["document"]["facts"]["price"] == "88.80"
            assert page.request.get(url).json()["document"]["revision"] == 4
            page.unroute("**/proposals/*/apply", lose_ack)
            page.set_viewport_size({"width": 390, "height": 844})
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            page.screenshot(path=str(output / "proposal-mobile.png"), full_page=True)
            assert not errors, errors
            print(
                "PASS: MCP stdio proposal → preview → explicit apply → replay/reload/stale/conflict/draft-discard/ACK-loss/mobile"
            )
        finally:
            browser.close()


if __name__ == "__main__":
    output = Path(tempfile.mkdtemp(prefix="poster-proposal-shots-"))
    print("Proposal screenshots:", output, flush=True)
    with disposable_server() as origin:
        run(origin, output)
