from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.designs.commands import CommandBatch
from app.designs.errors import InvalidCommandError
from app.designs.repository import DesignRepository
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


def test_library_pagination_preserves_ties_and_current_heads(tmp_path, product_bytes, monkeypatch):
    repo = DesignRepository(tmp_path / "designs.sqlite3", tmp_path / "assets")
    monkeypatch.setattr("app.designs.repository._now", lambda: "2026-11-15T01:00:00+00:00")
    documents = [
        repo.create(title=f"设计{i}", price=Decimal("99"), product_bytes=product_bytes)
        for i in range(5)
    ]
    expected = sorted([doc.document_id for doc in documents], reverse=True)
    first = repo.list_designs(limit=2)
    assert [item["document_id"] for item in first["designs"]] == expected[:2]
    target = expected[2]
    repo.execute(
        target,
        CommandBatch.model_validate(
            {
                "command_id": str(uuid4()),
                "actor": "user",
                "expected_revision": 1,
                "operations": [{"op": "set_text", "node_id": "title", "text": "更新的标题"}],
            }
        ),
    )
    monkeypatch.setattr("app.designs.repository._now", lambda: "2026-11-15T02:00:00+00:00")
    newer = repo.create(title="新设计", price=Decimal("99"), product_bytes=product_bytes)
    reopened = DesignRepository(repo.db_path, repo.asset_dir)
    second = reopened.list_designs(limit=2, cursor=first["next_cursor"])
    assert [item["document_id"] for item in second["designs"]] == expected[2:4]
    assert second["designs"][0]["title"] == "更新的标题"
    assert second["designs"][0]["revision"] == 2
    last = repo.list_designs(limit=2, cursor=second["next_cursor"])
    assert [item["document_id"] for item in last["designs"]] == expected[4:]
    assert last["next_cursor"] is None
    assert repo.list_designs()["designs"][0]["document_id"] == newer.document_id
    # Listing summaries must not read image files.
    for path in repo.asset_dir.glob("*.bin"):
        path.unlink()
    assert len(repo.list_designs()["designs"]) == 6


@pytest.mark.parametrize(
    "kwargs",
    [{"limit": 0}, {"limit": 51}, {"limit": True}, {"cursor": "../x"}, {"cursor": "missing"}],
)
def test_library_rejects_invalid_page(tmp_path, kwargs):
    repo = DesignRepository(tmp_path / "designs.sqlite3", tmp_path / "assets")
    with pytest.raises(InvalidCommandError):
        repo.list_designs(**kwargs)


def test_library_http_contract(client, product_bytes):
    assert client.get("/api/designs").json() == {"designs": [], "next_cursor": None}
    created = client.post(
        "/api/designs",
        data={"title": "保存在服务端", "price": "99"},
        files={"product": ("product.png", product_bytes, "image/png")},
    ).json()["document"]
    response = client.get("/api/designs?limit=1")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    item = response.json()["designs"][0]
    assert item["document_id"] == created["document_id"]
    assert item["revision"] == 1 and item["title"] == "保存在服务端"
    assert client.get(item["canvas_url"]).status_code == 200
    for query in ["limit=51", "limit=0", "cursor=missing", "cursor=../private"]:
        assert client.get("/api/designs?" + query).status_code == 422
    assert (
        client.get("/api/designs", headers={"Origin": "https://example.invalid"}).status_code == 403
    )
