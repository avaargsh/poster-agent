"""Hero square acceptance must use its own price region and overlay.

Synthetic OCR geometry only; no live model execution or product-quality claim.
"""
from __future__ import annotations

import io

from fastapi.testclient import TestClient
from PIL import Image

from app import storage
from app.main import app
from app.ocr_price_layout import HERO_PRICE_ZONES, PRICE_ZONES, assess_price_zones
from app.schemas import PosterSpec

client = TestClient(app)


def _ocr(text, box):
    return {"text": text, "bbox": box, "score": 0.99}


def test_hero_uses_independent_price_regions_and_rejects_classic_coordinates():
    hero = PosterSpec(title="商品上新", price="199", original_price="399", layout_id="hero")
    good = [
        _ocr("¥199", [90, 846, 290, 912]),
        _ocr("原价 ¥399", [95, 943, 294, 977]),
    ]
    status = {c["name"]: c for c in assess_price_zones(hero, "OK", good)}
    assert status["ocr_price_position"]["status"] == "PASS"
    assert status["ocr_original_price_position"]["status"] == "PASS"
    assert status["ocr_price_position"]["evidence"]["zone_xyxy"] == list(HERO_PRICE_ZONES["price"])
    classic_coord = [_ocr("¥199", [75, 672, 280, 752])]
    status = {c["name"]: c for c in assess_price_zones(hero, "OK", classic_coord)}
    assert status["ocr_price_position"]["status"] == "FAIL"
    assert status["ocr_price_position"]["evidence"]["zone_xyxy"] != list(PRICE_ZONES["price"])


def test_hero_acceptance_overlay_uses_hero_zones_not_classic(tmp_path, monkeypatch, product_bytes):
    monkeypatch.setenv("POSTER_DATA_DIR", str(tmp_path / "posters"))
    created = client.post(
        "/api/posters", files={"product": ("sku.png", product_bytes, "image/png")},
        data={"title": "商品上新", "price": "199", "original_price": "399",
              "layout_id": "hero"},
    )
    assert created.status_code == 201, created.text
    pid = created.json()["poster_id"]
    monkeypatch.setattr("app.acceptance.recognize", lambda _: [
        _ocr("¥199", [90, 846, 290, 912]),
        _ocr("原价 ¥399", [95, 943, 294, 977]),
    ])
    result = client.post(
        f"/api/posters/{pid}/versions/1/acceptance", json={"ocr_mode": "paddle"}
    )
    assert result.status_code == 200, result.text
    report = {c["name"]: c for c in result.json()["checks"]}
    assert report["ocr_price_position"]["status"] == "PASS"
    assert report["ocr_original_price_position"]["status"] == "PASS"

    saved_png = storage.version_path(pid, 1).read_bytes()
    overlay = client.get(
        f"/api/posters/{pid}/versions/1/acceptance/overlay?ocr_mode=paddle"
    )
    assert overlay.status_code == 200, overlay.text
    with Image.open(io.BytesIO(saved_png)) as original, Image.open(
        io.BytesIO(overlay.content)
    ) as diagnostic:
        assert original.size == diagnostic.size == (1024, 1024)
        # The border at (45,812) exists only on the hero price zone.
        assert diagnostic.getpixel((45, 812)) != original.getpixel((45, 812))
    assert storage.version_path(pid, 1).read_bytes() == saved_png


def test_hero_without_ocr_remains_unverified():
    hero = PosterSpec(title="商品上新", price="199", layout_id="hero")
    checks = assess_price_zones(hero, "NOT_RUN", [])
    assert checks[0]["status"] == "UNVERIFIED"
