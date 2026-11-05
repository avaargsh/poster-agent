"""Bounded, strict command contracts for design edits.

Command batches are the only mutation entry point for a persisted document.
They are deliberately small Pydantic unions so unknown fields or operations are
rejected before the repository sees them.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Literal, Union

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StringConstraints,
    field_validator,
    model_validator,
)

from .models import Coordinate, DesignDocument, Dimension, HexColor, NodeId, validate_plain_text

CommandId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{8,128}$")]
Actor = Literal["user", "agent", "cli", "external"]
ExpectedRevision = Annotated[int, Field(gt=0, strict=True)]

PlainText = Annotated[str, StringConstraints(min_length=1, max_length=200)]
SourceText = Annotated[str, StringConstraints(min_length=1, max_length=200)]


def _validate_plain_text(value: str) -> str:
    return validate_plain_text(value)


class _Operation(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SetText(_Operation):
    op: Literal["set_text"] = "set_text"
    node_id: NodeId
    text: PlainText

    @field_validator("text")
    @classmethod
    def text_is_plain(cls, value: str) -> str:
        return _validate_plain_text(value)


class SetPrice(_Operation):
    op: Literal["set_price"] = "set_price"
    price: Annotated[
        Decimal,
        Field(gt=0, le=999999, decimal_places=2, allow_inf_nan=False),
    ]
    source_text: SourceText

    @field_validator("price")
    @classmethod
    def canonical_price(cls, value: Decimal) -> Decimal:
        return value.quantize(Decimal("0.01"))

    @field_validator("source_text")
    @classmethod
    def source_is_plain(cls, value: str) -> str:
        return _validate_plain_text(value)


class SetStyle(_Operation):
    op: Literal["set_style"] = "set_style"
    node_id: NodeId
    color: HexColor | None = None
    font_size: Annotated[
        float | None,
        Field(ge=8, le=256, allow_inf_nan=False),
    ] = None
    opacity: Annotated[
        float | None,
        Field(ge=0, le=1, allow_inf_nan=False),
    ] = None

    @model_validator(mode="after")
    def at_least_one_property(self) -> SetStyle:
        if self.color is None and self.font_size is None and self.opacity is None:
            raise ValueError("at least one style property is required")
        return self


class TransformNode(_Operation):
    op: Literal["transform_node"] = "transform_node"
    node_id: NodeId
    x: Coordinate | None = None
    y: Coordinate | None = None
    width: Dimension | None = None
    height: Dimension | None = None
    rotation: Annotated[
        float | None,
        Field(ge=-360, le=360, allow_inf_nan=False),
    ] = None

    @model_validator(mode="after")
    def at_least_one_geometry_property(self) -> TransformNode:
        values = (self.x, self.y, self.width, self.height, self.rotation)
        if all(value is None for value in values):
            raise ValueError("at least one geometry property is required")
        return self


class SetNodeLock(_Operation):
    op: Literal["set_node_lock"] = "set_node_lock"
    node_id: NodeId
    locked: StrictBool


class ReorderNode(_Operation):
    op: Literal["reorder_node"] = "reorder_node"
    node_id: NodeId
    index: Annotated[int, Field(ge=0, le=199, strict=True)]


class Undo(_Operation):
    op: Literal["undo"] = "undo"


class Redo(_Operation):
    op: Literal["redo"] = "redo"


CommandOperation = Annotated[
    Union[SetText, SetPrice, SetStyle, TransformNode, SetNodeLock, ReorderNode, Undo, Redo],
    Field(discriminator="op"),
]


class CommandBatch(BaseModel):
    """One atomic, idempotent unit of document edits."""

    model_config = ConfigDict(extra="forbid")

    command_id: CommandId
    expected_revision: ExpectedRevision
    actor: Actor
    operations: Annotated[list[CommandOperation], Field(min_length=1, max_length=20)]

    @model_validator(mode="after")
    def undo_and_redo_are_alone(self) -> CommandBatch:
        has_history_operation = any(
            isinstance(operation, (Undo, Redo)) for operation in self.operations
        )
        if has_history_operation and len(self.operations) != 1:
            raise ValueError("undo and redo must be the only operation in a batch")
        return self


class CommandResult(BaseModel):
    """The committed snapshot for a command, or the original replay snapshot."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    document: DesignDocument
    command_id: CommandId
    replayed: bool


# Readable aliases used by callers that prefer an explicit suffix.
SetTextOp = SetText
SetPriceOp = SetPrice
SetStyleOp = SetStyle
TransformNodeOp = TransformNode
SetNodeLockOp = SetNodeLock
ReorderNodeOp = ReorderNode
UndoOp = Undo
RedoOp = Redo
