"""Contract tests for the persistent design repository and content-addressed assets."""

from __future__ import annotations

import hashlib
import os
import sqlite3
from decimal import Decimal
from io import BytesIO

import pytest
from PIL import Image

from app.designs import (
    AssetInvalidError,
    DesignDocument,
    DesignRepository,
    InvalidCommandError,
    NotFoundError,
    create_template,
)


def png_bytes(size: tuple[int, int] = (240, 320)) -> bytes:
    buffer = BytesIO()
    Image.new("RGBA", size, (24, 120, 180, 255)).save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.fixture
def product_bytes() -> bytes:
    return png_bytes()


@pytest.fixture
def repo(tmp_path) -> DesignRepository:
    return DesignRepository(tmp_path / "designs.sqlite3", tmp_path / "assets")


def create_design(repo: DesignRepository, title: str = "夏日特惠") -> object:
    return repo.create(title=title, price=Decimal("199.90"), product_bytes=png_bytes())


def design_count(repo: DesignRepository) -> int:
    with sqlite3.connect(repo.db_path) as connection:
        row = connection.execute("SELECT COUNT(*) FROM designs").fetchone()
    return int(row[0])


def stored_asset_files(repo: DesignRepository) -> list:
    return sorted(path for path in repo.asset_dir.rglob("*") if path.is_file())


def test_create_binds_asset_metadata_and_reopens(tmp_path, product_bytes):
    repo = DesignRepository(tmp_path / "nested" / "designs.sqlite3", tmp_path / "assets")
    document = repo.create(
        title="夏日 & <清凉> 特惠",
        price=Decimal("199.90"),
        product_bytes=product_bytes,
    )

    assert document.document_id.startswith("doc_")
    assert document.revision == 1
    assert document.facts.price == Decimal("199.90")
    assert len(document.asset_refs) == 1
    ref = document.asset_refs[0]
    assert ref.asset_id == "product"
    assert ref.sha256 == hashlib.sha256(product_bytes).hexdigest()
    assert ref.media_type == "image/png"
    assert ref.byte_size == len(product_bytes)

    reopened = DesignRepository(repo.db_path, repo.asset_dir)
    assert reopened.get(document.document_id) == document
    assert reopened.read_assets(document) == {"product": product_bytes}


def test_same_create_input_twice_produces_two_independent_projects(repo):
    first = create_design(repo, "同一个标题")
    second = create_design(repo, "同一个标题")

    assert first.document_id != second.document_id
    assert repo.get(first.document_id).document_id == first.document_id
    assert repo.get(second.document_id).document_id == second.document_id
    assert design_count(repo) == 2


def test_identical_product_bytes_are_published_once(repo):
    first = create_design(repo, "第一款")
    second = create_design(repo, "第二款")

    assert first.asset_refs[0].sha256 == second.asset_refs[0].sha256
    assert len(stored_asset_files(repo)) == 1


def test_get_latest_and_exact_history_without_fallback(repo):
    document = create_design(repo)

    assert repo.get(document.document_id) == document
    assert repo.get(document.document_id, 1) == document
    with pytest.raises(NotFoundError) as missing_revision:
        repo.get(document.document_id, 2)
    assert missing_revision.value.code == "NOT_FOUND"
    with pytest.raises(NotFoundError) as missing_document:
        repo.get("doc_0123456789abcdef0123456789abcdef")
    assert missing_document.value.code == "NOT_FOUND"


@pytest.mark.parametrize(
    "document_id",
    ["", "../escape", "/etc/passwd", "doc/../x", "doc id", "DOC_UPPER", "a" * 65],
)
def test_invalid_document_ids_are_not_found_without_path_lookup(repo, document_id):
    with pytest.raises(NotFoundError) as exc_info:
        repo.get(document_id)
    assert exc_info.value.code == "NOT_FOUND"
    assert str(repo.db_path) not in str(exc_info.value)


@pytest.mark.parametrize("revision", [0, -1, True, 1.0, "1"])
def test_invalid_revision_queries_are_not_found(repo, revision):
    document = create_design(repo)
    with pytest.raises(NotFoundError):
        repo.get(document.document_id, revision)


@pytest.mark.parametrize(
    "payload",
    [
        b"",
        b"not an image",
        b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>",
        b"<!DOCTYPE html><html><body>html</body></html>",
        b"GIF89a",
    ],
)
def test_invalid_product_bytes_never_create_a_document(repo, payload):
    with pytest.raises(AssetInvalidError) as exc_info:
        repo.create(title="商品", price=Decimal("1.00"), product_bytes=payload)
    assert exc_info.value.code == "ASSET_INVALID"
    assert str(repo.db_path) not in str(exc_info.value)
    assert design_count(repo) == 0
    assert stored_asset_files(repo) == []


@pytest.mark.parametrize(
    ("title", "price"),
    [
        ("", Decimal("1.00")),
        ("   ", Decimal("1.00")),
        ("商品", Decimal("0")),
        ("商品", Decimal("1.999")),
    ],
)
def test_invalid_template_inputs_are_rejected_without_persistence(repo, title, price):
    with pytest.raises(InvalidCommandError) as exc_info:
        repo.create(title=title, price=price, product_bytes=png_bytes())
    assert exc_info.value.code == "INVALID_COMMAND"
    assert design_count(repo) == 0


def test_read_assets_rejects_a_corrupted_file(repo, product_bytes):
    document = create_design(repo)
    files = stored_asset_files(repo)
    assert len(files) == 1
    files[0].write_bytes(b"corrupted")

    with pytest.raises(AssetInvalidError) as exc_info:
        repo.read_assets(document)
    assert exc_info.value.code == "ASSET_INVALID"


def test_read_assets_rejects_a_missing_file(repo):
    document = create_design(repo)
    stored_asset_files(repo)[0].unlink()

    with pytest.raises(AssetInvalidError):
        repo.read_assets(document)


def test_read_assets_rejects_symlinks_without_following_them(repo, tmp_path):
    document = create_design(repo)
    stored = stored_asset_files(repo)[0]
    outside = tmp_path / "outside.png"
    outside.write_bytes(png_bytes())
    stored.unlink()
    stored.symlink_to(outside)

    with pytest.raises(AssetInvalidError):
        repo.read_assets(document)


def test_read_assets_requires_the_repository_index_to_match_full_hash(repo, product_bytes):
    document = create_design(repo)
    with sqlite3.connect(repo.db_path) as connection:
        connection.execute(
            "UPDATE assets SET sha256 = ? WHERE document_id = ?",
            ("0" * 64, document.document_id),
        )

    with pytest.raises(AssetInvalidError):
        repo.read_assets(document)


def test_read_assets_rejects_a_document_with_a_tampered_reference(repo):
    document = create_design(repo)
    payload = document.model_dump(mode="json")
    payload["asset_refs"][0]["sha256"] = "0" * 64
    tampered = DesignDocument.model_validate(payload)

    with pytest.raises(AssetInvalidError):
        repo.read_assets(tampered)


def test_asset_publish_failure_does_not_commit_the_document(tmp_path):
    asset_dir = tmp_path / "readonly-assets"
    repo = DesignRepository(tmp_path / "designs.sqlite3", asset_dir)
    original_mode = asset_dir.stat().st_mode
    os.chmod(asset_dir, 0o500)
    try:
        with pytest.raises(AssetInvalidError):
            repo.create(
                title="无法写入素材",
                price=Decimal("1.00"),
                product_bytes=png_bytes(),
            )
    finally:
        os.chmod(asset_dir, original_mode)

    assert design_count(repo) == 0


def test_template_deterministic_id_is_replaced_by_a_repository_unique_id(repo):
    template = create_template(
        title="同一个标题",
        price=Decimal("199.90"),
        product_asset_id="product",
    )
    first = repo.create(title="同一个标题", price=Decimal("199.90"), product_bytes=png_bytes())
    second = repo.create(title="同一个标题", price=Decimal("199.90"), product_bytes=png_bytes())

    assert first.document_id != template.document_id
    assert second.document_id != template.document_id
    assert first.document_id != second.document_id
