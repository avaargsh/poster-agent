"""Contract tests for atomic design commands, idempotency and undo/redo."""

from __future__ import annotations

import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from io import BytesIO
from uuid import uuid4
from xml.etree import ElementTree

import pytest
from PIL import Image
from pydantic import ValidationError

from app.designs import (
    CommandBatch,
    DesignRepository,
    IdempotencyConflictError,
    InvalidCommandError,
    LockedNodeError,
    NothingToRedoError,
    NothingToUndoError,
    Redo,
    ReorderNode,
    RevisionConflictError,
    SetNodeLock,
    SetPrice,
    SetStyle,
    SetText,
    TransformNode,
    Undo,
    render_svg,
)

SVG_NS = "{http://www.w3.org/2000/svg}"


def png_bytes(size: tuple[int, int] = (240, 320)) -> bytes:
    buffer = BytesIO()
    Image.new("RGBA", size, (24, 120, 180, 255)).save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.fixture
def repo(tmp_path) -> DesignRepository:
    return DesignRepository(tmp_path / "designs.sqlite3", tmp_path / "assets")


@pytest.fixture
def document(repo):
    return repo.create(title="夏日特惠", price=Decimal("199.90"), product_bytes=png_bytes())


def command_id() -> str:
    return f"cmd-{uuid4().hex}"


def batch(expected_revision: int, *operations, actor: str = "user") -> CommandBatch:
    return CommandBatch(
        command_id=command_id(),
        expected_revision=expected_revision,
        actor=actor,
        operations=list(operations),
    )


def command_count(repo: DesignRepository, document_id: str) -> int:
    with sqlite3.connect(repo.db_path) as connection:
        row = connection.execute(
            "SELECT COUNT(*) FROM commands WHERE document_id = ?", (document_id,)
        ).fetchone()
    return int(row[0])


def head_revision(repo: DesignRepository, document_id: str) -> int:
    return repo.get(document_id).revision


def content(document) -> dict:
    return document.model_dump(mode="json", exclude={"revision"})


def title_text(document) -> str:
    node = next(node for node in document.nodes if node.node_id == "title")
    return "".join(line.text for line in node.lines)


def node(document, node_id: str):
    return next(node for node in document.nodes if node.node_id == node_id)


def svg_text_position(repo, document):
    svg = render_svg(document, assets=repo.read_assets(document))
    root = ElementTree.fromstring(svg)
    group = next(group for group in root.iter(f"{SVG_NS}g") if group.get("data-node-id") == "title")
    span = next(group.iter(f"{SVG_NS}tspan"))
    return span.get("x"), span.get("y")


def test_set_text_creates_a_new_revision_with_explicit_lines(repo, document):
    title = node(document, "title")
    original_first_line = title.lines[0]
    new_text = "新的标题文字会生成坐标"

    result = repo.execute(
        document.document_id,
        batch(1, SetText(node_id="title", text=new_text)),
    )

    assert result.replayed is False
    assert result.command_id
    updated = result.document
    assert updated.revision == 2
    assert title_text(updated) == new_text
    title_after = node(updated, "title")
    assert title_after.font_size == title.font_size
    assert (title_after.x, title_after.y) == (title.x, title.y)
    assert (title_after.lines[0].x, title_after.lines[0].y) == (
        original_first_line.x,
        original_first_line.y,
    )
    for index, line in enumerate(title_after.lines):
        assert line.text
        if index:
            assert line.y > title_after.lines[index - 1].y
    assert repo.get(document.document_id) == updated
    assert repo.get(document.document_id, 1) == document


def test_set_text_accepts_the_full_supported_length_and_keeps_line_limit(repo, document):
    text = "夏" * 200
    updated = repo.execute(
        document.document_id,
        batch(1, SetText(node_id="title", text=text)),
    ).document

    title = node(updated, "title")
    assert "".join(line.text for line in title.lines) == text
    assert 1 <= len(title.lines) <= 12


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "第一行\n第二行",
        "含\t制表符",
        "含\x85控制符",
        "含\ufffe字符",
        "含\uffff字符",
        "含\ud800字符",
        "x" * 201,
    ],
)
def test_set_text_rejects_blank_control_and_oversized_text(text):
    with pytest.raises(ValidationError):
        SetText(node_id="title", text=text)


def test_set_text_preserves_cjk_emoji_and_escaped_markup(repo, document):
    text = "夏日😀 & <清凉>"
    updated = repo.execute(
        document.document_id,
        batch(1, SetText(node_id="title", text=text)),
    ).document

    assert title_text(updated) == text
    svg = render_svg(updated, assets=repo.read_assets(updated))
    root = ElementTree.fromstring(svg)
    group = next(group for group in root.iter(f"{SVG_NS}g") if group.get("data-node-id") == "title")
    assert "".join(span.text or "" for span in group.iter(f"{SVG_NS}tspan")) == text


def test_set_text_that_overflows_coordinate_bounds_fails_without_writing(repo, document):
    moved = repo.execute(
        document.document_id,
        batch(1, TransformNode(node_id="title", y=8000)),
    ).document

    with pytest.raises(InvalidCommandError) as exc_info:
        repo.execute(
            document.document_id,
            batch(2, SetText(node_id="title", text="夏" * 200)),
        )
    assert exc_info.value.code == "INVALID_COMMAND"
    assert repo.get(document.document_id) == moved
    assert command_count(repo, document.document_id) == 1


def test_set_text_cannot_change_price_or_non_text_nodes(repo, document):
    with pytest.raises(InvalidCommandError) as exc_info:
        repo.execute(
            document.document_id,
            batch(1, SetText(node_id="price", text="¥1.00")),
        )
    assert exc_info.value.code == "INVALID_COMMAND"
    assert head_revision(repo, document.document_id) == 1

    with pytest.raises(InvalidCommandError) as exc_info:
        repo.execute(
            document.document_id,
            batch(1, SetText(node_id="product", text="商品")),
        )
    assert exc_info.value.code == "INVALID_COMMAND"


def test_set_price_updates_the_business_fact_and_unique_price_node(repo, document):
    command = batch(
        1,
        SetPrice(price=Decimal("289.5"), source_text="用户确认：双十一价格改为 289.5"),
    )
    result = repo.execute(document.document_id, command)

    updated = result.document
    assert updated.revision == 2
    assert updated.facts.price == Decimal("289.50")
    price = node(updated, "price")
    assert "".join(line.text for line in price.lines) == "¥289.50"
    assert price.font_size == node(document, "price").font_size

    with sqlite3.connect(repo.db_path) as connection:
        row = connection.execute(
            "SELECT request_json FROM commands WHERE document_id = ? AND command_id = ?",
            (document.document_id, command.command_id),
        ).fetchone()
    assert row is not None
    stored = json.loads(row[0])
    assert stored["operations"][0]["source_text"] == "用户确认：双十一价格改为 289.5"


@pytest.mark.parametrize(
    ("price", "source_text"),
    [
        (Decimal("0"), "来源"),
        (Decimal("-1"), "来源"),
        (Decimal("1.999"), "来源"),
        (Decimal("1.00"), ""),
        (Decimal("1.00"), "   "),
        (Decimal("1.00"), "含\n换行"),
        (Decimal("1.00"), "x" * 201),
    ],
)
def test_set_price_rejects_invalid_price_and_source(price, source_text):
    with pytest.raises(ValidationError):
        SetPrice(price=price, source_text=source_text)


def test_set_price_requires_an_unlocked_price_node(repo, document):
    locked = repo.execute(
        document.document_id,
        batch(1, SetNodeLock(node_id="price", locked=True)),
    ).document
    with pytest.raises(LockedNodeError):
        repo.execute(
            document.document_id,
            batch(2, SetPrice(price=Decimal("100.00"), source_text="用户确认")),
        )
    assert repo.get(document.document_id) == locked


def test_set_style_updates_allowed_node_properties(repo, document):
    updated = repo.execute(
        document.document_id,
        batch(
            1,
            SetStyle(node_id="title", color="#ffffff", font_size=72, opacity=0.8),
        ),
    ).document
    title = node(updated, "title")
    assert title.color == "#ffffff"
    assert title.font_size == 72
    assert title.opacity == 0.8

    updated = repo.execute(
        document.document_id,
        batch(2, SetStyle(node_id="accent", color="#abcdef")),
    ).document
    accent = node(updated, "accent")
    assert accent.fill == "#abcdef"

    updated = repo.execute(
        document.document_id,
        batch(3, SetStyle(node_id="product", opacity=0.5)),
    ).document
    assert node(updated, "product").opacity == 0.5


def test_set_style_rejects_unsupported_properties_and_missing_fields(repo, document):
    with pytest.raises(InvalidCommandError):
        repo.execute(
            document.document_id,
            batch(1, SetStyle(node_id="product", color="#ffffff")),
        )
    with pytest.raises(InvalidCommandError):
        repo.execute(
            document.document_id,
            batch(1, SetStyle(node_id="product", font_size=24)),
        )
    with pytest.raises(InvalidCommandError):
        repo.execute(
            document.document_id,
            batch(1, SetStyle(node_id="accent", font_size=24)),
        )
    with pytest.raises(ValidationError):
        SetStyle(node_id="title")
    with pytest.raises(ValidationError):
        SetStyle(node_id="title", opacity=1.5)
    with pytest.raises(ValidationError):
        SetStyle(node_id="title", color="red")


def test_transform_node_moves_text_lines_and_svg_coordinates(repo, document):
    before = svg_text_position(repo, document)
    title = node(document, "title")
    first_line = title.lines[0]

    updated = repo.execute(
        document.document_id,
        batch(1, TransformNode(node_id="title", x=100, y=120, rotation=15)),
    ).document
    title_after = node(updated, "title")
    assert (title_after.x, title_after.y, title_after.rotation) == (100, 120, 15)
    assert title_after.lines[0].x == first_line.x + (100 - title.x)
    assert title_after.lines[0].y == first_line.y + (120 - title.y)
    assert title_after.font_size == title.font_size

    after = svg_text_position(repo, updated)
    assert after != before
    assert float(after[0]) == title_after.lines[0].x
    assert float(after[1]) == title_after.lines[0].y


def test_transform_node_width_change_does_not_change_font_size_or_lines(repo, document):
    title = node(document, "title")
    updated = repo.execute(
        document.document_id,
        batch(1, TransformNode(node_id="title", width=500, height=300)),
    ).document
    title_after = node(updated, "title")
    assert (title_after.width, title_after.height) == (500, 300)
    assert title_after.font_size == title.font_size
    assert title_after.lines == title.lines


@pytest.mark.parametrize(
    "factory",
    [
        lambda: TransformNode(node_id="title"),
        lambda: TransformNode(node_id="title", x=float("inf")),
        lambda: TransformNode(node_id="title", y=float("nan")),
        lambda: TransformNode(node_id="title", width=0),
        lambda: TransformNode(node_id="title", height=-1),
        lambda: TransformNode(node_id="title", rotation=361),
    ],
)
def test_transform_rejects_empty_and_out_of_bounds_values(factory):
    with pytest.raises(ValidationError):
        factory()


def test_locked_nodes_reject_edits_until_explicitly_unlocked(repo, document):
    locked = repo.execute(
        document.document_id,
        batch(1, SetNodeLock(node_id="title", locked=True)),
    ).document
    assert node(locked, "title").locked is True

    rejected = [
        SetText(node_id="title", text="不能修改"),
        SetStyle(node_id="title", color="#000000"),
        TransformNode(node_id="title", x=1),
        ReorderNode(node_id="title", index=0),
    ]
    for operation in rejected:
        with pytest.raises(LockedNodeError) as exc_info:
            repo.execute(document.document_id, batch(2, operation))
        assert exc_info.value.code == "LOCKED_NODE"
    assert repo.get(document.document_id) == locked

    unlocked = repo.execute(
        document.document_id,
        batch(2, SetNodeLock(node_id="title", locked=False)),
    ).document
    assert node(unlocked, "title").locked is False

    edited = repo.execute(
        document.document_id,
        batch(3, SetText(node_id="title", text="解锁后可以修改")),
    ).document
    assert edited.revision == 4
    assert title_text(edited) == "解锁后可以修改"


@pytest.mark.parametrize("locked", [0, 1, "true", None])
def test_set_node_lock_requires_a_strict_boolean(locked):
    with pytest.raises(ValidationError):
        SetNodeLock(node_id="title", locked=locked)


def test_reorder_node_moves_the_layer_without_duplication(repo, document):
    original_ids = [item.node_id for item in document.nodes]
    updated = repo.execute(
        document.document_id,
        batch(1, ReorderNode(node_id="product", index=0)),
    ).document

    assert [item.node_id for item in updated.nodes][0] == "product"
    assert sorted(item.node_id for item in updated.nodes) == sorted(original_ids)
    assert len(updated.nodes) == len(original_ids)

    svg = render_svg(updated, assets=repo.read_assets(updated))
    root = ElementTree.fromstring(svg)
    rendered_ids = [
        group.get("data-node-id") for group in root.iter(f"{SVG_NS}g") if group.get("data-node-id")
    ]
    assert rendered_ids[0] == "product"


@pytest.mark.parametrize(
    "factory",
    [
        lambda: ReorderNode(node_id="title", index=-1),
        lambda: ReorderNode(node_id="title", index=True),
        lambda: ReorderNode(node_id="title", index=1.0),
    ],
)
def test_reorder_rejects_non_strict_or_negative_indices(factory):
    with pytest.raises(ValidationError):
        factory()


def test_reorder_rejects_an_index_beyond_the_document(repo, document):
    with pytest.raises(InvalidCommandError):
        repo.execute(
            document.document_id,
            batch(1, ReorderNode(node_id="title", index=len(document.nodes))),
        )
    assert head_revision(repo, document.document_id) == 1


def test_same_command_id_same_input_replays_the_original_revision(repo, document):
    command = batch(1, SetText(node_id="title", text="第一次修改"))
    first = repo.execute(document.document_id, command)
    assert first.replayed is False
    assert first.document.revision == 2

    second_edit = repo.execute(
        document.document_id,
        batch(2, SetText(node_id="title", text="第二次修改")),
    ).document
    assert second_edit.revision == 3

    replay = repo.execute(document.document_id, command)
    assert replay.replayed is True
    assert replay.command_id == command.command_id
    assert replay.document.revision == 2
    assert title_text(replay.document) == "第一次修改"
    assert repo.get(document.document_id) == second_edit


def test_same_command_id_with_different_input_conflicts(repo, document):
    first = batch(1, SetText(node_id="title", text="第一次修改"))
    repo.execute(document.document_id, first)

    changed_operations = CommandBatch(
        command_id=first.command_id,
        expected_revision=2,
        actor="user",
        operations=[SetText(node_id="title", text="不同输入")],
    )
    with pytest.raises(IdempotencyConflictError) as exc_info:
        repo.execute(document.document_id, changed_operations)
    assert exc_info.value.code == "IDEMPOTENCY_CONFLICT"

    changed_actor = CommandBatch(
        command_id=first.command_id,
        expected_revision=1,
        actor="agent",
        operations=[SetText(node_id="title", text="第一次修改")],
    )
    with pytest.raises(IdempotencyConflictError):
        repo.execute(document.document_id, changed_actor)

    assert head_revision(repo, document.document_id) == 2
    assert command_count(repo, document.document_id) == 1


def test_stale_revision_conflicts_without_writing_idempotency_or_history(repo, document):
    stale = batch(5, SetText(node_id="title", text="过期修改"))
    with pytest.raises(RevisionConflictError) as exc_info:
        repo.execute(document.document_id, stale)
    assert exc_info.value.code == "REVISION_CONFLICT"
    assert head_revision(repo, document.document_id) == 1
    assert command_count(repo, document.document_id) == 0

    retried = CommandBatch(
        command_id=stale.command_id,
        expected_revision=1,
        actor=stale.actor,
        operations=stale.operations,
    )
    result = repo.execute(document.document_id, retried)
    assert result.replayed is False
    assert result.document.revision == 2


def test_concurrent_writers_with_the_same_revision_only_one_succeeds(repo, document):
    barrier = threading.Barrier(2)

    def write(text: str):
        barrier.wait()
        try:
            result = repo.execute(
                document.document_id,
                batch(1, SetText(node_id="title", text=text)),
            )
            return ("ok", result.document.revision)
        except RevisionConflictError:
            return ("conflict", None)

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(write, ["并发一", "并发二"]))

    assert sorted(code for code, _ in outcomes) == ["conflict", "ok"]
    assert head_revision(repo, document.document_id) == 2
    assert command_count(repo, document.document_id) == 1
    assert title_text(repo.get(document.document_id)) in {"并发一", "并发二"}


def test_a_batch_is_atomic_and_one_revision(repo, document):
    failed = batch(
        1,
        SetText(node_id="title", text="本批次不应留下"),
        SetStyle(node_id="product", color="#ffffff"),
    )
    with pytest.raises(InvalidCommandError):
        repo.execute(document.document_id, failed)
    assert repo.get(document.document_id) == document
    assert command_count(repo, document.document_id) == 0

    succeeded = repo.execute(
        document.document_id,
        batch(
            1,
            SetText(node_id="title", text="一次提交两个操作"),
            SetStyle(node_id="accent", color="#123456"),
        ),
    ).document
    assert succeeded.revision == 2
    assert title_text(succeeded) == "一次提交两个操作"
    assert node(succeeded, "accent").fill == "#123456"


def test_one_batch_is_one_undo_unit(repo, document):
    edited = repo.execute(
        document.document_id,
        batch(
            1,
            SetText(node_id="title", text="批量修改"),
            SetStyle(node_id="accent", color="#111111"),
            TransformNode(node_id="accent", x=80),
        ),
    ).document
    assert edited.revision == 2

    undone = repo.execute(document.document_id, batch(2, Undo())).document
    assert undone.revision == 3
    assert content(undone) == content(document)

    redone = repo.execute(document.document_id, batch(3, Redo())).document
    assert redone.revision == 4
    assert content(redone) == content(edited)


def test_undo_and_redo_create_increasing_revisions_and_keep_history(repo, document):
    edited = repo.execute(
        document.document_id,
        batch(1, SetText(node_id="title", text="新标题")),
    ).document
    undone = repo.execute(document.document_id, batch(2, Undo())).document
    redone = repo.execute(document.document_id, batch(3, Redo())).document

    assert (edited.revision, undone.revision, redone.revision) == (2, 3, 4)
    assert repo.get(document.document_id, 2) == edited
    assert repo.get(document.document_id, 3) == undone
    assert repo.get(document.document_id, 4) == redone
    assert repo.get(document.document_id, 1) == document


def test_new_normal_edit_clears_redo_branch(repo, document):
    repo.execute(
        document.document_id,
        batch(1, SetText(node_id="title", text="第一次修改")),
    )
    repo.execute(document.document_id, batch(2, Undo()))
    repo.execute(
        document.document_id,
        batch(3, SetText(node_id="title", text="撤销后的新分支")),
    )

    with pytest.raises(NothingToRedoError) as exc_info:
        repo.execute(document.document_id, batch(4, Redo()))
    assert exc_info.value.code == "NOTHING_TO_REDO"
    assert head_revision(repo, document.document_id) == 4


def test_first_creation_cannot_be_undone(repo, document):
    with pytest.raises(NothingToUndoError) as exc_info:
        repo.execute(document.document_id, batch(1, Undo()))
    assert exc_info.value.code == "NOTHING_TO_UNDO"
    assert head_revision(repo, document.document_id) == 1
    assert command_count(repo, document.document_id) == 0


def test_nothing_to_redo_is_an_error_without_writes(repo, document):
    with pytest.raises(NothingToRedoError) as exc_info:
        repo.execute(document.document_id, batch(1, Redo()))
    assert exc_info.value.code == "NOTHING_TO_REDO"
    assert repo.get(document.document_id) == document
    assert command_count(repo, document.document_id) == 0


def test_repeated_undo_command_id_does_not_undo_twice(repo, document):
    repo.execute(
        document.document_id,
        batch(1, SetText(node_id="title", text="第一次修改")),
    )
    undo_command = batch(2, Undo())
    first_undo = repo.execute(document.document_id, undo_command)
    assert first_undo.document.revision == 3

    replay = repo.execute(document.document_id, undo_command)
    assert replay.replayed is True
    assert replay.document.revision == 3
    assert head_revision(repo, document.document_id) == 3
    assert command_count(repo, document.document_id) == 2


def test_multiple_undo_and_redo_steps_walk_the_saved_history(repo, document):
    second = repo.execute(
        document.document_id,
        batch(1, SetText(node_id="title", text="第一次修改")),
    ).document
    third = repo.execute(
        document.document_id,
        batch(2, SetText(node_id="title", text="第二次修改")),
    ).document

    fourth = repo.execute(document.document_id, batch(3, Undo())).document
    fifth = repo.execute(document.document_id, batch(4, Undo())).document
    assert content(fourth) == content(second)
    assert content(fifth) == content(document)

    sixth = repo.execute(document.document_id, batch(5, Redo())).document
    seventh = repo.execute(document.document_id, batch(6, Redo())).document
    assert content(sixth) == content(second)
    assert content(seventh) == content(third)
    assert [repo.get(document.document_id, revision).revision for revision in range(1, 8)] == list(
        range(1, 8)
    )


def test_idempotency_scope_is_per_document(repo):
    first = create_design_for_scope(repo, "第一款")
    second = create_design_for_scope(repo, "第二款")
    shared_command_id = command_id()

    first_batch = CommandBatch(
        command_id=shared_command_id,
        expected_revision=1,
        actor="user",
        operations=[SetText(node_id="title", text="第一款修改")],
    )
    second_batch = CommandBatch(
        command_id=shared_command_id,
        expected_revision=1,
        actor="user",
        operations=[SetText(node_id="title", text="第二款修改")],
    )
    first_result = repo.execute(first.document_id, first_batch)
    second_result = repo.execute(second.document_id, second_batch)

    assert first_result.replayed is False
    assert second_result.replayed is False
    assert first_result.document.revision == 2
    assert second_result.document.revision == 2


def test_canonical_decimal_price_replays_as_the_same_input(repo, document):
    original = batch(
        1,
        SetPrice(price=Decimal("10.5"), source_text="用户给出十元五角"),
    )
    repo.execute(document.document_id, original)

    equivalent = CommandBatch(
        command_id=original.command_id,
        expected_revision=1,
        actor="user",
        operations=[SetPrice(price=Decimal("10.50"), source_text="用户给出十元五角")],
    )
    replay = repo.execute(document.document_id, equivalent)
    assert replay.replayed is True
    assert replay.document.revision == 2
    assert replay.document.facts.price == Decimal("10.50")


def create_design_for_scope(repo: DesignRepository, title: str):
    return repo.create(title=title, price=Decimal("1.00"), product_bytes=png_bytes())


def test_undo_and_redo_survive_a_new_repository_instance(tmp_path, document, repo):
    repo.execute(
        document.document_id,
        batch(1, SetText(node_id="title", text="可撤销")),
    )
    reopened = DesignRepository(repo.db_path, repo.asset_dir)
    undone = reopened.execute(document.document_id, batch(2, Undo())).document
    assert content(undone) == content(document)
    redone = reopened.execute(document.document_id, batch(3, Redo())).document
    assert redone.revision == 4
    assert title_text(redone) == "可撤销"


def test_undo_and_redo_must_be_alone_in_a_batch():
    with pytest.raises(ValidationError):
        CommandBatch(
            command_id=command_id(),
            expected_revision=1,
            actor="user",
            operations=[Undo(), SetText(node_id="title", text="混合")],
        )
    with pytest.raises(ValidationError):
        CommandBatch(
            command_id=command_id(),
            expected_revision=1,
            actor="user",
            operations=[Undo(), Redo()],
        )


@pytest.mark.parametrize(
    "payload",
    [
        {
            "command_id": "short",
            "expected_revision": 1,
            "actor": "user",
            "operations": [{"op": "undo"}],
        },
        {
            "command_id": "cmd-" + "a" * 125,
            "expected_revision": 1,
            "actor": "user",
            "operations": [{"op": "undo"}],
        },
        {
            "command_id": "cmd-12345678",
            "expected_revision": True,
            "actor": "user",
            "operations": [{"op": "undo"}],
        },
        {
            "command_id": "cmd-12345678",
            "expected_revision": 0,
            "actor": "user",
            "operations": [{"op": "undo"}],
        },
        {
            "command_id": "cmd-12345678",
            "expected_revision": 1,
            "actor": "admin",
            "operations": [{"op": "undo"}],
        },
        {
            "command_id": "cmd-12345678",
            "expected_revision": 1,
            "actor": "user",
            "operations": [],
        },
        {
            "command_id": "cmd-12345678",
            "expected_revision": 1,
            "actor": "user",
            "operations": [{"op": "undo"}] * 21,
        },
        {
            "command_id": "cmd-12345678",
            "expected_revision": 1,
            "actor": "user",
            "operations": [{"op": "unknown"}],
        },
        {
            "command_id": "cmd-12345678",
            "expected_revision": 1,
            "actor": "user",
            "operations": [{"op": "undo", "extra": True}],
        },
        {
            "command_id": "cmd-12345678",
            "expected_revision": 1,
            "actor": "user",
            "operations": [{"op": "undo"}],
            "extra": True,
        },
    ],
)
def test_command_batch_rejects_unknown_or_invalid_fields(payload):
    with pytest.raises(ValidationError):
        CommandBatch.model_validate(payload)


def test_operation_unknown_fields_are_rejected():
    with pytest.raises(ValidationError):
        SetText(node_id="title", text="文本", unexpected=True)
    with pytest.raises(ValidationError):
        SetPrice(price=Decimal("1.00"), source_text="来源", unexpected=True)
    with pytest.raises(ValidationError):
        TransformNode(node_id="title", x=1, unexpected=True)
    with pytest.raises(ValidationError):
        ReorderNode(node_id="title", index=0, unexpected=True)
