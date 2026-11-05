"""Explicit CTA on new DesignDocument snapshots, without changing legacy defaults."""

import uuid

import pytest
from fastapi.testclient import TestClient

import app.designs.routes as design_routes
from app.main import app
from app.schemas import PosterSpec


@pytest.fixture
def design_paths(tmp_path, monkeypatch):
    monkeypatch.setenv("POSTER_DATA_DIR", str(tmp_path / "posters"))
    monkeypatch.setenv("POSTER_EXECUTION_DB", str(tmp_path / "executions.sqlite3"))
    monkeypatch.setenv("POSTER_CHECKPOINT_DB", str(tmp_path / "checkpoints.sqlite3"))
    monkeypatch.setenv("POSTER_DESIGN_DB", str(tmp_path / "designs.sqlite3"))
    monkeypatch.setenv("POSTER_DESIGN_ASSET_DIR", str(tmp_path / "design-assets"))


def create(client, product_bytes, cta=None):
    data = {"title": "双十一牙刷", "price": "199.90"}
    if cta is not None:
        data["cta"] = cta
    return client.post(
        "/api/designs",
        data=data,
        files={"product": ("toothbrush.png", product_bytes, "image/png")},
    )


def approve(client, doc):
    target = client.get(f"/api/designs/{doc}/approval-target").json()
    response = client.post(
        f"/api/designs/{doc}/approvals",
        json={
            "approval_id": "approval-cta-" + uuid.uuid4().hex,
            "expected_revision": target["revision"],
            "spec_digest": target["spec_digest"],
            "asset_manifest_digest": target["asset_manifest_digest"],
        },
    )
    assert response.status_code == 201, response.text


def test_explicit_cta_becomes_rendered_text_and_ocr_target_field(design_paths, product_bytes):
    with TestClient(app) as client:
        created = create(client, product_bytes, cta="立即抢购")
        assert created.status_code == 201, created.text
        doc = created.json()["document"]
        ctanodes = [
            node for node in doc["nodes"]
            if node["node_type"] == "text" and node["role"] == "cta"
        ]
        assert len(ctanodes) == 1
        assert "".join(line["text"] for line in ctanodes[0]["lines"]) == "立即抢购"
        assert ctanodes[0]["x"] >= 0
        assert ctanodes[0]["y"] + ctanodes[0]["height"] <= doc["page"]["height"]
        preview = client.get(f"/api/designs/{doc['document_id']}/revisions/1/preview.svg")
        assert preview.status_code == 200
        assert b"data-node-id=\"cta\"" in preview.content
        assert "立即抢购" in preview.text

        approve(client, doc["document_id"])
        ocr_target = client.get(f"/api/designs/{doc['document_id']}/revisions/1/ocr-target")
        assert ocr_target.status_code == 200, ocr_target.text
        assert ocr_target.json()["expected_fields"] == {
            "title": "双十一牙刷",
            "price": "¥199.90",
            "cta": "立即抢购",
        }
        assert ocr_target.json()["missing_fields"] == []
        assert ocr_target.json()["ocr_status"] == "NOT_RUN"
        assert ocr_target.json()["release_ready"] is False


def test_legacy_default_and_invalid_cta_stay_fail_closed(design_paths, product_bytes):
    with TestClient(app) as client:
        created = create(client, product_bytes)
        assert created.status_code == 201
        doc = created.json()["document"]
        assert not any(node.get("role") == "cta" for node in doc["nodes"])
        approve(client, doc["document_id"])
        ocr_target = client.get(f"/api/designs/{doc['document_id']}/revisions/1/ocr-target")
        assert ocr_target.json()["missing_fields"] == ["cta"]

        too_long = create(client, product_bytes, cta="抢" * 13)
        assert too_long.status_code == 422
        assert too_long.json()["error"]["code"] == "INVALID_COMMAND"
        controls = create(client, product_bytes, cta="立即\n购买")
        assert controls.status_code == 422
        assert controls.json()["error"]["code"] == "INVALID_COMMAND"


def test_brief_link_rejects_mismatching_explicit_cta(design_paths, product_bytes, monkeypatch):
    monkeypatch.setenv("POSTER_BRIEF_LINK_TOKEN", "x" * 48)
    spec = PosterSpec(title="双十一牙刷", price="199.90", cta="立即购买")
    monkeypatch.setattr(
        design_routes,
        "get_run",
        lambda rid: {
            "status": "APPROVED",
            "pending_approval": False,
            "confirmed_spec": spec.model_dump(mode="json"),
        },
    )
    with TestClient(app) as client:
        created = create(client, product_bytes, cta="立即抢购")
        assert created.status_code == 201
        doc = created.json()["document"]["document_id"]
        linked = client.post(
            f"/api/designs/{doc}/brief-link",
            json={"brief_run_id": "a" * 32},
            headers={"Authorization": "Bearer " + "x" * 48},
        )
        assert linked.status_code == 422, linked.text
        assert linked.json()["error"]["code"] == "INVALID_COMMAND"
