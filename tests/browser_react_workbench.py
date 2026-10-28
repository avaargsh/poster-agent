"""Opt-in real Chrome regression for the React workbench against a disposable server.

Usage: python tests/browser_react_workbench.py http://127.0.0.1:8000 /tmp/screenshots
Only synthetic product pixels and the explicitly selected free local background are used.
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

from browser_support import chromium_launch_options
from PIL import Image, ImageDraw
from playwright.sync_api import expect, sync_playwright


def run(origin: str, output: Path):
    output.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGBA", (360, 480), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((95, 55, 265, 425), radius=35, fill="#b8c6fa")
    draw.rounded_rectangle((115, 5, 245, 95), radius=15, fill="#454291")
    draw.rectangle((110, 200, 250, 290), fill="#f5f3ff")
    stream = io.BytesIO()
    image.save(stream, format="PNG")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, **chromium_launch_options())
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on(
                "console",
                lambda message: errors.append(message.text) if message.type == "error" else None,
            )
            page.goto(origin, wait_until="networkidle")
            expect(page.locator("html")).to_have_attribute("data-ready", "true")
            assert page.evaluate("document.getElementById('root').inert === false")
            page.screenshot(path=str(output / "desktop-empty.png"), full_page=True)

            # Draft restoration must preserve deliberately empty fields after hydration.
            page.locator("#create-title").fill("春日新品特惠")
            page.locator("#create-subtitle").fill("")
            page.locator("#saveCreateDraft").click()
            page.reload(wait_until="networkidle")
            page.locator("#restoreCreateDraft").click()
            expect(page.locator("#create-title")).to_have_value("春日新品特惠")
            expect(page.locator("#create-subtitle")).to_have_value("")
            expect(page.locator("#create-background_mode")).to_have_value("local")
            page.locator("#create-product").set_input_files(
                {
                    "name": "synthetic-product.png",
                    "mimeType": "image/png",
                    "buffer": stream.getvalue(),
                }
            )
            expect(page.locator("#productSelection")).to_be_visible()
            page.locator("#generate").click()
            expect(page.locator("#preview")).to_be_visible(timeout=20000)
            expect(page.locator("#editTitle")).to_have_value("春日新品特惠")
            expect(page.locator("#source")).to_have_text("本地背景")
            page.get_by_role("button", name="放大预览", exact=True).click()
            expect(page.locator(".preview-toolbar output")).to_have_text("125%")
            assert (
                page.locator("#previewStage").evaluate(
                    "node => node.style.getPropertyValue('--preview-zoom')"
                )
                == "1.25"
            )
            page.get_by_role("button", name="适应画布", exact=True).click()
            page.screenshot(path=str(output / "desktop-generated.png"), full_page=True)

            # Existing form values must survive React state changes and workspace navigation.
            page.locator("#editTitle").fill("春日新品第二版")
            page.locator("#nav-batch").click()
            expect(page.locator("#view-batch")).to_be_visible()
            page.locator("#nav-batch").press("ArrowDown")
            expect(page.locator("#nav-review")).to_be_focused()
            expect(page.locator("#view-review")).to_be_visible()
            page.locator("#nav-history").click()
            expect(page.locator("#view-history")).to_be_visible()
            page.locator("#nav-create").click()
            expect(page.locator("#editTitle")).to_have_value("春日新品第二版")
            page.locator("#editor-tab-export").click()
            expect(page.locator("#editor-export")).to_be_visible()
            page.locator("#compareNativeLayouts").click()
            expect(page.locator("#nativeCompareGrid")).to_be_visible(timeout=20000)
            expect(page.locator("#nativeCompareSplit")).to_have_js_property("naturalWidth", 1080)
            expect(page.locator("#nativeCompareFocus")).to_have_js_property("naturalWidth", 1080)

            page.set_viewport_size({"width": 390, "height": 844})
            for workspace in ("create", "batch", "review", "history"):
                page.locator(f"#nav-{workspace}").click()
                expect(page.locator(f"#view-{workspace}")).to_be_visible()
                assert not page.evaluate("document.documentElement.scrollWidth > innerWidth"), (
                    workspace
                )
                page.screenshot(path=str(output / f"mobile-{workspace}.png"), full_page=True)
            assert not errors, errors

            # Failure to load the application must leave a visible recovery message and
            # prevent clicks/keyboard submission into uninitialized server-rendered forms.
            broken = browser.new_page()
            broken.route(
                "**/*",
                lambda route: (
                    route.abort()
                    if route.request.resource_type == "script"
                    and (
                        "/assets/main-" in route.request.url or "/src/main.tsx" in route.request.url
                    )
                    else route.continue_()
                ),
            )
            broken.goto(origin, wait_until="networkidle")
            expect(broken.locator("#boot-status")).to_be_visible()
            assert broken.locator("#root").evaluate("node => node.inert")
            broken.close()
            print(
                "PASS: hydration, draft restore, local generation, zoom, navigation, exports, "
                "four mobile workspaces, startup recovery; no browser errors"
            )
        finally:
            browser.close()


if __name__ == "__main__":
    run(sys.argv[1], Path(sys.argv[2]))
