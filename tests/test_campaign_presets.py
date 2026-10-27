"""Campaign preset bulk CSV workflow: no hidden price/title substitutions."""
import csv
import io

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def upload(rows, *, columns=None, template="flash-sale"):
    columns = columns or ["sku_id", "product_image", "title", "price",
                           "original_price", "subtitle", "cta", "style", "logo_image"]
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=columns)
    writer.writeheader()
    writer.writerows(rows)
    return client.post(
        "/api/campaign-presets/apply-manifest",
        files={"manifest": ("products.csv", stream.getvalue().encode("utf-8-sig"), "text/csv")},
        data={"template_id": template},
    )


def sample(**changes):
    return {
        "sku_id": "SKU_01", "product_image": "one.png", "title": "电动牙刷",
        "price": "199", "original_price": "399", "subtitle": "",
        "cta": "", "style": "", "logo_image": "", **changes,
    }


def parsed(response):
    return list(csv.DictReader(io.StringIO(response.content.decode("utf-8-sig"))))


def test_catalog_and_fill_blanks_only_no_price_or_title_change():
    catalog = client.get("/api/campaign-presets")
    assert catalog.status_code == 200
    names = {item["id"]: item for item in catalog.json()["items"]}
    assert "flash-sale" in names
    assert "price" not in names["flash-sale"]
    assert "title" not in names["flash-sale"]
    response = upload([sample(), sample(sku_id="SKU_02", title="已有标题",
                                       price="88", subtitle="人工审核文案",
                                       cta="立即查看", style="自定义品牌风")])
    assert response.status_code == 200, response.text
    assert response.headers["x-template-filled-rows"] == "1"
    assert "attachment;" in response.headers["content-disposition"]
    entries = parsed(response)
    assert len(entries) == 2
    assert entries[0]["title"] == "电动牙刷"
    assert entries[0]["price"] == "199"
    assert entries[0]["original_price"] == "399"
    assert {k: entries[0][k] for k in ("style", "subtitle", "cta")} == {
        k: names["flash-sale"][k] for k in ("style", "subtitle", "cta")
    }
    assert entries[1]["title"] == "已有标题"
    assert entries[1]["price"] == "88"
    assert entries[1]["subtitle"] == "人工审核文案"
    assert entries[1]["cta"] == "立即查看"
    assert entries[1]["style"] == "自定义品牌风"


def test_compact_required_only_manifest_is_expanded_but_product_facts_untouched():
    row = {k: sample()[k] for k in ("sku_id", "product_image", "title", "price")}
    response = upload([row], columns=list(row), template="nature")
    assert response.status_code == 200, response.text
    actual = parsed(response)[0]
    assert actual["sku_id"] == row["sku_id"]
    assert actual["product_image"] == row["product_image"]
    assert actual["title"] == row["title"]
    assert actual["price"] == row["price"]
    assert actual["style"] == "绿色自然"
    assert actual["cta"]
    assert actual["subtitle"]


def test_manifest_fail_closed_on_invalid_data_and_cannot_stage_or_run():
    cases = [
        ([sample(title="=WEBSERVICE(\"https://example.com\")")], None),
        ([sample(price="")], None),
        ([sample(sku_id="../bad")], None),
        ([sample(), sample()], None),
        ([sample()], ["sku_id", "product_image", "title", "price", "script"]),
        ([sample() for _ in range(101)], None),
    ]
    for rows, columns in cases:
        if columns:
            rows = [{**{k: v for k, v in row.items() if k in columns}, "script": "echo"} for row in rows]
        response = upload(rows, columns=columns)
        assert response.status_code == 422, response.text
    assert upload([sample()], template="unknown").status_code == 422
    assert client.post("/api/campaign-presets/apply-manifest", files={
        "manifest": ("bad.csv", b"x" * (1024 * 1024 + 1), "text/csv")},
        data={"template_id": "nature"}).status_code == 413


def test_browser_exposes_explicit_download_not_automatic_generation():
    html = client.get("/").text
    assert 'id="campaignPreset"' in html
    assert 'id="applyCampaignPreset"' in html
    assert 'id="campaignPresetStatus"' in html
    assert 'id="createCampaignPreset"' in html
    assert 'id="applySingleCampaignPreset"' in html
    assert "if(activeReviewRunId){" in html
    assert "syncStylePresetPicker();" in html
    assert "/api/campaign-presets/apply-manifest" in html
    assert "不会覆盖价格、主标题或已有文案" in html
