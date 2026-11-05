"""Stable, user-facing domain errors for the design repository.

Errors never contain SQL text, server paths or other storage detail.  Callers
can branch on the stable ``code`` without parsing messages.
"""

from __future__ import annotations


class DesignError(Exception):
    """Base class for design-domain failures."""

    code = "DESIGN_ERROR"

    def __init__(self, message: str | None = None, *, code: str | None = None) -> None:
        self.message = message or self.default_message
        if code is not None:
            self.code = code
        super().__init__(self.message)

    @property
    def default_message(self) -> str:
        return "design operation failed"

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


class NotFoundError(DesignError):
    code = "NOT_FOUND"

    @property
    def default_message(self) -> str:
        return "design not found"


class InvalidCommandError(DesignError):
    code = "INVALID_COMMAND"

    @property
    def default_message(self) -> str:
        return "invalid design command"


class RevisionConflictError(DesignError):
    code = "REVISION_CONFLICT"

    @property
    def default_message(self) -> str:
        return "design revision conflict"


class LockedNodeError(DesignError):
    code = "LOCKED_NODE"

    @property
    def default_message(self) -> str:
        return "node is locked"


class AssetInvalidError(DesignError):
    code = "ASSET_INVALID"

    @property
    def default_message(self) -> str:
        return "design asset is invalid"


class IdempotencyConflictError(DesignError):
    code = "IDEMPOTENCY_CONFLICT"

    @property
    def default_message(self) -> str:
        return "command_id was already used with different input"


class NothingToUndoError(DesignError):
    code = "NOTHING_TO_UNDO"

    @property
    def default_message(self) -> str:
        return "there is nothing to undo"


class NothingToRedoError(DesignError):
    code = "NOTHING_TO_REDO"

    @property
    def default_message(self) -> str:
        return "there is nothing to redo"


class ApprovalRequiredError(DesignError):
    """Current revision is not approved for the protected export endpoint."""

    code = "APPROVAL_REQUIRED"

    @property
    def default_message(self) -> str:
        return "current design revision requires explicit approval"


class ApprovalDigestConflictError(DesignError):
    code = "APPROVAL_DIGEST_CONFLICT"

    @property
    def default_message(self) -> str:
        return "design approval snapshot digest mismatch"


class ApprovalConflictError(DesignError):
    code = "APPROVAL_CONFLICT"

    @property
    def default_message(self) -> str:
        return "this design revision already has a different approval"


class StorageError(DesignError):
    code = "STORAGE_ERROR"

    @property
    def default_message(self) -> str:
        return "design storage is unavailable"


# Explicit aliases make the exception names readable from call sites.
DocumentNotFoundError = NotFoundError
