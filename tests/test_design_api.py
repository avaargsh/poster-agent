"""HTTP contract tests for the local design API (Task A2b)."""

from __future__ import annotations

import re
import sqlite3
import uuid
from xml.etree import ElementTree

import pytest
from fastapi.testclient import TestClient

from app.designs.models import MAX_ASSET_BYTES
from app.main import app

DOCUMENT_ID = re.compile(r"^doc_[0-9a-f]{32}$")
SVG_NS = "{http://www.w3.org/2000/svg}"


@pytest.fixture
def design_paths(tmp_path, monkeypatch):
    paths = {
        "design_db": tmp_path / "designs" / "designs.sqlite3",
        "design_assets": tmp_path / "design-assets",
    }
    monkeypatch.setenv("POSTER_DATA_DIR", str(tmp_path / "posters"))
    monkeypatch.setenv("POSTER_EXECUTION_DB", str(tmp_path / "ledger" / "executions.sqlite3"))
    monkeypatch.setenv("POSTER_CHECKPOINT_DB", str(tmp_path / "workflow" / "checkpoints.sqlite3"))
    monkeypatch.setenv("POSTER_DESIGN_DB", str(paths["design_db"]))
    monkeypatch.setenv("POSTER_DESIGN_ASSET_DIR", str(paths["design_assets"]))
    return paths


@pytest.fixture
def client(design_paths):
    with TestClient(app) as test_client:
        yield test_client


def create_design(client, product_bytes, *, title="夏日特惠", price="199.90"):
    return client.post(
        "/api/designs",
        files={"product": ("product.png", product_bytes, "image/png")},
        data={"title": title, "price": price},
    )


def assert_error(response, status_code, code):
    assert response.status_code == status_code, response.text
    assert response.json()["error"]["code"] == code


def command_payload(*, expected_revision, operations, command_id=None, actor="user"):
    return {
        "command_id": command_id or f"cmd-{uuid.uuid4().hex}",
        "expected_revision": expected_revision,
        "actor": actor,
        "operations": operations,
    }


def latest(client, document_id):
    response = client.get(f"/api/designs/{document_id}")
    assert response.status_code == 200, response.text
    return response.json()


def node(document, node_id):
    return next(node for node in document["nodes"] if node["node_id"] == node_id)


def title_text(document):
    return "".join(line["text"] for line in node(document, "title")["lines"])


def price_text(document):
    return "".join(line["text"] for line in node(document, "price")["lines"])


def design_count(database):
    if not database.exists():
        return 0
    with sqlite3.connect(database) as connection:
        return int(connection.execute("SELECT COUNT(*) FROM designs").fetchone()[0])


def test_create_read_and_exact_revision_preview_contract(client, product_bytes):
    created = create_design(client, product_bytes)
    assert created.status_code == 201, created.text
    payload = created.json()
    document = payload["document"]

    assert DOCUMENT_ID.fullmatch(document["document_id"])
    assert document["revision"] == 1
    assert document["facts"]["price"] == "199.90"
    assert title_text(document) == "夏日特惠"
    assert price_text(document) == "¥199.90"
    expected_preview = f"/api/designs/{document['document_id']}/revisions/1/preview.svg"
    assert payload["preview_url"] == expected_preview

    latest_response = client.get(f"/api/designs/{document['document_id']}")
    assert latest_response.status_code == 200
    assert latest_response.json()["document"] == document
    assert latest_response.json()["preview_url"] == expected_preview

    revision_response = client.get(f"/api/designs/{document['document_id']}/revisions/1")
    assert revision_response.status_code == 200
    assert revision_response.json()["document"] == document

    preview = client.get(expected_preview)
    assert preview.status_code == 200
    assert preview.headers["content-type"] == "image/svg+xml; charset=utf-8"
    assert preview.headers["x-content-type-options"] == "nosniff"
    assert preview.headers["cache-control"] == "no-store"
    policy = preview.headers["content-security-policy"]
    assert "default-src 'none'" in policy
    assert "img-src data:" in policy
    assert "script-src" not in policy
    assert "sandbox" in policy

    root = ElementTree.fromstring(preview.content)
    title_group = next(
        group for group in root.iter(f"{SVG_NS}g") if group.get("data-node-id") == "title"
    )
    assert "".join(span.text or "" for span in title_group.iter(f"{SVG_NS}tspan")) == "夏日特惠"
    image = next(root.iter(f"{SVG_NS}image"))
    assert image.get("href", "").startswith("data:image/png;base64,")

    assert_error(
        client.get(f"/api/designs/{document['document_id']}/revisions/2"),
        404,
        "NOT_FOUND",
    )
    invalid_revision = client.get(f"/api/designs/{document['document_id']}/revisions/0")
    assert invalid_revision.status_code == 422
    assert_error(
        client.get("/api/designs/Not_A_Valid_Id"),
        404,
        "NOT_FOUND",
    )


def test_create_edit_undo_redo_restart_and_fixed_history_svg(design_paths, product_bytes):
    with TestClient(app) as client:
        created = create_design(client, product_bytes).json()
        document_id = created["document"]["document_id"]
        commands_url = f"/api/designs/{document_id}/commands"
        revision_one_svg = client.get(created["preview_url"]).content

        changed = client.post(
            commands_url,
            json=command_payload(
                expected_revision=1,
                operations=[
                    {
                        "op": "set_price",
                        "price": "128.50",
                        "source_text": "用户确认价格",
                    }
                ],
            ),
        )
        assert changed.status_code == 200, changed.text
        changed_payload = changed.json()
        assert changed_payload["replayed"] is False
        assert changed_payload["document"]["revision"] == 2
        assert changed_payload["document"]["facts"]["price"] == "128.50"
        assert price_text(changed_payload["document"]) == "¥128.50"
        assert changed_payload["preview_url"].endswith("/revisions/2/preview.svg")
        revision_two_svg = client.get(changed_payload["preview_url"]).content
        assert revision_two_svg != revision_one_svg

        undone = client.post(
            commands_url,
            json=command_payload(
                expected_revision=2,
                operations=[{"op": "undo"}],
            ),
        )
        assert undone.status_code == 200, undone.text
        assert undone.json()["document"]["revision"] == 3
        assert price_text(undone.json()["document"]) == "¥199.90"

        redone = client.post(
            commands_url,
            json=command_payload(
                expected_revision=3,
                operations=[{"op": "redo"}],
            ),
        )
        assert redone.status_code == 200, redone.text
        assert redone.json()["document"]["revision"] == 4
        assert price_text(redone.json()["document"]) == "¥128.50"

        assert client.get(f"/api/designs/{document_id}/revisions/1/preview.svg").content == (
            revision_one_svg
        )
        assert client.get(f"/api/designs/{document_id}/revisions/2/preview.svg").content == (
            revision_two_svg
        )
        assert client.get(f"/api/designs/{document_id}/revisions/4/preview.svg").content == (
            revision_two_svg
        )

    with TestClient(app) as restarted:
        assert restarted.get(f"/api/designs/{document_id}/revisions/1/preview.svg").content == (
            revision_one_svg
        )
        assert restarted.get(f"/api/designs/{document_id}/revisions/2/preview.svg").content == (
            revision_two_svg
        )
        current = latest(restarted, document_id)
        assert current["document"]["revision"] == 4
        assert price_text(current["document"]) == "¥128.50"

        history_three = restarted.get(f"/api/designs/{document_id}/revisions/3")
        assert history_three.status_code == 200
        assert price_text(history_three.json()["document"]) == "¥199.90"
        history_three_svg = restarted.get(f"/api/designs/{document_id}/revisions/3/preview.svg")
        assert history_three_svg.status_code == 200
        assert b"\xc2\xa5199.90" in history_three_svg.content


@pytest.mark.parametrize(
    "content",
    [b"", b"not an image", b"\x89PNG\r\n\x1a\nbroken"],
)
def test_invalid_upload_bytes_are_422_asset_invalid(client, design_paths, content):
    response = client.post(
        "/api/designs",
        files={"product": ("fake.png", content, "image/png")},
        data={"title": "商品", "price": "99"},
    )

    assert_error(response, 422, "ASSET_INVALID")
    assert design_count(design_paths["design_db"]) == 0


def test_upload_filename_is_never_used_as_a_storage_path(client, design_paths, product_bytes):
    response = client.post(
        "/api/designs",
        files={"product": ("../../outside.png", product_bytes, "image/png")},
        data={"title": "文件名", "price": "99"},
    )

    assert response.status_code == 201, response.text
    stored = sorted(design_paths["design_assets"].iterdir())
    assert stored
    assert all(path.name.endswith(".bin") for path in stored)
    assert not (design_paths["design_assets"].parent / "outside.png").exists()


def test_upload_over_limit_is_413_without_creating_a_document(client, design_paths):
    response = client.post(
        "/api/designs",
        files={"product": ("large.png", b"0" * (MAX_ASSET_BYTES + 1), "image/png")},
        data={"title": "超限", "price": "99"},
    )

    assert_error(response, 413, "ASSET_TOO_LARGE")
    assert design_count(design_paths["design_db"]) == 0


@pytest.mark.parametrize("price", ["abc", "0", "-1", "1.999", "1000000"])
def test_invalid_price_is_422_without_partial_document(client, design_paths, product_bytes, price):
    response = create_design(client, product_bytes, price=price)

    assert_error(response, 422, "INVALID_COMMAND")
    assert design_count(design_paths["design_db"]) == 0


def test_missing_form_fields_are_422_and_do_not_create_documents(
    client, design_paths, product_bytes
):
    response = client.post(
        "/api/designs",
        files={"product": ("product.png", product_bytes, "image/png")},
        data={"price": "99"},
    )

    assert response.status_code == 422
    assert design_count(design_paths["design_db"]) == 0


def test_command_entry_rejects_non_user_actor_without_writing(client, product_bytes):
    created = create_design(client, product_bytes).json()
    document_id = created["document"]["document_id"]

    response = client.post(
        f"/api/designs/{document_id}/commands",
        json=command_payload(
            expected_revision=1,
            actor="agent",
            operations=[{"op": "set_text", "node_id": "title", "text": "代理修改"}],
        ),
    )

    assert_error(response, 422, "INVALID_COMMAND")
    unchanged = latest(client, document_id)
    assert unchanged["document"]["revision"] == 1
    assert title_text(unchanged["document"]) == "夏日特惠"


def test_stale_replay_conflict_lock_and_history_errors(client, product_bytes):
    created = create_design(client, product_bytes).json()
    document_id = created["document"]["document_id"]
    commands_url = f"/api/designs/{document_id}/commands"
    command_id = f"cmd-{uuid.uuid4().hex}"
    first_batch = command_payload(
        command_id=command_id,
        expected_revision=1,
        operations=[{"op": "set_text", "node_id": "title", "text": "第一次修改"}],
    )

    first = client.post(commands_url, json=first_batch)
    assert first.status_code == 200, first.text
    assert first.json()["document"]["revision"] == 2

    replay = client.post(commands_url, json=first_batch)
    assert replay.status_code == 200, replay.text
    assert replay.json()["replayed"] is True
    assert replay.json()["document"]["revision"] == 2

    conflict = client.post(
        commands_url,
        json={
            **first_batch,
            "operations": [{"op": "set_text", "node_id": "title", "text": "换了内容"}],
        },
    )
    assert_error(conflict, 409, "IDEMPOTENCY_CONFLICT")
    assert latest(client, document_id)["document"]["revision"] == 2

    stale = client.post(
        commands_url,
        json=command_payload(
            expected_revision=1,
            operations=[{"op": "set_text", "node_id": "title", "text": "过期修改"}],
        ),
    )
    assert_error(stale, 409, "REVISION_CONFLICT")
    assert title_text(latest(client, document_id)["document"]) == "第一次修改"

    assert_error(
        client.post(
            commands_url,
            json=command_payload(expected_revision=1, operations=[{"op": "undo"}]),
        ),
        409,
        "REVISION_CONFLICT",
    )

    locked = client.post(
        commands_url,
        json=command_payload(
            expected_revision=2,
            operations=[{"op": "set_node_lock", "node_id": "title", "locked": True}],
        ),
    )
    assert locked.status_code == 200, locked.text
    assert locked.json()["document"]["revision"] == 3

    blocked = client.post(
        commands_url,
        json=command_payload(
            expected_revision=3,
            operations=[{"op": "set_text", "node_id": "title", "text": "锁定修改"}],
        ),
    )
    assert_error(blocked, 409, "LOCKED_NODE")
    assert title_text(latest(client, document_id)["document"]) == "第一次修改"


def test_nothing_to_undo_invalid_document_and_unknown_operation(client, product_bytes):
    created = create_design(client, product_bytes).json()
    document_id = created["document"]["document_id"]
    commands_url = f"/api/designs/{document_id}/commands"

    assert_error(
        client.post(
            commands_url,
            json=command_payload(expected_revision=1, operations=[{"op": "undo"}]),
        ),
        409,
        "NOTHING_TO_UNDO",
    )
    assert_error(
        client.post(
            "/api/designs/not-valid-id/commands",
            json=command_payload(
                expected_revision=1,
                operations=[{"op": "undo"}],
            ),
        ),
        404,
        "NOT_FOUND",
    )
    unknown = client.post(
        commands_url,
        json=command_payload(
            expected_revision=1,
            operations=[{"op": "delete_everything"}],
        ),
    )
    assert unknown.status_code == 422
    assert latest(client, document_id)["document"]["revision"] == 1


def test_persisted_asset_corruption_fails_preview_without_svg(client, design_paths, product_bytes):
    created = create_design(client, product_bytes).json()
    document_id = created["document"]["document_id"]
    preview_url = created["preview_url"]
    assets = list(design_paths["design_assets"].glob("*.bin"))
    assert len(assets) == 1
    assets[0].write_bytes(b"corrupted")

    preview = client.get(preview_url)

    assert_error(preview, 409, "ASSET_INVALID")
    assert "image/svg+xml" not in preview.headers.get("content-type", "")
    assert b"<svg" not in preview.content

    # Metadata remains readable even when preview generation fails closed.
    metadata = client.get(f"/api/designs/{document_id}")
    assert metadata.status_code == 200
    assert metadata.json()["document"]["revision"] == 1


def test_cross_site_and_non_local_requests_are_still_rejected(client):
    cross_site = client.post(
        "/api/designs",
        headers={
            "Origin": "https://attacker.example",
            "Sec-Fetch-Site": "cross-site",
        },
        data={"title": "攻击", "price": "99"},
    )
    assert cross_site.status_code == 403

    remote = TestClient(app, client=("203.0.113.50", 41000))
    assert remote.get("/api/designs/does-not-exist").status_code == 403
