"""Local web batch import: full-cohort preflight, progress, and safe resume."""
from __future__ import annotations

import csv
import io
import json
from importlib import import_module

from fastapi.testclient import TestClient
from PIL import Image

from app import storage
from app.main import app

client = TestClient(app)


def _png(color):
    stream = io.BytesIO()
    Image.new("RGBA", (128, 160), color).save(stream, "PNG")
    return stream.getvalue()


def _csv(rows):
    stream = io.StringIO(newline="")
    fields = [
        "sku_id", "product_image", "title", "price",
        "original_price", "subtitle", "cta", "style", "logo_image",
    ]
    writer = csv.DictWriter(stream, fieldnames=fields)
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode("utf-8")


def _rows():
    return [
        {
            "sku_id": "sku-one", "product_image": "sku_one.png",
            "title": "电动牙刷促销", "price": "199", "original_price": "399",
            "subtitle": "限时福利", "cta": "立即购买", "style": "蓝色科技风",
            "logo_image": "logo.png",
        },
        {
            "sku_id": "sku-two", "product_image": "sku_two.png",
            "title": "智能生活好物", "price": "89", "original_price": "129",
            "subtitle": "品质生活", "cta": "立即抢购", "style": "蓝色科技风",
            "logo_image": "",
        },
    ]


def _upload_batch(monkeypatch, tmp_path, rows=None):
    monkeypatch.setenv("POSTER_BATCH_DIR", str(tmp_path / "batch-root"))
    monkeypatch.setenv("POSTER_DATA_DIR", str(tmp_path / "posters"))
    monkeypatch.setenv("POSTER_EXECUTION_DB", str(tmp_path / "executions.sqlite3"))
    images = {
        "sku_one.png": _png((60, 110, 195, 255)),
        "sku_two.png": _png((195, 70, 45, 255)),
        "logo.png": _png((50, 200, 100, 180)),
    }
    files = [
        ("manifest", ("real_skus.csv", _csv(rows or _rows()), "text/csv")),
        *[
            ("images", (name, data, "image/png"))
            for name, data in images.items()
        ],
    ]
    response = client.post("/api/batches", files=files)
    return response


def test_web_preflight_validates_entire_cohort_without_creating_posters(
    monkeypatch, tmp_path,
):
    response = _upload_batch(monkeypatch, tmp_path)
    assert response.status_code == 201, response.text
    state = response.json()
    assert state["status"] == "READY"
    assert state["total"] == 2
    assert state["processed"] == 0
    assert state["plan"]["no_paid_model"] is True
    assert state["plan"]["unique_source_files"] == 2
    assert [row["status"] for row in state["rows"]] == ["PENDING", "PENDING"]
    assert not storage.root().exists()
    assert not (tmp_path / "executions.sqlite3").exists()
    staged = tmp_path / "batch-root" / state["batch_id"]
    assert (staged / "manifest.csv").is_file()
    assert (staged / "batch_preflight.json").is_file()


def test_web_apply_reports_progress_and_reuses_existing_m5g_journal(
    monkeypatch, tmp_path,
):
    response = _upload_batch(monkeypatch, tmp_path)
    assert response.status_code == 201, response.text
    batch_id = response.json()["batch_id"]

    def forbid_paid_provider(*_args, **_kwargs):
        raise AssertionError("web batch import must never call Qwen")

    monkeypatch.setattr("app.main.generate_ai_background", forbid_paid_provider)
    started = client.post(f"/api/batches/{batch_id}/apply")
    assert started.status_code == 202, started.text
    assert started.json()["status"] == "RUNNING"

    finished = client.get(f"/api/batches/{batch_id}")
    assert finished.status_code == 200
    state = finished.json()
    assert state["status"] == "COMPLETED"
    assert state["processed"] == state["total"] == 2
    assert all(row["status"] == "COMPLETED" for row in state["rows"])
    poster_ids = {row["sku_id"]: row["poster_id"] for row in state["rows"]}
    assert len(set(poster_ids.values())) == 2

    batch_dir = tmp_path / "batch-root" / batch_id
    journal = json.loads((batch_dir / "batch_import_results.json").read_text())
    assert journal["completed"] is True
    assert journal["expected_sku_ids"] == ["sku-one", "sku-two"]
    assert journal["rows"]["sku-one"]["poster_id"] == poster_ids["sku-one"]
    assert all(
        storage.view(poster_id)["spec"]["background_mode"] == "local"
        for poster_id in poster_ids.values()
    )

    again = client.post(f"/api/batches/{batch_id}/apply")
    assert again.status_code == 202
    after = client.get(f"/api/batches/{batch_id}").json()
    assert after["status"] == "COMPLETED"
    assert {row["sku_id"]: row["poster_id"] for row in after["rows"]} == poster_ids
    assert len(list(storage.root().iterdir())) == 2


def test_failed_web_batch_can_resume_without_duplicate_posters(monkeypatch, tmp_path):
    response = _upload_batch(monkeypatch, tmp_path)
    assert response.status_code == 201, response.text
    batch_id = response.json()["batch_id"]
    batch = import_module("scripts.real_sku_batch")
    original = batch._file_parts
    calls = {"count": 0}

    def fail_second_row(item):
        calls["count"] += 1
        if calls["count"] == 2:
            raise batch.BatchError("simulated operator interruption")
        return original(item)

    monkeypatch.setattr(batch, "_file_parts", fail_second_row)
    assert client.post(f"/api/batches/{batch_id}/apply").status_code == 202
    failed = client.get(f"/api/batches/{batch_id}").json()
    assert failed["status"] == "FAILED"
    assert failed["processed"] == 1
    first_id = next(row["poster_id"] for row in failed["rows"] if row["sku_id"] == "sku-one")
    assert next(row for row in failed["rows"] if row["sku_id"] == "sku-two")["status"] == "FAILED"
    assert len(list(storage.root().iterdir())) == 1

    monkeypatch.setattr(batch, "_file_parts", original)
    assert client.post(f"/api/batches/{batch_id}/apply").status_code == 202
    complete = client.get(f"/api/batches/{batch_id}").json()
    assert complete["status"] == "COMPLETED"
    assert complete["processed"] == 2
    assert next(row for row in complete["rows"] if row["sku_id"] == "sku-one")["poster_id"] == first_id
    assert len(list(storage.root().iterdir())) == 2


def test_web_preflight_rejects_path_traversal_before_any_poster(
    monkeypatch, tmp_path,
):
    rows = _rows()[:1]
    rows[0]["product_image"] = "../outside.png"
    monkeypatch.setenv("POSTER_BATCH_DIR", str(tmp_path / "batch-root"))
    monkeypatch.setenv("POSTER_DATA_DIR", str(tmp_path / "posters"))
    monkeypatch.setenv("POSTER_EXECUTION_DB", str(tmp_path / "executions.sqlite3"))
    files = [
        ("manifest", ("manifest.csv", _csv(rows), "text/csv")),
        ("images", ("outside.png", _png((1, 2, 3, 255)), "image/png")),
        ("images", ("logo.png", _png((4, 5, 6, 255)), "image/png")),
    ]
    response = client.post("/api/batches", files=files)
    assert response.status_code == 422
    assert "目录之外" in response.json()["detail"]
    assert not storage.root().exists()
    assert not (tmp_path / "executions.sqlite3").exists()
    assert list((tmp_path / "batch-root").iterdir()) == []


def test_web_preflight_rejects_unused_images_and_duplicate_names(monkeypatch, tmp_path):
    monkeypatch.setenv("POSTER_BATCH_DIR", str(tmp_path / "batch-root"))
    monkeypatch.setenv("POSTER_DATA_DIR", str(tmp_path / "posters"))
    files = [
        ("manifest", ("manifest.csv", _csv(_rows()[:1]), "text/csv")),
        ("images", ("sku_one.png", _png((1, 2, 3, 255)), "image/png")),
        ("images", ("logo.png", _png((4, 5, 6, 255)), "image/png")),
        ("images", ("unused.png", _png((7, 8, 9, 255)), "image/png")),
    ]
    response = client.post("/api/batches", files=files)
    assert response.status_code == 422
    assert "未被 CSV 引用" in response.json()["detail"]
    assert list((tmp_path / "batch-root").iterdir()) == []

    duplicate = [
        ("manifest", ("manifest.csv", _csv(_rows()[:1]), "text/csv")),
        ("images", ("sku_one.png", _png((1, 2, 3, 255)), "image/png")),
        ("images", ("SKU_ONE.PNG", _png((4, 5, 6, 255)), "image/png")),
    ]
    response = client.post("/api/batches", files=duplicate)
    assert response.status_code == 422
    assert "文件名重复" in response.json()["detail"]


def test_web_batch_ui_exposes_explicit_preflight_apply_and_progress_controls():
    response = client.get("/")
    assert response.status_code == 200
    html = response.text
    for element in (
        'id="batchManifest"', 'id="batchImages"', 'id="preflightBatch"',
        'id="applyBatch"', 'id="batchProgress"', 'id="batchRows"',
    ):
        assert element in html
    assert "/api/batches" in html
    assert "restoreLastBatch" in html
    assert "scheduleBatchPoll" in html
    assert "只有点击生成后才开始处理" in html
    assert "不调用 Qwen 生图或 OCR" in html

def test_batch_status_does_not_overwrite_completion_from_a_stale_running_snapshot(
    monkeypatch, tmp_path,
):
    response = _upload_batch(monkeypatch, tmp_path)
    assert response.status_code == 201
    batch_id = response.json()["batch_id"]
    batch_dir = tmp_path / "batch-root" / batch_id
    status_path = batch_dir / "batch_status.json"
    state = json.loads(status_path.read_text(encoding="utf-8"))
    state["status"] = "COMPLETED"
    status_path.write_text(json.dumps(state), encoding="utf-8")

    routes = import_module("app.batch_import_routes")
    original_read_state = routes._read_state
    calls = {"count": 0}

    def stale_first_read(path):
        calls["count"] += 1
        actual = original_read_state(path)
        return {**actual, "status": "RUNNING"} if calls["count"] == 1 else actual

    monkeypatch.setattr(routes, "_read_state", stale_first_read)
    response = client.get(f"/api/batches/{batch_id}")
    assert response.status_code == 200
    assert response.json()["status"] == "COMPLETED"
    assert json.loads(status_path.read_text(encoding="utf-8"))["status"] == "COMPLETED"




def test_web_batch_review_scope_rejects_unfinished_and_returns_exact_completed_cohort(
    monkeypatch, tmp_path,
):
    staged = _upload_batch(monkeypatch, tmp_path)
    assert staged.status_code == 201, staged.text
    batch_id = staged.json()["batch_id"]
    url = f"/api/batches/{batch_id}/review-scope"

    premature = client.get(url)
    assert premature.status_code == 409
    assert not storage.root().exists()

    started = client.post(f"/api/batches/{batch_id}/apply")
    assert started.status_code == 202, started.text

    completed = client.get(url)
    assert completed.status_code == 200, completed.text
    body = completed.json()
    assert body["batch_id"] == batch_id
    assert body["completed"] is True
    assert body["total_skus"] == 2
    assert len(body["manifest_sha256"]) == 64
    assert [row["sku_id"] for row in body["rows"]] == ["sku-one", "sku-two"]
    assert len({row["poster_id"] for row in body["rows"]}) == 2
    assert all(len(row["poster_id"]) == 32 for row in body["rows"])
    assert "request_sha256" not in completed.text
    assert "product_sha256" not in completed.text
    assert "idempotency_key" not in completed.text
    assert str(tmp_path) not in completed.text

    reviewed_ids = {row["poster_id"] for row in body["rows"]}
    queue = client.get("/api/trials/review-queue?reviewer=local-operator")
    assert queue.status_code == 200
    assert reviewed_ids.issubset({
        item["poster_id"] for item in queue.json()["items"]
    })


def test_web_batch_review_scope_rejects_corrupt_or_inconsistent_journal(
    monkeypatch, tmp_path,
):
    staged = _upload_batch(monkeypatch, tmp_path)
    assert staged.status_code == 201, staged.text
    batch_id = staged.json()["batch_id"]
    assert client.post(f"/api/batches/{batch_id}/apply").status_code == 202
    batch_dir = tmp_path / "batch-root" / batch_id
    journal = batch_dir / "batch_import_results.json"
    original = json.loads(journal.read_text(encoding="utf-8"))

    mismatches = []
    missing_row = json.loads(json.dumps(original))
    del missing_row["rows"]["sku-two"]
    mismatches.append(missing_row)
    duplicate_poster = json.loads(json.dumps(original))
    duplicate_poster["rows"]["sku-two"]["poster_id"] = (
        duplicate_poster["rows"]["sku-one"]["poster_id"]
    )
    mismatches.append(duplicate_poster)
    changed_manifest = json.loads(json.dumps(original))
    changed_manifest["manifest_sha256"] = "f" * 64
    mismatches.append(changed_manifest)
    bad_version = json.loads(json.dumps(original))
    bad_version["rows"]["sku-one"]["version"] = 999
    mismatches.append(bad_version)

    for invalid in mismatches:
        journal.write_text(json.dumps(invalid), encoding="utf-8")
        assert client.get(f"/api/batches/{batch_id}/review-scope").status_code == 409

    journal.write_text("{broken", encoding="utf-8")
    assert client.get(f"/api/batches/{batch_id}/review-scope").status_code == 409
    journal.write_text(json.dumps(original), encoding="utf-8")
    assert client.get(f"/api/batches/{batch_id}/review-scope").status_code == 200


def test_web_batch_handoff_ui_reuses_existing_review_queue():
    page = client.get("/")
    assert page.status_code == 200
    html = page.text
    assert 'id="reviewCurrentBatch"' in html
    assert 'id="batchReviewScopeStatus"' in html
    assert "/review-scope" in html
    assert "batchReviewScope={ids:" in html
    assert "loadReviewQueue" in html
    assert "当前批次已完成" in html


def test_web_batch_history_listing_filters_pages_without_exposing_inputs(
    tmp_path, monkeypatch,
):
    first = _upload_batch(monkeypatch, tmp_path)
    second = _upload_batch(monkeypatch, tmp_path)
    third = _upload_batch(monkeypatch, tmp_path)
    assert all(r.status_code == 201 for r in (first, second, third))
    ids = [r.json()["batch_id"] for r in (first, second, third)]
    root = tmp_path / "batch-root"
    for index, batch_id in enumerate(ids):
        status_path = root / batch_id / "batch_status.json"
        state = json.loads(status_path.read_text(encoding="utf-8"))
        state["updated_at"] = f"2026-10-24T0{index}:00:00+00:00"
        state["status"] = ["READY", "COMPLETED", "FAILED"][index]
        state["processed"] = index
        status_path.write_text(json.dumps(state), encoding="utf-8")

    response = client.get("/api/batches?limit=2")
    assert response.status_code == 200, response.text
    page = response.json()
    assert page["limit"] == 2 and page["offset"] == 0
    assert page["has_more"] is True
    assert [item["batch_id"] for item in page["items"]] == ids[::-1][:2]
    assert [item["status"] for item in page["items"]] == ["FAILED", "COMPLETED"]
    assert all(
        set(item) == {
            "batch_id", "status", "created_at", "updated_at", "processed", "total"
        } for item in page["items"]
    )
    assert "sku-one" not in response.text
    assert "manifest_sha256" not in response.text
    assert str(tmp_path) not in response.text

    last = client.get("/api/batches?limit=2&offset=2").json()
    assert last["has_more"] is False
    assert [item["batch_id"] for item in last["items"]] == ids[:1]
    only_completed = client.get("/api/batches?status=COMPLETED").json()
    assert [item["batch_id"] for item in only_completed["items"]] == ids[1:2]
    assert only_completed["has_more"] is False


def test_web_batch_history_skips_corrupt_and_symlinked_directories(
    tmp_path, monkeypatch,
):
    first = _upload_batch(monkeypatch, tmp_path)
    assert first.status_code == 201
    root = tmp_path / "batch-root"
    valid = first.json()["batch_id"]
    broken = root / ("f" * 32)
    broken.mkdir()
    (broken / "batch_status.json").write_text("{broken", encoding="utf-8")
    wrong_type = root / ("d" * 32)
    wrong_type.mkdir()
    state = json.loads((root / valid / "batch_status.json").read_text(encoding="utf-8"))
    state["batch_id"] = wrong_type.name
    state["status"] = []
    (wrong_type / "batch_status.json").write_text(json.dumps(state), encoding="utf-8")
    outside = tmp_path / "unrelated"
    outside.mkdir()
    (outside / "private.txt").write_text("do not read", encoding="utf-8")
    (root / ("e" * 32)).symlink_to(outside, target_is_directory=True)
    (root / "notes.txt").write_text("unrelated", encoding="utf-8")

    response = client.get("/api/batches")
    assert response.status_code == 200, response.text
    data = response.json()
    assert [item["batch_id"] for item in data["items"]] == [valid]
    assert data["skipped_invalid"] == 2
    assert "private.txt" not in response.text
    assert (outside / "private.txt").read_text() == "do not read"


def test_web_batch_history_reports_abandoned_running_without_mutating_disk(
    tmp_path, monkeypatch,
):
    response = _upload_batch(monkeypatch, tmp_path)
    assert response.status_code == 201
    batch_id = response.json()["batch_id"]
    status_path = tmp_path / "batch-root" / batch_id / "batch_status.json"
    original = json.loads(status_path.read_text(encoding="utf-8"))
    original["status"] = "RUNNING"
    status_path.write_text(json.dumps(original), encoding="utf-8")
    before = status_path.read_bytes()

    listing = client.get("/api/batches?status=INTERRUPTED")
    assert listing.status_code == 200
    assert [(x["batch_id"], x["status"]) for x in listing.json()["items"]] == [
        (batch_id, "INTERRUPTED")
    ]
    assert status_path.read_bytes() == before
    assert client.get("/api/batches?status=RUNNING").json()["items"] == []


def test_web_batch_history_validates_page_and_status_and_does_not_create_root(
    tmp_path, monkeypatch,
):
    monkeypatch.setenv("POSTER_BATCH_DIR", str(tmp_path / "not-created"))
    good = client.get("/api/batches")
    assert good.status_code == 200
    assert good.json()["items"] == []
    assert good.json()["has_more"] is False
    assert not (tmp_path / "not-created").exists()
    for params in (
        "limit=0", "limit=51", "offset=-1", "offset=10001",
        "status=UNKNOWN", "status=FAILED%27%20OR%201%3D1",
    ):
        response = client.get("/api/batches?" + params)
        assert response.status_code == 422, (params, response.text)


def test_web_batch_history_ui_can_switch_to_older_batches_without_deletion():
    page = client.get("/")
    assert page.status_code == 200
    html = page.text
    for marker in (
        'id="batchHistoryFilter"', 'id="refreshBatchHistory"',
        'id="batchHistoryPrev"', 'id="batchHistoryNext"',
        'id="batchHistoryItems"', 'id="batchHistoryInfo"',
    ):
        assert marker in html
    assert "function selectHistoricalBatch" in html
    assert "async function loadBatchHistory" in html
    assert "batchReviewScope=null" in html
    assert "不会自动删除原图或评测账本" in html


def test_web_batch_review_scope_rejects_missing_corrupt_published_files(
    monkeypatch, tmp_path,
):
    staged = _upload_batch(monkeypatch, tmp_path)
    assert staged.status_code == 201, staged.text
    batch_id = staged.json()["batch_id"]
    assert client.post(f"/api/batches/{batch_id}/apply").status_code == 202
    url = f"/api/batches/{batch_id}/review-scope"
    assert client.get(url).status_code == 200
    poster_id = client.get(url).json()["rows"][0]["poster_id"]
    poster_dir = storage.poster_dir(poster_id)
    image_path = poster_dir / "versions" / "v1.png"
    spec_path = poster_dir / "versions" / "v1.json"
    product_path = poster_dir / "assets" / "product.png"

    image_bytes = image_path.read_bytes()
    image_path.write_bytes(b"not-a-png")
    assert client.get(url).status_code == 409
    image_path.write_bytes(image_bytes)
    assert client.get(url).status_code == 200

    spec_bytes = spec_path.read_bytes()
    spec_path.write_text("{broken", encoding="utf-8")
    assert client.get(url).status_code == 409
    spec_path.write_bytes(spec_bytes)
    assert client.get(url).status_code == 200

    product_bytes = product_path.read_bytes()
    product_path.unlink()
    assert client.get(url).status_code == 409
    product_path.write_bytes(product_bytes)
    assert client.get(url).status_code == 200

    image_path.unlink()
    assert client.get(url).status_code == 409


def test_web_batch_review_scope_rejects_symlinked_image_and_missing_poster(
    monkeypatch, tmp_path,
):
    staged = _upload_batch(monkeypatch, tmp_path)
    assert staged.status_code == 201
    batch_id = staged.json()["batch_id"]
    assert client.post(f"/api/batches/{batch_id}/apply").status_code == 202
    url = f"/api/batches/{batch_id}/review-scope"
    response = client.get(url)
    assert response.status_code == 200
    poster_id = response.json()["rows"][0]["poster_id"]
    img = storage.poster_dir(poster_id) / "versions" / "v1.png"
    original = img.read_bytes()
    outside = tmp_path / "outside.png"
    outside.write_bytes(original)
    img.unlink()
    img.symlink_to(outside)
    assert client.get(url).status_code == 409
    assert outside.read_bytes() == original
    img.unlink()
    img.write_bytes(original)
    assert client.get(url).status_code == 200

    import shutil

    shutil.rmtree(storage.poster_dir(poster_id))
    response = client.get(url)
    assert response.status_code == 409
    assert "已保存" in response.json()["detail"] or "不可用" in response.json()["detail"]


def test_web_batch_review_scope_rejects_mismatched_or_invalid_v1_spec(
    monkeypatch, tmp_path,
):
    staged = _upload_batch(monkeypatch, tmp_path)
    batch_id = staged.json()["batch_id"]
    assert client.post(f"/api/batches/{batch_id}/apply").status_code == 202
    url = f"/api/batches/{batch_id}/review-scope"
    first = client.get(url)
    assert first.status_code == 200
    poster_id = first.json()["rows"][0]["poster_id"]
    spec_path = storage.poster_dir(poster_id) / "versions" / "v1.json"
    original_bytes = spec_path.read_bytes()
    original = json.loads(original_bytes)

    changed = dict(original)
    changed["background_mode"] = "ai"
    spec_path.write_text(json.dumps(changed), encoding="utf-8")
    assert client.get(url).status_code == 409

    # Restore the exact bytes: re-serializing equivalent JSON changes SHA-256.
    spec_path.write_bytes(original_bytes)
    assert client.get(url).status_code == 200


def test_web_m6_package_requires_complete_batch_and_explicit_apply(
    tmp_path, monkeypatch,
):
    uploaded = _upload_batch(monkeypatch, tmp_path)
    assert uploaded.status_code == 201
    batch_id = uploaded.json()["batch_id"]
    endpoint = f"/api/batches/{batch_id}/m6-package"
    target = tmp_path / "batch-root" / batch_id / "m6_review_package"

    assert client.get(endpoint).status_code == 409
    assert client.post(endpoint).status_code == 409
    assert not target.exists()

    assert client.post(f"/api/batches/{batch_id}/apply").status_code == 202
    assert client.get(f"/api/batches/{batch_id}").json()["status"] == "COMPLETED"

    plan = client.get(endpoint)
    assert plan.status_code == 200, plan.text
    assert plan.json()["prepared"] is False
    assert plan.json()["expected_review_rows"] == 4
    assert not target.exists()

    response = client.post(endpoint)
    assert response.status_code == 201, response.text
    result = response.json()
    assert result["prepared"] is True
    assert result["batch_sku_count"] == 2
    assert result["review_rows_created"] == 4
    assert result["missing_output_count"] == 0
    assert result["human_review_performed"] is False
    assert result["ocr_inference_performed"] is False
    assert result["paid_model_calls"] == 0
    assert result["relative_directory"] == f"{batch_id}/m6_review_package"
    assert str(tmp_path) not in response.text

    with (target / "m6_output_reviews.csv").open(encoding="utf-8") as handle:
        reviews = list(csv.DictReader(handle))
    assert {row["variant_id"] for row in reviews} == {
        "square", "feed_4x5_native",
    }
    assert len(reviews) == 4
    assert all(row["overall_accept"] == "UNVERIFIED" for row in reviews)
    assert all(not row["reviewed_at_utc"] for row in reviews)
    assert all((target / row["output_image_path"]).is_file() for row in reviews)

    previously_saved = (target / "m6_output_reviews.csv").read_bytes()
    again = client.post(endpoint)
    assert again.status_code == 409
    assert (target / "m6_output_reviews.csv").read_bytes() == previously_saved


def test_web_m6_package_rejects_tampered_origin_and_symlink_output(
    tmp_path, monkeypatch,
):
    uploaded = _upload_batch(monkeypatch, tmp_path)
    batch_id = uploaded.json()["batch_id"]
    assert client.post(f"/api/batches/{batch_id}/apply").status_code == 202
    assert client.get(f"/api/batches/{batch_id}").json()["status"] == "COMPLETED"
    endpoint = f"/api/batches/{batch_id}/m6-package"
    batch_dir = tmp_path / "batch-root" / batch_id
    journal_path = batch_dir / "batch_import_results.json"
    original = journal_path.read_bytes()
    data = json.loads(original)
    data["rows"].pop("sku-two")
    journal_path.write_text(json.dumps(data), encoding="utf-8")
    assert client.post(endpoint).status_code == 409
    assert not (batch_dir / "m6_review_package").exists()

    journal_path.write_bytes(original)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "private.txt").write_text("untouched", encoding="utf-8")
    (batch_dir / "m6_review_package").symlink_to(outside, target_is_directory=True)
    assert client.post(endpoint).status_code == 409
    assert (outside / "private.txt").read_text(encoding="utf-8") == "untouched"


def test_web_m6_package_ui_is_explicit_and_restores_completed_batch():
    page = client.get("/")
    assert page.status_code == 200
    for marker in (
        'id="prepareM6Package"', 'id="m6PackageStatus"',
        "/m6-package", "relative_directory",
    ):
        assert marker in page.text


def test_web_m6_review_package_zip_download_includes_only_expected_files(
    tmp_path, monkeypatch,
):
    import hashlib
    import zipfile

    uploaded = _upload_batch(monkeypatch, tmp_path)
    batch_id = uploaded.json()["batch_id"]
    assert client.post(f"/api/batches/{batch_id}/apply").status_code == 202
    assert client.post(f"/api/batches/{batch_id}/m6-package").status_code == 201
    folder = tmp_path / "batch-root" / batch_id / "m6_review_package"
    (folder / "private-notes.txt").write_text("never export me", encoding="utf-8")
    response = client.get(f"/api/batches/{batch_id}/m6-package/download")
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/zip"
    assert "attachment" in response.headers["content-disposition"]
    assert str(tmp_path) not in str(response.headers)
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        names = set(archive.namelist())
        assert names == {
            "m6_preparation.json", "m6_cohort_registry.csv", "m6_output_reviews.csv",
            "images/sku-one-square.png", "images/sku-one-native-4x5.png",
            "images/sku-two-square.png", "images/sku-two-native-4x5.png",
        }
        assert all(not n.startswith("/") and ".." not in n.split("/") for n in names)
        assert archive.read("m6_output_reviews.csv") == (
            folder / "m6_output_reviews.csv"
        ).read_bytes()
        with io.StringIO(archive.read("m6_output_reviews.csv").decode("utf-8")) as handle:
            rows = list(csv.DictReader(handle))
        for row in rows:
            payload = archive.read(row["output_image_path"])
            assert hashlib.sha256(payload).hexdigest() == row["output_image_sha256"]
    assert b"never export me" not in response.content
    assert client.get(f"/api/batches/{batch_id}/m6-package/download").status_code == 200


def test_web_m6_zip_refuses_unprepared_batch_without_creating_files(
    tmp_path, monkeypatch,
):
    uploaded = _upload_batch(monkeypatch, tmp_path)
    batch_id = uploaded.json()["batch_id"]
    endpoint = f"/api/batches/{batch_id}/m6-package/download"
    assert client.get(endpoint).status_code == 409
    assert client.post(f"/api/batches/{batch_id}/apply").status_code == 202
    assert client.get(endpoint).status_code == 409
    assert not (tmp_path / "batch-root" / batch_id / "m6_review_package").exists()


def test_web_m6_zip_refuses_tampered_png_without_leaking_file(
    tmp_path, monkeypatch,
):
    uploaded = _upload_batch(monkeypatch, tmp_path)
    batch_id = uploaded.json()["batch_id"]
    assert client.post(f"/api/batches/{batch_id}/apply").status_code == 202
    assert client.post(f"/api/batches/{batch_id}/m6-package").status_code == 201
    folder = tmp_path / "batch-root" / batch_id / "m6_review_package"
    picture = folder / "images/sku-one-square.png"
    saved = picture.read_bytes()
    picture.write_bytes(b"tampered")
    assert client.get(f"/api/batches/{batch_id}/m6-package/download").status_code == 409
    picture.write_bytes(saved)
    assert client.get(f"/api/batches/{batch_id}/m6-package/download").status_code == 200


def test_web_m6_zip_rejects_symlink_and_csv_path_traversal(
    tmp_path, monkeypatch,
):
    uploaded = _upload_batch(monkeypatch, tmp_path)
    batch_id = uploaded.json()["batch_id"]
    assert client.post(f"/api/batches/{batch_id}/apply").status_code == 202
    assert client.post(f"/api/batches/{batch_id}/m6-package").status_code == 201
    folder = tmp_path / "batch-root" / batch_id / "m6_review_package"
    output = folder / "images/sku-one-square.png"
    original = output.read_bytes()
    outside = tmp_path / "outside.png"
    outside.write_bytes(original)
    output.unlink()
    output.symlink_to(outside)
    endpoint = f"/api/batches/{batch_id}/m6-package/download"
    assert client.get(endpoint).status_code == 409
    output.unlink()
    output.write_bytes(original)

    csv_path = folder / "m6_output_reviews.csv"
    original_csv = csv_path.read_bytes()
    csv_path.write_bytes(original_csv.replace(
        b"images/sku-one-square.png", b"../outside.png"
    ))
    assert client.get(endpoint).status_code == 409
    csv_path.write_bytes(original_csv)


def test_web_m6_zip_ui_download_is_available_only_for_prepared_batch():
    page = client.get("/")
    assert page.status_code == 200
    html = page.text
    assert 'id="downloadM6Package"' in html
    assert "/m6-package/download" in html
    assert "$('downloadM6Package').disabled=!result.prepared" in html


def _seed_m6_ocr_capture_for_web(package):
    """Synthetic declaration fixture. It is NOT a real OCR execution record."""
    import hashlib

    reviews_raw = (package / "m6_output_reviews.csv").read_bytes()
    rows = list(csv.DictReader(io.StringIO(reviews_raw.decode("utf-8"))))
    observations = []
    for row in rows:
        observations.append({
            "sku_id": row["sku_id"],
            "variant_id": row["variant_id"],
            "poster_id": row["poster_id"],
            "image_sha256": row["output_image_sha256"],
            "recognized_line_count": 2,
            "recognized_lines": [
                {"text": "¥129", "confidence": 0.95, "bbox": [65, 668, 200, 724]},
                {"text": "Headline", "confidence": 0.82, "bbox": None},
            ],
            "status": "CAPTURED_REVIEW_REQUIRED",
        })
    document = {
        "schema_version": "m6-paddle-ocr-evidence-1",
        "cohort_id": rows[0]["cohort_id"],
        "reviews_csv_sha256": hashlib.sha256(reviews_raw).hexdigest(),
        "sku_count": len(rows) // 2,
        "expected_variant_count": len(rows),
        "captured_variant_count": len(rows),
        "real_ocr_inference_attempted": True,  # fixture-only assertion
        "paddleocr_version": "3.3.0",
        "paddlepaddle_version": "3.2.0",
        "model_digests": {
            "detection": {"sha256": "a" * 64, "file_count": 1, "total_bytes": 4},
            "recognition": {"sha256": "b" * 64, "file_count": 1, "total_bytes": 4},
        },
        "captured_at_utc": "2026-10-26T00:00:00+00:00",
        "elapsed_seconds": 1.2,
        "human_review_performed": False,
        "automatically_accepted": False,
        "source_authorizations_verified": False,
        "min_20_real_skus_proven": False,
        "positioned_line_count": len(rows),
        "unlocated_line_count": len(rows),
        "observations": observations,
    }
    path = package / "m6_paddle_ocr_evidence.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def test_web_m6_ocr_overlay_full_user_flow_is_explicit_and_non_destructive(
    tmp_path, monkeypatch,
):
    import hashlib
    import zipfile

    batch_id = _upload_batch(monkeypatch, tmp_path).json()["batch_id"]
    assert client.post(f"/api/batches/{batch_id}/apply").status_code == 202
    assert client.post(f"/api/batches/{batch_id}/m6-package").status_code == 201
    package = tmp_path / "batch-root" / batch_id / "m6_review_package"
    before = {
        "reviews": (package / "m6_output_reviews.csv").read_bytes(),
        "image": (package / "images/sku-one-square.png").read_bytes(),
    }
    status_url = f"/api/batches/{batch_id}/m6-overlay"
    status = client.get(status_url)
    assert status.status_code == 200
    assert status.json()["ocr_capture_present"] is False
    assert status.json()["overlay_prepared"] is False
    assert status.json()["automatically_accepted"] is False
    assert client.post(status_url).status_code == 409
    assert client.get(status_url + "/download").status_code == 409

    capture = _seed_m6_ocr_capture_for_web(package)
    assert client.get(status_url).json()["ocr_capture_present"] is True
    (package / "extra-private.txt").write_text("do not disclose", encoding="utf-8")
    created = client.post(status_url)
    assert created.status_code == 201, created.text
    assert created.json()["overlay_count"] == 4
    assert created.json()["positioned_line_count"] == 4
    assert created.json()["unlocated_line_count"] == 4
    assert created.json()["automatically_accepted"] is False
    assert client.get(status_url).json()["overlay_prepared"] is True

    download = client.get(status_url + "/download")
    assert download.status_code == 200, download.text
    assert download.headers["content-type"] == "application/zip"
    assert "attachment" in download.headers["content-disposition"]
    assert "no-store" in download.headers["cache-control"]
    assert str(tmp_path) not in str(download.headers)
    with zipfile.ZipFile(io.BytesIO(download.content)) as archive:
        names = set(archive.namelist())
        assert names == {
            "m6_ocr_overlay_manifest.json",
            "images/sku-one-square.png", "images/sku-one-native-4x5.png",
            "images/sku-two-square.png", "images/sku-two-native-4x5.png",
        }
        manifest = json.loads(archive.read("m6_ocr_overlay_manifest.json"))
        assert manifest["ocr_capture_sha256"] == hashlib.sha256(capture.read_bytes()).hexdigest()
        for row in manifest["items"]:
            assert hashlib.sha256(archive.read(row["overlay_path"])).hexdigest() == row["overlay_sha256"]
    assert b"do not disclose" not in download.content
    assert (package / "m6_output_reviews.csv").read_bytes() == before["reviews"]
    assert (package / "images/sku-one-square.png").read_bytes() == before["image"]
    assert client.post(status_url).status_code == 409
    assert client.get(status_url + "/download").status_code == 200


def test_web_m6_ocr_overlay_blocks_stale_capture_and_never_leaks_archive(
    tmp_path, monkeypatch,
):
    batch_id = _upload_batch(monkeypatch, tmp_path).json()["batch_id"]
    assert client.post(f"/api/batches/{batch_id}/apply").status_code == 202
    assert client.post(f"/api/batches/{batch_id}/m6-package").status_code == 201
    package = tmp_path / "batch-root" / batch_id / "m6_review_package"
    ocr = _seed_m6_ocr_capture_for_web(package)
    endpoint = f"/api/batches/{batch_id}/m6-overlay"
    assert client.post(endpoint).status_code == 201
    original = ocr.read_bytes()
    ocr.write_bytes(original.replace(b"3.3.0", b"3.3.1"))
    assert client.get(endpoint + "/download").status_code == 409
    ocr.write_bytes(original)
    assert client.get(endpoint + "/download").status_code == 200

    out = package / "m6_ocr_review_overlays.zip"
    backed_up = out.read_bytes()
    out.unlink()
    outside = tmp_path / "other-auditor.zip"
    outside.write_bytes(backed_up)
    out.symlink_to(outside)
    assert client.get(endpoint + "/download").status_code == 409
    assert client.post(endpoint).status_code == 409
    assert outside.read_bytes() == backed_up


def test_web_m6_ocr_overlay_refuses_forged_or_unfinished_batches(
    tmp_path, monkeypatch,
):
    batch_id = _upload_batch(monkeypatch, tmp_path).json()["batch_id"]
    assert client.get(f"/api/batches/{batch_id}/m6-overlay").status_code == 409
    assert client.post(f"/api/batches/{batch_id}/m6-overlay").status_code == 409
    assert client.get(f"/api/batches/{batch_id}/m6-overlay/download").status_code == 409
    assert client.get("/api/batches/../../m6-overlay").status_code in (404, 422)


def test_web_m6_overlay_ui_is_offline_and_disabled_by_default():
    page = client.get("/")
    assert page.status_code == 200
    for marker in (
        'id="prepareM6Overlay"',
        'id="downloadM6Overlay"',
        'id="m6OverlayStatus"',
        "/m6-overlay",
        "ocr_capture_present",
        "overlay_prepared",
    ):
        assert marker in page.text


def test_web_m6_sku_ocr_review_pairs_readonly_labels_and_text(tmp_path, monkeypatch):
    """A synthetic declaration is enough to exercise UI transport, not real OCR."""
    import hashlib

    batch_id = _upload_batch(monkeypatch, tmp_path).json()["batch_id"]
    assert client.post(f"/api/batches/{batch_id}/apply").status_code == 202
    assert client.post(f"/api/batches/{batch_id}/m6-package").status_code == 201
    package = tmp_path / "batch-root" / batch_id / "m6_review_package"
    endpoint = f"/api/batches/{batch_id}/m6-ocr-review"
    assert client.get(endpoint).status_code == 409
    capture = _seed_m6_ocr_capture_for_web(package)
    reviews_before = (package / "m6_output_reviews.csv").read_bytes()
    capture_before = capture.read_bytes()

    response = client.get(endpoint, params={"sku_id": "sku-two"})
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["sku_ids"] == ["sku-one", "sku-two"]
    assert data["selected_sku_id"] == "sku-two"
    assert data["ocr_capture_consistency"] == "MATCHED_DECLARED_REAL"
    assert data["ocr_capture_sha256"] == hashlib.sha256(capture_before).hexdigest()
    assert data["reviews_csv_sha256"] == hashlib.sha256(reviews_before).hexdigest()
    assert data["automatically_accepted"] is False
    assert data["runtime_attested"] is False
    assert data["quality_verified"] is False
    assert len(data["variants"]) == 2
    assert {v["variant_id"] for v in data["variants"]} == {
        "square", "feed_4x5_native",
    }
    for variant in data["variants"]:
        assert variant["review_labels"]["overall_accept"] == "UNVERIFIED"
        assert variant["review_labels"]["price"] == "UNVERIFIED"
        assert variant["recognized_line_count"] == 2
        assert variant["preview_line_count"] == 2
        assert variant["lines_truncated_count"] == 0
        assert variant["lines"][0] == {
            "text": "¥129", "confidence": 0.95, "bbox": [65, 668, 200, 724],
        }
        assert variant["lines"][1]["bbox"] is None
        assert len(variant["image_sha256"]) == 64
        assert "image_path" not in variant
    assert (package / "m6_output_reviews.csv").read_bytes() == reviews_before
    assert capture.read_bytes() == capture_before


def test_web_m6_sku_ocr_review_rejects_bad_selection_or_stale_artifacts(
    tmp_path, monkeypatch,
):
    batch_id = _upload_batch(monkeypatch, tmp_path).json()["batch_id"]
    endpoint = f"/api/batches/{batch_id}/m6-ocr-review"
    assert client.get(endpoint).status_code == 409
    assert client.post(f"/api/batches/{batch_id}/apply").status_code == 202
    assert client.get(endpoint).status_code == 409
    assert client.post(f"/api/batches/{batch_id}/m6-package").status_code == 201
    package = tmp_path / "batch-root" / batch_id / "m6_review_package"
    capture = _seed_m6_ocr_capture_for_web(package)
    assert client.get(endpoint, params={"sku_id": "unknown-sku"}).status_code == 404
    assert client.get(endpoint).status_code == 200
    csv_path = package / "m6_output_reviews.csv"
    original_csv = csv_path.read_bytes()
    csv_path.write_bytes(original_csv.replace(b"UNVERIFIED", b"FAIL", 1))
    assert client.get(endpoint).status_code == 409
    csv_path.write_bytes(original_csv)

    image_path = package / "images/sku-one-square.png"
    image_bytes = image_path.read_bytes()
    image_path.write_bytes(b"bad image")
    assert client.get(endpoint, params={"sku_id": "sku-one"}).status_code == 409
    image_path.write_bytes(image_bytes)
    assert client.get(endpoint, params={"sku_id": "sku-one"}).status_code == 200

    previous = capture.read_bytes()
    capture.write_bytes(previous.replace(b"3.3.0", b"test-double"))
    assert client.get(endpoint).status_code == 409


def test_web_m6_sku_ocr_review_preview_bounded_and_untrusted_text_is_safe(
    tmp_path, monkeypatch,
):
    batch_id = _upload_batch(monkeypatch, tmp_path).json()["batch_id"]
    assert client.post(f"/api/batches/{batch_id}/apply").status_code == 202
    assert client.post(f"/api/batches/{batch_id}/m6-package").status_code == 201
    package = tmp_path / "batch-root" / batch_id / "m6_review_package"
    capture = _seed_m6_ocr_capture_for_web(package)
    data = json.loads(capture.read_text(encoding="utf-8"))
    lines = [{"text": "<img src=x onerror=alert(1)>" + "字" * 500,
              "confidence": 0.77, "bbox": None} for _ in range(60)]
    for observation in data["observations"]:
        observation["recognized_lines"] = lines
        observation["recognized_line_count"] = len(lines)
    data["positioned_line_count"] = 0
    data["unlocated_line_count"] = 4 * len(lines)
    capture.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    res = client.get(f"/api/batches/{batch_id}/m6-ocr-review")
    assert res.status_code == 200, res.text
    for variant in res.json()["variants"]:
        assert variant["recognized_line_count"] == 60
        assert variant["preview_line_count"] <= 20
        assert variant["lines_truncated_count"] >= 40
        assert len(variant["lines"][0]["text"]) <= 160
    page = client.get("/")
    assert page.status_code == 200
    assert 'id="inspectM6OCR"' in page.text
    assert 'id="m6OcrSkuPicker"' in page.text
    assert 'id="m6OcrReviewRows"' in page.text
    assert "/m6-ocr-review" in page.text
    assert "textContent=line.text" in page.text


def test_web_m6_sku_image_preview_serves_only_verified_frozen_images(tmp_path, monkeypatch):
    """Synthetic OCR declaration tests transport integrity, not real model quality."""
    import hashlib

    batch_id = _upload_batch(monkeypatch, tmp_path).json()["batch_id"]
    endpoint = f"/api/batches/{batch_id}/m6-ocr-review"
    assert client.get(endpoint + "/sku-one/image/square").status_code == 409
    assert client.post(f"/api/batches/{batch_id}/apply").status_code == 202
    assert client.post(f"/api/batches/{batch_id}/m6-package").status_code == 201
    package = tmp_path / "batch-root" / batch_id / "m6_review_package"
    assert client.get(endpoint + "/sku-one/image/square").status_code == 409
    capture = _seed_m6_ocr_capture_for_web(package)
    before_csv = (package / "m6_output_reviews.csv").read_bytes()
    before_capture = capture.read_bytes()

    for variant, suffix in (("square", "square"), ("feed_4x5_native", "native-4x5")):
        image_path = package / f"images/sku-two-{suffix}.png"
        original = image_path.read_bytes()
        result = client.get(endpoint + f"/sku-two/image/{variant}")
        assert result.status_code == 200, result.text
        assert result.headers["content-type"] == "image/png"
        assert "no-store" in result.headers["cache-control"]
        assert result.headers["x-content-type-options"] == "nosniff"
        assert result.content == original
        assert hashlib.sha256(result.content).hexdigest() == hashlib.sha256(original).hexdigest()

    assert client.get(endpoint + "/not-in-cohort/image/square").status_code == 404
    assert client.get(endpoint + "/sku-one/image/thumbnail").status_code == 404
    assert (package / "m6_output_reviews.csv").read_bytes() == before_csv
    assert capture.read_bytes() == before_capture


def test_web_m6_sku_image_preview_fails_closed_on_stale_or_linked_input(
    tmp_path, monkeypatch,
):
    batch_id = _upload_batch(monkeypatch, tmp_path).json()["batch_id"]
    assert client.post(f"/api/batches/{batch_id}/apply").status_code == 202
    assert client.post(f"/api/batches/{batch_id}/m6-package").status_code == 201
    package = tmp_path / "batch-root" / batch_id / "m6_review_package"
    capture = _seed_m6_ocr_capture_for_web(package)
    endpoint = f"/api/batches/{batch_id}/m6-ocr-review/sku-one/image/square"
    assert client.get(endpoint).status_code == 200

    image = package / "images/sku-one-square.png"
    original = image.read_bytes()
    image.write_bytes(b"changed after frozen review")
    assert client.get(endpoint).status_code == 409
    image.write_bytes(original)
    outside = tmp_path / "unrelated.png"
    outside.write_bytes(original)
    image.unlink()
    image.symlink_to(outside)
    assert client.get(endpoint).status_code == 409
    image.unlink()
    image.write_bytes(original)

    csv_path = package / "m6_output_reviews.csv"
    original_csv = csv_path.read_bytes()
    csv_path.write_bytes(original_csv.replace(b"UNVERIFIED", b"FAIL", 1))
    assert client.get(endpoint).status_code == 409
    csv_path.write_bytes(original_csv)

    ocr_original = capture.read_bytes()
    capture.write_bytes(ocr_original.replace(b"3.3.0", b"test-double"))
    assert client.get(endpoint).status_code == 409
    capture.write_bytes(ocr_original)
    assert client.get(endpoint).status_code == 200


def test_web_m6_sku_review_renders_verified_images_without_using_ocr_as_approval():
    page = client.get("/")
    assert page.status_code == 200
    assert "/m6-ocr-review/" in page.text
    assert "/image/" in page.text
    assert "m6-ocr-evidence-image" in page.text
    assert "picture.src='/api/batches/'" in page.text
    assert "不代表已通过质量验收" in page.text


def _m6_review_sidecar_request(package, *, sku="sku-one"):
    import hashlib
    raw = (package / "m6_output_reviews.csv").read_bytes()
    ocr = (package / "m6_paddle_ocr_evidence.json").read_bytes()
    checks = {
        field: "PASS" for field in (
            "sku_integrity", "text_legibility", "title", "subtitle", "price",
            "original_price", "price_position", "original_price_position",
            "cta", "composition",
        )
    }
    return {
        "reviews_csv_sha256": hashlib.sha256(raw).hexdigest(),
        "ocr_capture_sha256": hashlib.sha256(ocr).hexdigest(),
        "decisions": [
            {
                "sku_id": sku, "variant_id": variant, "reviewer_alias": "reviewer-1",
                "checks": checks.copy(), "overall_accept": "PASS", "overall_reason": "",
            }
            for variant in ("square", "feed_4x5_native")
        ],
    }


def test_m6_sidecar_export_checks_frozen_images_and_never_writes_source(
    tmp_path, monkeypatch,
):
    import hashlib

    batch_id = _upload_batch(monkeypatch, tmp_path).json()["batch_id"]
    endpoint = f"/api/batches/{batch_id}/m6-review-sidecar"
    assert client.post(endpoint, json={"decisions": []}).status_code == 409
    assert client.post(f"/api/batches/{batch_id}/apply").status_code == 202
    assert client.post(f"/api/batches/{batch_id}/m6-package").status_code == 201
    package = tmp_path / "batch-root" / batch_id / "m6_review_package"
    # A malformed request is 422 even while OCR evidence is absent.
    assert client.post(endpoint, json={"decisions": []}).status_code == 422
    _seed_m6_ocr_capture_for_web(package)
    request = _m6_review_sidecar_request(package)
    before = {
        filename: (package / filename).read_bytes()
        for filename in ("m6_output_reviews.csv", "m6_paddle_ocr_evidence.json",
                         "images/sku-one-square.png")
    }
    response = client.post(endpoint, json=request)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["schema_version"] == "m6-human-review-sidecar-1"
    assert result["reviews_csv_sha256"] == request["reviews_csv_sha256"]
    assert result["ocr_capture_sha256"] == request["ocr_capture_sha256"]
    assert result["decision_count"] == 2
    assert result["human_review_verified"] is False
    assert result["automatically_accepted"] is False
    assert result["quality_verified"] is False
    assert result["runtime_attested"] is False
    assert result["status"] == "OPERATOR_ASSERTED_NOT_ATTESTED"
    assert len(result["decisions"]) == 2
    for row in result["decisions"]:
        assert row["manual_overall_label"] == "PASS"
        assert len(row["image_sha256"]) == 64
        assert row["reviewer_alias"] == "reviewer-1"
        assert row["reviewed_at_utc"] is None
        assert "output_image_path" not in row
    assert all((package / name).read_bytes() == payload for name, payload in before.items())
    assert not (package / "m6_review_decisions").exists()


def test_m6_sidecar_export_rejects_invalid_or_unproven_human_labels(tmp_path, monkeypatch):
    batch_id = _upload_batch(monkeypatch, tmp_path).json()["batch_id"]
    assert client.post(f"/api/batches/{batch_id}/apply").status_code == 202
    assert client.post(f"/api/batches/{batch_id}/m6-package").status_code == 201
    package = tmp_path / "batch-root" / batch_id / "m6_review_package"
    _seed_m6_ocr_capture_for_web(package)
    endpoint = f"/api/batches/{batch_id}/m6-review-sidecar"
    good = _m6_review_sidecar_request(package)
    assert client.post(endpoint, json=good).status_code == 200
    from copy import deepcopy

    for label in ("UNVERIFIED", "APPROVED", "INDETERMINATE"):
        payload = deepcopy(good)
        payload["decisions"][0]["checks"]["title"] = label
        assert client.post(endpoint, json=payload).status_code == 422
    payload = deepcopy(good)
    payload["decisions"][0]["checks"]["price"] = "FAIL"
    assert client.post(endpoint, json=payload).status_code == 422
    payload["decisions"][0]["overall_accept"] = "FAIL"
    payload["decisions"][0]["overall_reason"] = "Price visibly incorrect"
    assert client.post(endpoint, json=payload).status_code == 200
    payload = deepcopy(good)
    payload["decisions"][0]["overall_accept"] = "FAIL"
    assert client.post(endpoint, json=payload).status_code == 422
    payload = deepcopy(good)
    payload["decisions"].append(deepcopy(payload["decisions"][0]))
    assert client.post(endpoint, json=payload).status_code == 422
    payload = deepcopy(good)
    payload["decisions"][0]["sku_id"] = "not-in-cohort"
    assert client.post(endpoint, json=payload).status_code == 422
    payload = deepcopy(good)
    payload["reviews_csv_sha256"] = "0" * 64
    assert client.post(endpoint, json=payload).status_code == 409
    payload = deepcopy(good)
    payload["ocr_capture_sha256"] = "0" * 64
    assert client.post(endpoint, json=payload).status_code == 409
    payload = deepcopy(good)
    payload["decisions"][0]["reviewer_alias"] = "<script>"
    assert client.post(endpoint, json=payload).status_code == 422


def test_m6_sidecar_export_rejects_changed_ocr_csv_or_png(tmp_path, monkeypatch):
    batch_id = _upload_batch(monkeypatch, tmp_path).json()["batch_id"]
    assert client.post(f"/api/batches/{batch_id}/apply").status_code == 202
    assert client.post(f"/api/batches/{batch_id}/m6-package").status_code == 201
    package = tmp_path / "batch-root" / batch_id / "m6_review_package"
    _seed_m6_ocr_capture_for_web(package)
    endpoint = f"/api/batches/{batch_id}/m6-review-sidecar"
    good = _m6_review_sidecar_request(package)
    for filename in ("m6_output_reviews.csv", "m6_paddle_ocr_evidence.json",
                     "images/sku-one-square.png"):
        file = package / filename
        saved = file.read_bytes()
        file.write_bytes(b"stale")
        assert client.post(endpoint, json=good).status_code == 409
        file.write_bytes(saved)
    assert client.post(endpoint, json=good).status_code == 200


def test_m6_sidecar_workbench_exports_draft_with_explicit_operator_action():
    page = client.get("/")
    assert page.status_code == 200
    for item in (
        'id="m6ReviewSaveSku"', 'id="m6ReviewExportSidecar"',
        "/m6-review-sidecar", "m6ReviewDrafts", "OPERATOR_ASSERTED_NOT_ATTESTED",
    ):
        assert item in page.text
