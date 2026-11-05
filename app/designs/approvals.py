"""Versioned, exact-snapshot approval evidence for local design documents.

An approval is an explicit local operator acknowledgement, not an authenticated
identity or an EngageGuard Permit. It does not authorize external side effects.
"""

from __future__ import annotations

import hashlib
import json
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from .models import RENDERER_VERSION, DesignDocument

ApprovalId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{8,128}$")]
Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class ApprovalRequest(BaseModel):
    """Client must submit the exact target snapshot it explicitly reviewed."""

    model_config = ConfigDict(extra="forbid")

    approval_id: ApprovalId
    expected_revision: Annotated[int, Field(gt=0, strict=True)]
    spec_digest: Digest
    asset_manifest_digest: Digest


def _sha256(payload: object) -> str:
    serialized = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def snapshot_digests(document: DesignDocument) -> tuple[str, str]:
    """Bind full design semantics and immutable indexed asset references.

    Revision is part of both digests. Renderer version belongs to the spec
    hash, so a changed renderer cannot silently reuse a previous approval.
    Asset bytes are verified against their SHA-256 references separately.
    """
    payload = document.model_dump(mode="json")
    assets = payload.pop("asset_refs")
    spec_digest = _sha256({"renderer_version": RENDERER_VERSION, "document": payload})
    manifest_digest = _sha256(
        {
            "document_id": document.document_id,
            "revision": document.revision,
            "asset_refs": sorted(assets, key=lambda item: item["asset_id"]),
        }
    )
    return spec_digest, manifest_digest
