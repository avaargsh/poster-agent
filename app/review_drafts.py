"""Mutable, source-bound progress; isolated from append-only review records."""
from __future__ import annotations

import hashlib
import json
import os
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from . import human_annotations, native_human_reviews, storage

ReviewVariant = Literal["square", "feed_4x5_native", "feed_4x5_native_product_focus"]
MAX_DRAFT_BYTES = 16 * 1024


class DraftConflict(ValueError):
    pass


class DraftField(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    judgment: Literal["", "correct", "defective", "unsure"] = ""
    reason: str = Field(default="", max_length=300)


class SaveDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_revision: int = Field(ge=0)
    fields: dict[str, DraftField] = Field(max_length=8)


class _StoredDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: Literal["review-draft-1"] = "review-draft-1"
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    revision: int = Field(ge=1)
    saved_at: datetime
    fields: dict[str, DraftField] = Field(max_length=8)


def _source(poster_id: str, version: int, variant: ReviewVariant, reviewer: str):
    poster = storage.poster_dir(poster_id)
    if poster.is_symlink() or (poster / "versions").is_symlink():
        raise DraftConflict("海报存储目录无效，已有进度未改动")
    if variant == "square":
        context = human_annotations.get_context(poster_id, version, reviewer)
    else:
        context = native_human_reviews.context(poster_id, version, reviewer, variant=variant)
    identity = {
        "poster_id": poster_id, "version": version, "variant": variant, "reviewer": reviewer,
        "image_sha256": context["image_sha256"], "spec_sha256": context["spec_sha256"],
        "source_image_sha256": context.get("source_image_sha256"),
        "asset_sha256": context.get("asset_sha256"),
        # A later formal submission starts a fresh progress namespace. Older
        # partial work remains on disk and cannot mask the submitted labels.
        "base_review_id": (context.get("existing_review") or {}).get("review_id"),
    }
    source = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    folder = poster / "versions" / f"v{version}.{variant}.review_drafts"
    if folder.is_symlink() or (folder.exists() and not folder.is_dir()):
        raise DraftConflict("草稿目录无效，已有进度未改动")
    return context, source, folder / f"{reviewer}-{source}.json"


def _read(path: Path, source: str, allowed: set[str]) -> _StoredDraft | None:
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise DraftConflict("无法安全读取草稿，已有进度未改动") from exc
    with os.fdopen(fd, "rb") as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            raise DraftConflict("草稿不是普通文件，已有进度未改动")
        raw = handle.read(MAX_DRAFT_BYTES + 1)
    if not raw or len(raw) > MAX_DRAFT_BYTES:
        raise DraftConflict("草稿文件为空或过大，已有进度未改动")
    try:
        record = _StoredDraft.model_validate_json(raw)
    except ValidationError as exc:
        raise DraftConflict("草稿文件损坏，已有进度未改动") from exc
    if record.source_sha256 != source or not set(record.fields) <= allowed:
        raise DraftConflict("草稿与当前图片或字段不一致，已有进度未改动")
    return record


def _response(context, source, record):
    return {
        "schema_version": "review-draft-1", "status": "DRAFT",
        "source_sha256": source, "revision": record.revision if record else 0,
        "saved_at": record.saved_at.isoformat() if record else None,
        "fields": {key: value.model_dump() for key, value in record.fields.items()} if record else {},
        "fields_to_review": context["fields_to_review"],
        "image_sha256": context["image_sha256"], "spec_sha256": context["spec_sha256"],
        "base_review_id": (context.get("existing_review") or {}).get("review_id"),
        "automatically_accepted": False,
    }


def load(poster_id: str, version: int, variant: ReviewVariant, reviewer: str) -> dict:
    with storage.LOCK:
        context, source, path = _source(poster_id, version, variant, reviewer)
        return _response(context, source, _read(path, source, set(context["fields_to_review"])))


def save(poster_id: str, version: int, variant: ReviewVariant, reviewer: str, payload: SaveDraft) -> dict:
    with storage.LOCK:
        context, source, path = _source(poster_id, version, variant, reviewer)
        if source != payload.source_sha256:
            raise DraftConflict("图片、审核员或正式审核已变化，请重新载入；当前输入不会覆盖已保存进度")
        allowed = set(context["fields_to_review"])
        if not set(payload.fields) <= allowed:
            raise ValueError("草稿含当前版式不适用的审核字段")
        previous = _read(path, source, allowed)
        revision = previous.revision if previous else 0
        # A response may be lost after the file is committed. A retry of the
        # exact current content is safe and must not create a false conflict.
        if previous and payload.expected_revision <= revision and payload.fields == previous.fields:
            return _response(context, source, previous)
        if payload.expected_revision != revision:
            raise DraftConflict("另一个页面已更新这份进度；当前输入已保留，请先载入已保存版本")
        record = _StoredDraft(source_sha256=source, revision=revision + 1,
                              saved_at=datetime.now(timezone.utc), fields=payload.fields)
        storage._atomic_json(path, record.model_dump(mode="json"))
        return _response(context, source, record)
