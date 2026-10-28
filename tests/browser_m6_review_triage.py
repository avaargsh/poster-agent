"""Explicit Chromium smoke against the real FastAPI HTML, with fake OCR API responses.

This checks operator UI behavior only. It does not run PaddleOCR, authenticate
reviewers, validate real SKU rights, or approve poster quality. The browser
mock is scoped to /api/batches/.../m6-ocr-review for this isolated test.
"""
from __future__ import annotations

import base64
import json
import socket
import shutil
import subprocess
import sys
import time
from urllib.parse import parse_qs, urlsplit
from urllib.request import urlopen

from playwright.sync_api import sync_playwright

SKU_IDS = ("sku-one", "sku-two", "sku-three")
VARIANTS = ("square", "feed_4x5_native")
CHECKS = (
    "sku_integrity", "text_legibility", "title", "subtitle", "price",
    "original_price", "price_position", "original_price_position",
    "cta", "composition",
)
# A 1x1 synthetic PNG, never a real poster.
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"
    "AAAADUlEQVQIHWP4z8DwHwAFgAI/ScL/nwAAAABJRU5ErkJggg=="
)


def _check(condition: bool, description: str) -> None:
    if not condition:
        raise AssertionError(description)


def _port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _fixture(batch: str, sku: str) -> dict:
    return {
        "cohort_id": batch,
        "reviews_csv_sha256": "a" * 64 if batch.endswith("A") else "c" * 64,
        "ocr_capture_sha256": "b" * 64 if batch.endswith("A") else "d" * 64,
        "ocr_capture_consistency": "SYNTHETIC_BROWSER_STUB",
        "sku_ids": list(SKU_IDS),
        "selected_sku_id": sku,
        "variants": [{
            "variant_id": variant, "poster_id": "p" * 32,
            "image_sha256": "f" * 64,
            "review_labels": {**dict.fromkeys(CHECKS, "UNVERIFIED"),
                              "overall_accept": "UNVERIFIED",
                              "reviewer_alias": "", "overall_reason": ""},
            "recognized_line_count": 1, "preview_line_count": 1,
            "lines_truncated_count": 0, "unlocated_line_count": 0,
            "lines": [{"text": "synthetic", "confidence": 0.9, "bbox": [0, 0, 1, 1]}],
            "status": "CAPTURED_REVIEW_REQUIRED",
        } for variant in VARIANTS],
        "automatically_accepted": False,
        "quality_verified": False,
        "runtime_attested": False,
    }


def _fill_and_save(page, failure=False) -> None:
    cards = page.locator("#m6OcrReviewRows section[data-m6-variant]")
    _check(cards.count() == 2, "expected exactly two frozen poster variants")
    for index in range(2):
        card = cards.nth(index)
        for field in CHECKS:
            value = "FAIL" if failure and index == 1 and field == "price" else "PASS"
            card.locator(f'select[data-m6-check="{field}"]').select_option(value)
        card.locator('input[data-m6-alias]').fill("reviewer1")
        card.locator('select[data-m6-overall]').select_option(
            "FAIL" if failure and index == 1 else "PASS"
        )
        if failure and index == 1:
            card.locator('input[data-m6-reason]').fill("Synthetic price mismatch")
    page.locator("#m6ReviewSaveSku").click()
    _check("暂存" in page.locator("#m6ReviewSidecarStatus").inner_text(), "save failed")


def main() -> None:
    port = _port()
    origin = f"http://127.0.0.1:{port}"
    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app",
         "--host", "127.0.0.1", "--port", str(port), "--no-access-log"],
        stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT,
    )
    try:
        for _ in range(80):
            if server.poll() is not None:
                raise RuntimeError("FastAPI server exited before browser test")
            try:
                with urlopen(origin + "/", timeout=1) as response:
                    if response.status == 200:
                        break
            except OSError:
                time.sleep(0.15)
        else:
            raise RuntimeError("FastAPI server did not start")

        response_state = {"invalid": False}
        observed = []

        def stub_review(route):
            url = urlsplit(route.request.url)
            segments = url.path.split("/")
            if "/image/" in url.path:
                route.fulfill(status=200, body=PNG, content_type="image/png")
                return
            batch = segments[3]
            sku = parse_qs(url.query).get("sku_id", [SKU_IDS[0]])[0]
            observed.append((batch, sku))
            if response_state["invalid"]:
                route.fulfill(
                    status=409, content_type="application/json",
                    body=json.dumps({"detail": "synthetic digest changed"}),
                )
                return
            route.fulfill(
                status=200, content_type="application/json",
                body=json.dumps(_fixture(batch, sku)),
            )

        def fake_sidecar_export(route):
            payload = route.request.post_data_json
            route.fulfill(
                status=200, content_type="application/json",
                body=json.dumps({
                    "status": "OPERATOR_ASSERTED_NOT_ATTESTED",
                    "reviews_csv_sha256": payload["reviews_csv_sha256"],
                    "ocr_capture_sha256": payload["ocr_capture_sha256"],
                    "decisions": payload["decisions"],
                }),
            )

        def fake_worksheet(route):
            payload = route.request.post_data_json
            csv = (
                "sku_id,variant_id,overall_accept,reviewed_at_utc\\n"
                "sku-one,square,PASS,\\n"
                "sku-two,square,UNVERIFIED,\\n"
            )
            route.fulfill(
                status=200, body=csv,
                headers={
                    "Content-Type": "text/csv; charset=utf-8",
                    "Content-Disposition":
                        'attachment; filename="m6-operator-worksheet-draft.csv"',
                    "X-Worksheet-Status": "OPERATOR_ASSERTED_NOT_ATTESTED",
                    "X-Source-Reviews-SHA256": payload["reviews_csv_sha256"],
                },
            )

        cutout_state={"sha":"a"*64,"decision":"UNVERIFIED","save_calls":0}
        def cutout_stub(route):
            url=urlsplit(route.request.url)
            if url.path.endswith("/comparison"):
                route.fulfill(status=200,body=PNG,content_type="image/png")
                return
            if route.request.method=="POST":
                payload=route.request.post_data_json
                _check(payload["expected_csv_sha256"]==cutout_state["sha"],
                       "stale cutout CSV digest accepted")
                _check(payload["sku_id"]=="sku-one", "wrong cutout SKU saved")
                _check(payload["subject_integrity_review"]=="PASS" and
                       payload["edge_artifact_review"]=="PASS" and
                       payload["brand_mark_review"]=="PASS",
                       "cutout review lost mandatory human fields")
                cutout_state["sha"]="e"*64
                cutout_state["decision"]="PASS"
                cutout_state["save_calls"]+=1
            route.fulfill(status=200,content_type="application/json",
                body=json.dumps({
                    "batch_id":"m6-browser-fixture-A","cohort_id":"m6-test",
                    "csv_sha256":cutout_state["sha"],"status":
                       "OPERATOR_ASSERTED_NOT_ATTESTED" if
                       cutout_state["decision"]=="PASS" else "HUMAN_REVIEW_REQUIRED",
                    "candidate_count":1,"operator_accepted_count":
                       cutout_state["save_calls"],
                    "operator_rejected_count":0,"model_runtime_attested":False,
                    "rows":[{
                        "sku_id":"sku-one","quality_state":"REVIEW_REQUIRED",
                        "human_accept":cutout_state["decision"],
                        "subject_integrity_review":cutout_state["decision"],
                        "edge_artifact_review":cutout_state["decision"],
                        "brand_mark_review":cutout_state["decision"],
                        "comparison_url":"/api/batches/m6-browser-fixture-A/"
                           "m6-cutout-review/sku-one/comparison",
                        "machine_risks":"SUBJECT_TOUCHES_FRAME",
                        "foreground_fraction":"0.4","soft_alpha_fraction":"0.02",
                        "reviewer_alias":"","reviewer_notes":""
                    }]
                }))
        with sync_playwright() as pw:
            system_chrome = shutil.which("google-chrome") or shutil.which(
                "google-chrome-stable"
            )
            launch = {"executable_path": system_chrome} if system_chrome else {}
            browser = pw.chromium.launch(
                headless=True, args=["--no-sandbox"], **launch
            )
            try:
                page = browser.new_page(viewport={"width": 1440, "height": 1000})
                page.route("**/api/batches/*/m6-cutout-review**", cutout_stub)
                page.route("**/api/batches/*/m6-ocr-review**", stub_review)
                page.route("**/api/batches/*/m6-review-sidecar", fake_sidecar_export)
                page.route("**/api/batches/*/m6-review-sidecar/worksheet", fake_worksheet)
                page.goto(origin + "/", wait_until="domcontentloaded")
                page.locator("#nav-batch").click()
                page.evaluate(
                    "() => { currentBatchId='m6-browser-fixture-A';"
                    " lastBatchStatus='COMPLETED'; }"
                )
                page.evaluate(
                    "() => {document.getElementById('batchResults').hidden=false;"
                    " document.getElementById('openM6CutoutReview').disabled=false;}"
                )
                page.locator("#openM6CutoutReview").click()
                page.locator("#m6CutoutReviewPanel").wait_for(state="visible")
                page.locator("#m6CutoutSubject").select_option("PASS")
                page.locator("#m6CutoutEdge").select_option("PASS")
                page.locator("#m6CutoutBrand").select_option("PASS")
                page.locator("#m6CutoutDecision").select_option("PASS")
                page.locator("#m6CutoutReviewer").fill("human-reviewer")
                page.locator("#m6CutoutSave").click()
                page.wait_for_function(
                    "() => document.querySelector('#m6CutoutStatus').textContent"
                    ".includes('人工审核已原子保存')"
                )
                _check(cutout_state["save_calls"]==1,
                       "explicit cutout review save was not invoked")
                _check(page.locator("#m6CutoutComparison").get_attribute("src")
                       .endswith("/comparison"),
                       "cutout side-by-side comparison URL is missing")
                print("PASS: Chromium cutout side-by-side human review, explicit"
                      " CAS-save and model-unattested status")
                page.evaluate(
                    "() => loadM6OCRReview('m6-browser-fixture-A', 'sku-one')"
                )
                queue = page.locator("#m6ReviewQueue [role=listitem]")
                _check(queue.count() == 3, "all SKUs need review initially")
                _fill_and_save(page)
                _check(queue.count() == 2, "completed SKU should leave actionable queue")
                _check(page.locator("#m6ReviewDownloadWorksheet").is_enabled(),
                       "verified human draft should enable worksheet download")
                with page.expect_download() as downloaded:
                    page.locator("#m6ReviewDownloadWorksheet").click()
                artifact = downloaded.value
                _check(artifact.suggested_filename == "m6-operator-worksheet-draft.csv",
                       "unexpected worksheet filename")
                _check("已导出独立人工评审 CSV 草稿" in page.locator(
                    "#m6ReviewSidecarStatus").inner_text(),
                    "download must warn that operator worksheet is not approval")


                page.locator("#m6ReviewQueueFilter").select_option("missing")
                _check(queue.count() == 2, "missing filter should list two SKUs")
                page.locator("#m6ReviewQueueFilter").select_option("failed")
                _check(queue.count() == 0, "no reported FAIL before operator entry")
                page.locator("#m6ReviewQueueFilter").select_option("all")
                _check(queue.count() == 3, "all filter must retain complete SKU")
                page.locator("#m6ReviewQueueFilter").select_option("actionable")
                queue.get_by_role("button", name="核对 sku-two 的 OCR 与人工审核").click()
                page.wait_for_function(
                    "() => document.querySelector('#m6OcrSkuPicker').value === 'sku-two'"
                )
                _check(("m6-browser-fixture-A", "sku-two") in observed,
                       "SKU navigation did not call the expected OCR API")
                _fill_and_save(page, failure=True)
                page.locator("#m6ReviewQueueFilter").select_option("failed")
                _check(queue.count() == 1, "FAIL filter should show one SKU")
                _check("原生 4:5" in queue.first.inner_text(),
                       "FAIL variant must be identified")

                response_state["invalid"] = True
                page.locator("#m6OcrSkuPicker").select_option("sku-three")
                page.wait_for_function(
                    "() => document.getElementById('m6OcrReviewStatus')"
                    ".textContent.includes('无法核对 OCR 证据')"
                )
                _check(queue.count() == 0, "stale triage links survived failed refresh")
                _check(page.locator("#m6ReviewSaveSku").is_disabled(),
                       "review save must be blocked when evidence is invalid")
                _check(page.locator("#m6ReviewImportSidecar").is_disabled(),
                       "sidecar restore must be blocked when evidence is invalid")
                _check(page.locator("#m6ReviewDownloadWorksheet").is_disabled(),
                       "worksheet download must be blocked after failed evidence refresh")

                response_state["invalid"] = False
                page.evaluate(
                    "() => loadM6OCRReview('m6-browser-fixture-A','sku-three')"
                )
                _check(queue.count() == 1, "previous FAIL draft did not recover")
                _check("sku-two" in queue.first.inner_text(),
                       "recovered queue lost operator FAIL")

                page.evaluate(
                    "() => { resetM6OCRReview(); "
                    "currentBatchId='m6-browser-fixture-B'; "
                    "lastBatchStatus='COMPLETED'; }"
                )
                page.evaluate(
                    "() => loadM6OCRReview('m6-browser-fixture-B','sku-one')"
                )
                page.locator("#m6ReviewQueueFilter").select_option("failed")
                _check(queue.count() == 0, "drafts leaked across batches")
                _check("页面内暂存 0/6" in page.locator(
                    "#m6ReviewDraftProgress"
                ).inner_text(), "batch switch did not reset draft counters")
                _check(page.locator("#m6ReviewExportSidecar").is_disabled(),
                       "old batch export must remain disabled")
                _check(page.locator("#m6ReviewDownloadWorksheet").is_disabled(),
                       "cross-batch draft worksheet must stay disabled")
                print("PASS: Chromium FastAPI page, queue filters, SKU navigation, "
                      "operator FAIL, stale evidence lockout, recovery, cross-batch reset")
            finally:
                browser.close()
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=5)


if __name__ == "__main__":
    main()
