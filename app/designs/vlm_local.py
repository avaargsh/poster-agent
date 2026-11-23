"""Pinned, opt-in local Ollama visual critique (never a release authority).

The transport is deliberately fixed to 127.0.0.1:11434. Caller input cannot
choose a URL, hostname, API key or proxy. The local server's digest assertion
is only operator-side evidence, not independent model provenance.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from dataclasses import dataclass
from typing import Annotated

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .visual_critic import QualityFinding

_OLLAMA_BASE = "http://127.0.0.1:11434"
_MODEL_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}(:[a-zA-Z0-9][a-zA-Z0-9._-]{0,63})?$")
_DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_MAX_TAGS_BYTES = 65536
_MAX_CHAT_BYTES = 32768
_MAX_INPUT_BYTES = 4 * 1024 * 1024
_MAX_OUTPUT_TOKENS = 512
_MODEL_TIMEOUT_SECONDS = 60.0

_SYSTEM_PROMPT = (
    "You are a conservative visual reviewer for an ecommerce poster. "
    "Return exactly one JSON object containing 'findings' (array, at most 20). "
    "Each finding must have finding_id (unique, lowercase alphanumeric plus '-' or '_'), "
    "category (contrast|spacing|visual_hierarchy|product_visibility|text_legibility|brand_style), "
    "severity (info|minor|major), confidence (number 0..1), explanation (short, single line), "
    "suggested_action (NONE|REVIEW_LAYOUT|REVIEW_STYLE|REVIEW_BACKGROUND), "
    "and optionally region {x,y,width,height} normalized 0..1 entirely on canvas. "
    "Only report perceptible soft visual layout/style concerns. "
    "Never assert OCR correctness, SKU fidelity, product identity, pricing, legal compliance, "
    "permissions, acceptance, repair authorization or release readiness. "
    "Do not rewrite text or suggest changes to prices, SKU, logo or factual claims. "
    'If uncertain or nothing stands out, return {"findings":[]}.'
)


class VLMModelProfileError(RuntimeError):
    """An operator-defined local model/digest is missing or malformed."""


class VLMProviderUnavailable(RuntimeError):
    """Local Ollama endpoint or its claimed pinned model cannot be used."""


@dataclass(frozen=True)
class VLMModelProfile:
    model: str
    digest: str
    fingerprint: str


class _FindingEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    findings: Annotated[list[QualityFinding], Field(max_length=20)]


def _unique_keys(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _finite_json(raw: str):
    return json.loads(
        raw,
        object_pairs_hook=_unique_keys,
        parse_constant=lambda value: (_ for _ in ()).throw(ValueError("non-finite JSON")),
    )


def load_vlm_profile(raw: str | None) -> VLMModelProfile:
    if not isinstance(raw, str) or len(raw) > 1024:
        raise VLMModelProfileError("local model profile is required")
    try:
        cfg = _finite_json(raw)
    except (TypeError, ValueError):
        raise VLMModelProfileError("invalid local model profile") from None
    if (
        not isinstance(cfg, dict)
        or set(cfg) != {"schema_version", "model", "digest"}
        or type(cfg["schema_version"]) is not int
        or cfg["schema_version"] != 1
        or not isinstance(cfg["model"], str)
        or _MODEL_PATTERN.fullmatch(cfg["model"]) is None
        or not isinstance(cfg["digest"], str)
        or _DIGEST_PATTERN.fullmatch(cfg["digest"]) is None
    ):
        raise VLMModelProfileError("expected a single pinned local Ollama model")
    canonical = json.dumps(cfg, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return VLMModelProfile(
        model=cfg["model"],
        digest=cfg["digest"],
        fingerprint=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    )


def parse_findings(content: str) -> list[dict]:
    """Treat model output as untrusted: strict schema, size, ID and count bounds."""
    if not isinstance(content, str) or len(content.encode("utf-8")) > _MAX_CHAT_BYTES:
        raise ValueError("VLM output exceeds limit")
    try:
        parsed = _finite_json(content)
        findings = _FindingEnvelope.model_validate(parsed).findings
    except (ValueError, ValidationError, TypeError, RecursionError):
        raise ValueError("invalid visual finding response") from None
    ids = [finding.finding_id for finding in findings]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate finding IDs")
    return [finding.model_dump(mode="json") for finding in findings]


def _bounded_response(
    client: httpx.Client, method: str, path: str, *, limit: int, json_body: dict | None = None
) -> dict:
    """Bound response bytes before JSON parsing. Never follow redirects or use proxies."""
    try:
        with client.stream(
            method, _OLLAMA_BASE + path, json=json_body, timeout=_MODEL_TIMEOUT_SECONDS
        ) as response:
            if response.status_code != 200:
                raise VLMProviderUnavailable("local model endpoint rejected request")
            chunks: list[bytes] = []
            total = 0
            for part in response.iter_bytes():
                total += len(part)
                if total > limit:
                    raise VLMProviderUnavailable("local model response too large")
                chunks.append(part)
        parsed = _finite_json(b"".join(chunks).decode("utf-8"))
        if not isinstance(parsed, dict):
            raise VLMProviderUnavailable("invalid local model response")
        return parsed
    except VLMProviderUnavailable:
        raise
    except (httpx.HTTPError, UnicodeError, ValueError, TypeError):
        raise VLMProviderUnavailable("local model response unavailable") from None


def verify_ollama_model(profile: VLMModelProfile) -> None:
    """Verify the local server's *claimed* model digest before and after invocation."""
    with httpx.Client(trust_env=False, follow_redirects=False) as client:
        listed = _bounded_response(client, "GET", "/api/tags", limit=_MAX_TAGS_BYTES)
    entries = listed.get("models")
    if not isinstance(entries, list) or len(entries) > 128:
        raise VLMProviderUnavailable("local model listing invalid")
    # A matching row does not prove uniqueness. Reject duplicate tags for
    # the pinned name, even when one (or both) claims the expected digest.
    selected = [
        item for item in entries if isinstance(item, dict) and item.get("name") == profile.model
    ]
    if len(selected) != 1 or selected[0].get("digest") != profile.digest:
        raise VLMProviderUnavailable("pinned model digest missing or ambiguous")


def inspect_approved_png(png: bytes, profile: VLMModelProfile) -> list[dict]:
    """Explicit, bounded local /api/chat request; no retry or automatic fallback."""
    if not isinstance(png, bytes) or not 0 < len(png) <= _MAX_INPUT_BYTES:
        raise VLMProviderUnavailable("approved raster exceeds VLM input limit")
    payload = {
        "model": profile.model,
        "stream": False,
        "format": "json",
        "options": {"temperature": 0, "num_predict": _MAX_OUTPUT_TOKENS},
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {
                "role": "user",
                "content": "Review only soft visual composition issues in this approved image.",
                "images": [base64.b64encode(png).decode("ascii")],
            },
        ],
    }
    with httpx.Client(trust_env=False, follow_redirects=False) as client:
        response = _bounded_response(
            client, "POST", "/api/chat", limit=_MAX_CHAT_BYTES, json_body=payload
        )
    if response.get("model") != profile.model:
        raise VLMProviderUnavailable("local model identity changed")
    # Non-streaming Ollama replies must be complete assistant messages.
    # A plausible JSON findings body is not evidence of finished inference.
    if response.get("done") is not True:
        raise VLMProviderUnavailable("local model reply is incomplete")
    message = response.get("message")
    if (
        not isinstance(message, dict)
        or message.get("role") != "assistant"
        or not isinstance(message.get("content"), str)
    ):
        raise VLMProviderUnavailable("missing local assistant findings")
    return parse_findings(message["content"])
