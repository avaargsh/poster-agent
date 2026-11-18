"""Read-only, uncalibrated layout hints for journaled OCR boxes.

This tests deterministic geometry only, not real OCR inference, true glyph
extent, a calibrated zone classifier, human approval or release permission.
"""

from decimal import Decimal

from app.designs.ocr_layout_hints import assess_design_node_hints
from app.designs.templates import create_template


def _design():
    return create_template(
        title="双十一牙刷", price=Decimal("199.90"), product_asset_id="sku_1", cta="立即抢购"
    )


def _checks(*, price_box=None, price_status="BOX_PRESENT_UNCALIBRATED"):
    return [
        {"field": "title", "status": "BOX_PRESENT_UNCALIBRATED", "bbox": [80, 110, 220, 145]},
        {
            "field": "price",
            "status": price_status,
            "bbox": [80, 780, 200, 840] if price_box is None else price_box,
        },
        {"field": "cta", "status": "BOX_PRESENT_UNCALIBRATED", "bbox": [80, 915, 200, 960]},
    ]


def _assess(doc, checks, source="a" * 64):
    return assess_design_node_hints(
        doc, checks, width=1024, height=1024, spatial_source_sha256=source
    )


def test_matching_fields_with_node_overlap_remain_uncalibrated():
    report = _assess(_design(), _checks())
    assert report["design_node_overlap_status"] == "OVERLAP_ONLY_UNCALIBRATED"
    assert report["design_node_overlap_schema_version"] == 1
    assert len(report["design_node_overlap_fingerprint_sha256"]) == 64
    assert [c["field"] for c in report["design_node_overlap_checks"]] == [
        "title", "price", "cta"
    ]
    price = report["design_node_overlap_checks"][1]
    assert price["node_id"] == "price"
    assert price["status"] == "OVERLAP_UNCALIBRATED"
    assert price["overlap_fraction"] == 1.0


def test_bbox_inside_canvas_but_outside_price_node_is_not_positive():
    report = _assess(_design(), _checks(price_box=[550, 370, 600, 420]))
    price = report["design_node_overlap_checks"][1]
    assert report["design_node_overlap_status"] == "BLOCKED"
    assert price["status"] == "OUTSIDE_DESIGN_NODE"
    assert price["overlap_fraction"] == 0.0


def test_partial_intersection_is_diagnostic_not_a_pass():
    report = _assess(_design(), _checks(price_box=[690, 800, 730, 840]))
    price = report["design_node_overlap_checks"][1]
    assert report["design_node_overlap_status"] == "OVERLAP_ONLY_UNCALIBRATED"
    assert price["status"] == "OVERLAP_UNCALIBRATED"
    assert 0 < price["overlap_fraction"] < 1


def test_missing_bbox_blocks_hint():
    checks = _checks()
    checks[1]["status"] = "MISSING_BBOX"
    checks[1]["bbox"] = None
    report = _assess(_design(), checks)
    assert report["design_node_overlap_status"] == "BLOCKED"
    assert report["design_node_overlap_checks"][1]["status"] == "NO_VALID_OCR_BOX"


def test_rotated_node_does_not_use_unrotated_rectangle():
    doc = _design()
    node = next(n for n in doc.nodes if n.role == "price")
    node.rotation = 30
    report = _assess(doc, _checks())
    assert report["design_node_overlap_status"] == "BLOCKED"
    assert report["design_node_overlap_checks"][1]["status"] == "UNSUPPORTED_NODE_GEOMETRY"


def test_hidden_node_does_not_supply_a_hint():
    doc = _design()
    node = next(n for n in doc.nodes if n.role == "price")
    node.visible = False
    report = _assess(doc, _checks())
    assert report["design_node_overlap_status"] == "BLOCKED"
    assert report["design_node_overlap_checks"][1]["status"] == "NO_VISIBLE_TEXT_NODE"


def test_different_source_identity_changes_only_hint_fingerprint():
    before = _assess(_design(), _checks(), "a" * 64)
    after = _assess(_design(), _checks(), "b" * 64)
    assert before["design_node_overlap_status"] == after["design_node_overlap_status"]
    assert (
        before["design_node_overlap_fingerprint_sha256"]
        != after["design_node_overlap_fingerprint_sha256"]
    )


def test_document_page_mismatch_fails_closed():
    doc = _design()
    doc.page.width = 900
    report = _assess(doc, _checks())
    assert report["design_node_overlap_status"] == "BLOCKED"
    assert all(
        c["status"] == "UNSUPPORTED_NODE_GEOMETRY"
        for c in report["design_node_overlap_checks"]
    )
