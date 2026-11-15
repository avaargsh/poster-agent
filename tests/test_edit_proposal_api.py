from uuid import uuid4
from xml.etree import ElementTree

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    for key, name in [
        ("POSTER_DATA_DIR", "posters"),
        ("POSTER_EXECUTION_DB", "executions.sqlite3"),
        ("POSTER_CHECKPOINT_DB", "checkpoints.sqlite3"),
        ("POSTER_DESIGN_DB", "designs.sqlite3"),
        ("POSTER_DESIGN_ASSET_DIR", "assets"),
    ]:
        monkeypatch.setenv(key, str(tmp_path / name))
    with TestClient(app) as value:
        yield value


def make(client, product_bytes):
    response = client.post(
        "/api/designs",
        data={"title": "原始标题", "price": "99.90"},
        files={"product": ("product.png", product_bytes, "image/png")},
    )
    assert response.status_code == 201
    return response.json()["document"]


def test_proposal_http_preview_apply_and_replay(client, product_bytes):
    document = make(client, product_bytes)
    url = "/api/designs/" + document["document_id"]
    body = {
        "request_id": str(uuid4()),
        "expected_revision": 1,
        "summary": "放大价格",
        "operations": [{"op": "set_style", "node_id": "price", "font_size": 100}],
    }
    response = client.post(url + "/proposals", json=body)
    assert response.status_code == 201, response.text
    proposal = response.json()
    assert proposal["status"] == "PENDING"
    assert client.get(url).json()["document"]["revision"] == 1
    preview = client.get(proposal["preview_url"])
    assert preview.status_code == 200
    ElementTree.fromstring(preview.content)
    assert "sandbox" in preview.headers["content-security-policy"]
    assert (
        client.get(url + "/proposals").json()["proposals"][0]["proposal_id"]
        == proposal["proposal_id"]
    )
    apply_url = url + "/proposals/" + proposal["proposal_id"] + "/apply"
    result = client.post(apply_url)
    assert result.status_code == 200 and result.json()["document"]["revision"] == 2
    assert client.post(apply_url).json()["replayed"]
    assert client.get(url + "/proposals/" + proposal["proposal_id"]).json()["status"] == "APPLIED"
    assert (
        client.post(
            url + "/proposals", json={**body, "expected_revision": 2, "release_ready": True}
        ).status_code
        == 422
    )
    assert client.post(apply_url, headers={"Origin": "https://example.invalid"}).status_code == 403


def test_proposal_http_isolated_identity_and_stale_application(client, product_bytes):
    first = make(client, product_bytes)
    other = make(client, product_bytes)
    url = "/api/designs/" + first["document_id"]
    body = {
        "request_id": str(uuid4()),
        "expected_revision": 1,
        "summary": "修改标题",
        "operations": [{"op": "set_text", "node_id": "title", "text": "新标题"}],
    }
    proposal = client.post(url + "/proposals", json=body).json()
    assert (
        client.get(
            "/api/designs/" + other["document_id"] + "/proposals/" + proposal["proposal_id"]
        ).status_code
        == 404
    )
    assert client.get(url + "/proposals?limit=1000").status_code == 422
    edit = {
        "command_id": str(uuid4()),
        "actor": "user",
        "expected_revision": 1,
        "operations": [{"op": "transform_node", "node_id": "product", "x": 600}],
    }
    assert client.post(url + "/commands", json=edit).status_code == 200
    rejected = client.post(url + "/proposals/" + proposal["proposal_id"] + "/apply")
    assert rejected.status_code == 409 and rejected.json()["error"]["code"] == "REVISION_CONFLICT"
    assert client.get(url).json()["document"]["revision"] == 2
