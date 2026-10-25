"""Limit paid compatible-image API response bytes before JSON decoding.

These checks use a mock transport. No real model or billable request occurs.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from app import image_provider


def _mock_client(monkeypatch, handler):
    original = httpx.AsyncClient
    monkeypatch.setattr(
        image_provider.httpx,
        "AsyncClient",
        lambda *args, **kwargs: original(
            *args, transport=httpx.MockTransport(handler), **kwargs
        ),
    )


def _provider_env(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "fixture-key")
    monkeypatch.setenv(
        "QWEN_IMAGE_ENDPOINT",
        "https://workspace.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/images/generations",
    )


def test_oversized_compatible_json_stops_stream_before_remaining_chunks(monkeypatch):
    """The entire JSON body must be capped before a huge Base64 field is loaded."""
    _provider_env(monkeypatch)
    monkeypatch.setattr(image_provider, "MAX_IMAGE_RESPONSE_BYTES", 96)
    received_chunks = []

    class OversizedBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            received_chunks.append("prefix")
            yield b'{"data":[{"b64_json":"' + b"A" * 30
            received_chunks.append("over-limit")
            yield b"A" * 60
            received_chunks.append("unwanted-third-chunk")
            raise AssertionError("client read past the response size cap")

    def handler(request):
        assert request.method == "POST"
        return httpx.Response(200, stream=OversizedBody())

    _mock_client(monkeypatch, handler)
    with pytest.raises(image_provider.ImageProviderError, match="模型响应.*过大"):
        asyncio.run(image_provider.generate_ai_background("蓝色科技风"))
    assert received_chunks == ["prefix", "over-limit"]


def test_response_size_limit_applies_to_json_metadata_not_only_image_bytes(monkeypatch):
    """A small image URL cannot make an oversized response body acceptable."""
    _provider_env(monkeypatch)
    monkeypatch.setattr(image_provider, "MAX_IMAGE_RESPONSE_BYTES", 150)
    calls = []

    def handler(request):
        calls.append(request.method)
        return httpx.Response(
            200,
            json={"data": [{"url": "https://safe.aliyuncs.com/demo.png"}],
                  "padding": "A" * 500},
        )

    _mock_client(monkeypatch, handler)
    with pytest.raises(image_provider.ImageProviderError, match="模型响应.*过大"):
        asyncio.run(image_provider.generate_ai_background("蓝色科技风"))
    assert calls == ["POST"]
