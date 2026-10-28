"""Bound a reusable layout snapshot to the existing real-SKU batch import pipeline.

Local synthetic SKU images only; no image model, OCR, or real product acceptance.
"""

from __future__ import annotations

import csv
import io
import json

from fastapi.testclient import TestClient
from PIL import Image

from app import storage
from app.main import app
from scripts import real_sku_batch

client = TestClient(app)


def png(color):
    img = Image.new("RGBA", (128, 180), color)
    raw = io.BytesIO()
    img.save(raw, "PNG")
    return raw.getvalue()


def manifest(rows):
    out = io.StringIO(newline="")
    writer = csv.DictWriter(
        out,
        fieldnames=[
            "sku_id",
            "product_image",
            "title",
            "price",
            "original_price",
            "subtitle",
            "cta",
            "style",
        ],
    )
    writer.writeheader()
    writer.writerows(rows)
    return out.getvalue().encode("utf-8-sig")


def rows():
    return [
        dict(
            sku_id="sku-one",
            product_image="one.png",
            title="电动牙刷",
            price="199",
            original_price="399",
            subtitle="质感生活",
            cta="查看商品",
            style="蓝色科技风",
        ),
        dict(
            sku_id="sku-two",
            product_image="two.png",
            title="护肤单品",
            price="89",
            original_price="119",
            subtitle="精选好物",
            cta="立即查看",
            style="粉色浪漫",
        ),
    ]


def source_poster(monkeypatch, tmp_path):
    monkeypatch.setenv("POSTER_DATA_DIR", str(tmp_path / "posters"))
    monkeypatch.setenv("POSTER_EXECUTION_DB", str(tmp_path / "executions.sqlite3"))
    r = client.post(
        "/api/posters",
        files={"product": ("model.png", png((15, 120, 180, 255)), "image/png")},
        data={
            "title": "模板源",
            "price": "199",
            "layout_id": "hero",
            "product_align": "bottom",
            "product_scale": "compact",
            "layer_layout": json.dumps(
                {"schema_version": 1, "title_dx": 12, "price_dy": -6, "cta_dx": 9, "logo_dy": 7}
            ),
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def post_batch(monkeypatch, tmp_path, src, *, version="1", poster_id=None, csv_bytes=None):
    monkeypatch.setenv("POSTER_BATCH_DIR", str(tmp_path / "batches"))
    files = [
        ("manifest", ("skus.csv", csv_bytes or manifest(rows()), "text/csv")),
        ("images", ("one.png", png((50, 90, 210, 255)), "image/png")),
        ("images", ("two.png", png((200, 80, 70, 255)), "image/png")),
    ]
    return client.post(
        "/api/batches",
        files=files,
        data={
            "layout_poster_id": poster_id or src["poster_id"],
            "layout_version": version,
        },
    )


def test_saved_poster_layout_applies_to_every_sku_without_changing_prices(tmp_path, monkeypatch):
    source = source_poster(monkeypatch, tmp_path)
    staged = post_batch(monkeypatch, tmp_path, source)
    assert staged.status_code == 201, staged.text
    batch_id = staged.json()["batch_id"]
    assert staged.json()["status"] == "READY"
    staged_dir = tmp_path / "batches" / batch_id
    with (staged_dir / "manifest.csv").open(encoding="utf-8-sig", newline="") as f:
        out = list(csv.DictReader(f))
    assert len(out) == 2
    assert [row["title"] for row in out] == ["电动牙刷", "护肤单品"]
    assert [row["price"] for row in out] == ["199", "89"]
    assert all(row["layout_id"] == "hero" for row in out)
    assert all(row["product_align"] == "bottom" for row in out)
    assert all(json.loads(row["layer_layout"])["price_dy"] == -6 for row in out)
    plan = json.loads((staged_dir / "batch_preflight.json").read_text())
    assert plan["layout_source"]["poster_id"] == source["poster_id"]
    assert plan["layout_source"]["version"] == 1
    assert len(plan["layout_source"]["spec_sha256"]) == 64

    started = client.post(f"/api/batches/{batch_id}/apply")
    assert started.status_code == 202, started.text
    complete = client.get(f"/api/batches/{batch_id}").json()
    assert complete["status"] == "COMPLETED", complete
    assert complete["processed"] == 2
    ids = [row["poster_id"] for row in complete["rows"]]
    assert len(set(ids)) == 2
    for idx, ident in enumerate(ids):
        spec = storage.view(ident)["spec"]
        assert spec["title"] == rows()[idx]["title"]
        assert spec["price"] == rows()[idx]["price"]
        assert spec["layout_id"] == "hero"
        assert spec["layer_layout"]["price_dy"] == -6
        assert spec["layer_layout"]["title_dx"] == 12
    # Existing idempotent batch replay must keep poster IDs and same template.
    again = client.post(f"/api/batches/{batch_id}/apply")
    assert again.status_code == 202, again.text
    after = client.get(f"/api/batches/{batch_id}").json()
    assert [row["poster_id"] for row in after["rows"]] == ids


def test_batch_snapshots_old_version_even_when_source_poster_changes(tmp_path, monkeypatch):
    source = source_poster(monkeypatch, tmp_path)
    staged = post_batch(monkeypatch, tmp_path, source)
    assert staged.status_code == 201, staged.text
    batch_id = staged.json()["batch_id"]
    revised = client.post(
        f"/api/posters/{source['poster_id']}/revisions",
        json={
            "expected_version": 1,
            "layout_id": "classic",
            "layer_layout": {"schema_version": 1, "price_dx": 10},
        },
    )
    assert revised.status_code == 201, revised.text
    assert client.post(f"/api/batches/{batch_id}/apply").status_code == 202
    complete = client.get(f"/api/batches/{batch_id}").json()
    assert complete["status"] == "COMPLETED"
    for row in complete["rows"]:
        saved = storage.view(row["poster_id"])["spec"]
        assert saved["layout_id"] == "hero"
        assert saved["layer_layout"]["price_dx"] == 0
        assert saved["layer_layout"]["title_dx"] == 12


def test_invalid_or_unpublished_template_fails_before_any_batch_staging(tmp_path, monkeypatch):
    source = source_poster(monkeypatch, tmp_path)
    for version, poster in [
        ("0", None),
        ("2", None),
        ("-1", None),
        ("x", None),
        ("1", "bad-poster-id"),
    ]:
        response = post_batch(monkeypatch, tmp_path, source, version=version, poster_id=poster)
        assert response.status_code in {404, 422}, response.text
    root = tmp_path / "batches"
    assert not root.exists() or list(root.iterdir()) == []


def test_cli_manifest_layout_columns_validate_and_run(tmp_path, monkeypatch):
    monkeypatch.setenv("POSTER_DATA_DIR", str(tmp_path / "posters"))
    monkeypatch.setenv("POSTER_EXECUTION_DB", str(tmp_path / "executions.sqlite3"))
    batch_dir = tmp_path / "items"
    batch_dir.mkdir()
    (batch_dir / "one.png").write_bytes(png((55, 110, 170, 255)))
    row = {
        **rows()[0],
        "product_image": "one.png",
        "layout_id": "hero",
        "product_align": "top",
        "product_scale": "compact",
        "layer_layout": json.dumps({"schema_version": 1, "price_dx": 10}),
    }
    fields = list(row)
    path = batch_dir / "items.csv"
    with path.open("w", newline="", encoding="utf-8") as fp:
        writer = csv.DictWriter(fp, fieldnames=fields)
        writer.writeheader()
        writer.writerow(row)
    _, prepared = real_sku_batch.preflight(path)
    assert prepared[0]["data"]["layout_id"] == "hero"
    assert json.loads(prepared[0]["data"]["layer_layout"])["price_dx"] == 10
    result = real_sku_batch.run_batch(path, tmp_path / "results", apply=True)
    poster = storage.view(result["poster_ids"]["sku-one"])
    assert poster["spec"]["layout_id"] == "hero"
    assert poster["spec"]["layer_layout"]["price_dx"] == 10


def test_manifest_rejects_bad_layout_json_before_generation(tmp_path, monkeypatch):
    monkeypatch.setenv("POSTER_DATA_DIR", str(tmp_path / "posters"))
    batch_dir = tmp_path / "items"
    batch_dir.mkdir()
    (batch_dir / "one.png").write_bytes(png((70, 110, 145, 255)))
    for raw in ('{"price_dx":999}', '{"bad_field":1}', "[0,1]", "{", '{"schema_version":2}'):
        row = {**rows()[0], "layer_layout": raw}
        path = batch_dir / "bad.csv"
        with path.open("w", newline="", encoding="utf-8") as fp:
            writer = csv.DictWriter(fp, fieldnames=list(row))
            writer.writeheader()
            writer.writerow(row)
        try:
            real_sku_batch.preflight(path)
        except real_sku_batch.BatchError:
            pass
        else:
            raise AssertionError("Invalid layout in batch manifest was accepted")
    assert not storage.root().exists()


def test_web_ui_exposes_explicit_saved_layout_batch_apply():
    html = client.get("/").text
    for marker in ('id="batchUseSavedLayout"', 'id="batchTemplateVersion"'):
        assert marker in html
    assert "layout_poster_id" in html
    assert "layout_version" in html
    assert "resetBatchForNewFiles()" in html
