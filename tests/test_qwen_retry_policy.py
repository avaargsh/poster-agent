"""Qwen Brief extraction must not issue an implicit second billable request."""

import asyncio

import httpx
import pytest

from app.brief_compiler import BriefCompilerError, _qwen_extract


@pytest.mark.parametrize("http_status", [429, 503])
def test_uncertain_qwen_response_is_not_automatically_retried(monkeypatch, http_status):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "fake-key")
    monkeypatch.setenv(
        "QWEN_CHAT_ENDPOINT",
        "https://demo.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/chat/completions",
    )
    hits = {"n": 0}

    def handler(request):
        hits["n"] += 1
        return httpx.Response(http_status, json={"message": "provider unavailable"})

    base_client = httpx.AsyncClient
    monkeypatch.setattr(
        "app.brief_compiler.httpx.AsyncClient",
        lambda **kwargs: base_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    with pytest.raises(BriefCompilerError):
        asyncio.run(_qwen_extract("给商品生成海报，主标题是科技优惠"))
    assert hits["n"] == 1
