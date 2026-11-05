"""Opt-in named local reviewer credentials for single-process loopback routes.

Bearer possession attributes an action to a locally configured principal. This
is not independent human identity proof, an IdP, or an external authorization
system. Token digests must come from high-entropy random tokens.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from typing import Literal

from fastapi import Request

_SCOPE = Literal["approve", "export", "audit.read"]
_VALID_SCOPES = frozenset({"approve", "export", "audit.read"})
_PRINCIPAL_ID = re.compile(r"^[a-z][a-z0-9_-]{2,63}$")
_SHA256 = re.compile(r"^[a-f0-9]{64}$")


@dataclass(frozen=True)
class ReviewerContext:
    principal_id: str
    policy_digest: str


class ReviewerAccessError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def _invalid_config() -> ReviewerAccessError:
    return ReviewerAccessError(
        503, "DESIGN_REVIEWER_CONFIG_INVALID", "reviewer policy configuration is invalid"
    )


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate policy key")
        result[key] = value
    return result


def _parse_policy(raw: str) -> tuple[dict, str]:
    if not raw or len(raw) > 8192:
        raise _invalid_config()
    try:
        policy = json.loads(raw, object_pairs_hook=_reject_duplicate_keys)
    except (ValueError, TypeError):
        raise _invalid_config() from None
    if not isinstance(policy, dict) or not 1 <= len(policy) <= 32:
        raise _invalid_config()
    observed_hashes: set[str] = set()
    for principal_id, settings in policy.items():
        if not isinstance(principal_id, str) or _PRINCIPAL_ID.fullmatch(principal_id) is None:
            raise _invalid_config()
        if not isinstance(settings, dict) or set(settings) != {"token_sha256", "scopes"}:
            raise _invalid_config()
        digest = settings["token_sha256"]
        scopes = settings["scopes"]
        if (
            not isinstance(digest, str)
            or _SHA256.fullmatch(digest) is None
            or digest in observed_hashes
            or not isinstance(scopes, list)
            or not 1 <= len(scopes) <= len(_VALID_SCOPES)
            or any(not isinstance(scope, str) for scope in scopes)
            or len(scopes) != len(set(scopes))
            or not set(scopes) <= _VALID_SCOPES
        ):
            raise _invalid_config()
        observed_hashes.add(digest)
    canonical = json.dumps(policy, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    policy_digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return policy, policy_digest


def reviewer_for_request(request: Request, scope: _SCOPE) -> ReviewerContext | None:
    """Use the policy captured at app lifespan start, never user headers as identity."""
    raw = getattr(request.app.state, "design_reviewer_policy_raw", None)
    if raw is None:
        return None  # Legacy loopback behavior: no named-reviewer evidence.
    policy, policy_digest = _parse_policy(raw)

    authorization = request.headers.get("authorization", "")
    if not authorization.startswith("Bearer "):
        raise ReviewerAccessError(
            401, "DESIGN_REVIEWER_UNAUTHENTICATED", "reviewer bearer token required"
        )
    token = authorization.removeprefix("Bearer ")
    if not 32 <= len(token) <= 512 or any(ord(char) < 33 or ord(char) > 126 for char in token):
        raise ReviewerAccessError(
            401, "DESIGN_REVIEWER_UNAUTHENTICATED", "reviewer bearer token required"
        )
    token_sha256 = hashlib.sha256(token.encode("ascii")).hexdigest()
    principal_id = None
    principal_scopes = None
    for candidate, settings in policy.items():
        if hmac.compare_digest(token_sha256, settings["token_sha256"]):
            principal_id = candidate
            principal_scopes = settings["scopes"]
    if principal_id is None:
        raise ReviewerAccessError(
            401, "DESIGN_REVIEWER_UNAUTHENTICATED", "reviewer bearer token required"
        )
    if scope not in principal_scopes:
        raise ReviewerAccessError(
            403, "DESIGN_REVIEWER_FORBIDDEN", "reviewer scope does not permit this action"
        )
    return ReviewerContext(principal_id=principal_id, policy_digest=policy_digest)
