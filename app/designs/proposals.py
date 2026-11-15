"""Bounded agent suggestions that require a separate human application."""

from typing import Annotated, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .commands import (
    CommandBatch,
    CommandId,
    ExpectedRevision,
    ReorderNode,
    SetPrice,
    SetStyle,
    SetText,
    TransformNode,
)
from .models import validate_plain_text

ProposalOperation = Annotated[
    Union[SetText, SetPrice, SetStyle, TransformNode, ReorderNode], Field(discriminator="op")
]


class EditProposalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: CommandId
    expected_revision: ExpectedRevision
    summary: str = Field(min_length=1, max_length=500)
    operations: list[ProposalOperation] = Field(min_length=1, max_length=20)

    @field_validator("summary")
    @classmethod
    def plain_summary(cls, value: str) -> str:
        return validate_plain_text(value).strip()


def application_command(proposal_id: str, request: EditProposalRequest) -> CommandBatch:
    return CommandBatch.model_validate(
        {
            "command_id": "proposal_" + proposal_id,
            "expected_revision": request.expected_revision,
            "actor": "user",
            "operations": [operation.model_dump(mode="json") for operation in request.operations],
        }
    )
