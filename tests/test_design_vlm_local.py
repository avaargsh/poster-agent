"""Local Ollama adapter protocol contracts (no network or model downloads)."""

import base64
import json

import httpx
import pytest

import app.designs.vlm_local as local

DIGEST = "sha256:" + "d" * 64


def _profile():
    return local.load_vlm_profile(
        json.dumps({"schema_version": 1, "model": "llava:latest", "digest": DIGEST})
    )


def _install_httpx_transport(monkeypatch, handler):
    real_client = httpx.Client

    def local_client(**kwargs):
        assert kwargs["trust_env"] is False
        assert kwargs["follow_redirects"] is False
        return real_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(local.httpx, "Client", local_client)


def test_fixed_loopback_tags_and_bounded_ollama_chat(monkeypatch):
    requests = []

    def handler(request):
        requests.append(request)
        assert request.url.host == "127.0.0.1"
        assert request.url.port == 11434
        if request.url.path == "/api/tags":
            assert request.method == "GET"
            return httpx.Response(
                200, json={"models": [{"name": "llava:latest", "digest": DIGEST}]}
            )
        assert request.url.path == "/api/chat"
        assert request.method == "POST"
        body = json.loads(request.content)
        assert body["model"] == "llava:latest"
        assert body["stream"] is False
        assert body["format"] == "json"
        assert body["options"]["num_predict"] <= 512
        assert base64.b64decode(body["messages"][1]["images"][0]) == b"synthetic-png"
        return httpx.Response(
            200, json={"model": "llava:latest", "message": {"content": '{"findings":[]}'}}
        )

    _install_httpx_transport(monkeypatch, handler)
    local.verify_ollama_model(_profile())
    assert local.inspect_approved_png(b"synthetic-png", _profile()) == []
    assert len(requests) == 2


@pytest.mark.parametrize(
    "listing",
    [
        {"models": [{"name": "llava:latest", "digest": "sha256:" + "a" * 64}]},
        {"models": []},
        {"models": {"name": "llava:latest", "digest": DIGEST}},
        {"models": [{}] * 129},
    ],
)
def test_model_identity_must_match_exact_digest(monkeypatch, listing):
    _install_httpx_transport(monkeypatch, lambda request: httpx.Response(200, json=listing))
    with pytest.raises(local.VLMProviderUnavailable):
        local.verify_ollama_model(_profile())


def test_redirects_oversize_and_claimed_model_mismatch_fail_closed(monkeypatch):
    _install_httpx_transport(monkeypatch, lambda request: httpx.Response(302, headers={"location": "https://example.org"}))
    with pytest.raises(local.VLMProviderUnavailable):
        local.verify_ollama_model(_profile())

    _install_httpx_transport(
        monkeypatch, lambda request: httpx.Response(200, content=b"x" * 66000)
    )
    with pytest.raises(local.VLMProviderUnavailable):
        local.verify_ollama_model(_profile())

    _install_httpx_transport(
        monkeypatch,
        lambda request: httpx.Response(
            200, json={"model": "unapproved", "message": {"content": '{"findings":[]}'}}
        ),
    )
    with pytest.raises(local.VLMProviderUnavailable):
        local.inspect_approved_png(b"small", _profile())

    with pytest.raises(local.VLMProviderUnavailable):
        local.inspect_approved_png(b"x" * (4 * 1024 * 1024 + 1), _profile())


def test_duplicate_json_keys_and_untrusted_claims_do_not_parse():
    for content in (
        '{"findings":[],"findings":[]}',
        '{"findings":[{"finding_id":"ok","category":"contrast","severity":"major",'
        '"confidence":0.6,"explanation":"bad","suggested_action":"REVIEW_STYLE",'
        '"repair_authorized":true}]}',
        '{"findings":[{"finding_id":"a","category":"contrast","severity":"major",'
        '"confidence":0.6,"explanation":"bad","suggested_action":"REVIEW_STYLE"},'
        '{"finding_id":"a","category":"spacing","severity":"major",'
        '"confidence":0.6,"explanation":"bad","suggested_action":"REVIEW_LAYOUT"}]}',
        '{"findings":[],"quality_verified":true}',
        '{"findings":[],"release_ready":true}',
    ):
        with pytest.raises(ValueError):
            local.parse_findings(content)
