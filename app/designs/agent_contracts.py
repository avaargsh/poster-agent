"""Bounded requests and MCP scope for local account-backed design tasks."""

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .commands import CommandId, ExpectedRevision
from .models import DocumentId

MAX_AGENT_TOOL_CALLS = 12
AGENT_TIMEOUT_SECONDS = 120


class AgentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: CommandId
    expected_revision: ExpectedRevision
    prompt: str = Field(min_length=1, max_length=2000)

    @field_validator("prompt")
    @classmethod
    def prompt_text(cls, value: str) -> str:
        value = value.strip()
        if not value or any(ord(char) < 32 and char not in "\n\t" for char in value):
            raise ValueError("prompt must contain readable text")
        return value


class AgentScope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    document_id: DocumentId
    request_id: CommandId
    expected_revision: ExpectedRevision
