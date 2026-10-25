import pytest
import asyncio
import httpx
from app.image_provider import generate_ai_background, ImageProviderError, make_prompt


def test_provider_prompt_no_text():
    prompt = make_prompt('蓝色科技风')
    assert 'No text' in prompt
    assert '蓝色科技风' in prompt


def test_bad_domain_rejected(monkeypatch):
    monkeypatch.setenv('DASHSCOPE_API_KEY', 'some-key')
    monkeypatch.setenv('QWEN_IMAGE_ENDPOINT', 'http://127.0.0.1/images/generations')
    with pytest.raises(ImageProviderError):
        asyncio.run(generate_ai_background('简约'))


def test_qwen_compatible_response_with_mock_http(monkeypatch):
    """不消耗额度，但走官方 compatible-mode 协议及结果下载解析。"""
    import io
    from PIL import Image
    monkeypatch.setenv('DASHSCOPE_API_KEY', 'test-key')
    monkeypatch.setenv('QWEN_IMAGE_ENDPOINT',
                       'https://demo.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/images/generations')
    png = io.BytesIO()
    Image.new('RGB', (1024, 1024), '#204060').save(png, 'PNG')
    original = httpx.AsyncClient
    def handler(request: httpx.Request):
        if request.method == 'POST':
            assert request.headers['Authorization'] == 'Bearer test-key'
            import json
            data = json.loads(request.content)
            assert data['model'] == 'qwen-image-3.0'
            assert data['size'] == '1024x1024'
            assert 'No text' in data['prompt']
            return httpx.Response(200, json={'data': [{'url':
                'https://dashscope-result-sz.oss-cn-shenzhen.aliyuncs.com/demo.png'}]})
        return httpx.Response(200, content=png.getvalue(), headers={'content-type': 'image/png'})
    def mock_client(*args, **kwargs):
        return original(*args, transport=httpx.MockTransport(handler), **kwargs)
    monkeypatch.setattr('app.image_provider.httpx.AsyncClient', mock_client)
    result = asyncio.run(generate_ai_background('蓝色科技风'))
    assert result.size == (1024, 1024)


@pytest.mark.parametrize("image_format", ["PNG", "JPEG", "WEBP"])
def test_compatible_provider_decodes_inline_base64_images_without_second_http_call(
    monkeypatch, image_format
):
    """EcomGen-style compatible image responses can contain b64_json instead of a URL."""
    import base64
    import io
    from PIL import Image

    monkeypatch.setenv("DASHSCOPE_API_KEY", "fixture-key")
    monkeypatch.setenv(
        "QWEN_IMAGE_ENDPOINT",
        "https://workspace.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/images/generations",
    )
    encoded_image = io.BytesIO()
    Image.new("RGB", (64, 64), "#204060").save(encoded_image, format=image_format)
    encoded = base64.b64encode(encoded_image.getvalue()).decode("ascii")
    requests = []
    original_client = httpx.AsyncClient

    def handler(request):
        requests.append(request)
        assert request.method == "POST"
        return httpx.Response(
            200,
            json={"data": [{
                "b64_json": encoded,
                "url": "https://evil.example/must-not-fetch.png",
            }]},
        )

    def mock_client(*args, **kwargs):
        return original_client(*args, transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr("app.image_provider.httpx.AsyncClient", mock_client)
    result = asyncio.run(generate_ai_background("蓝色科技风"))
    assert result.mode == "RGB"
    assert result.size == (64, 64)
    assert len(requests) == 1


@pytest.mark.parametrize("invalid_value", ["not-valid-base64!", "bm90IGFuIGltYWdl", 123, ""])
def test_compatible_provider_rejects_invalid_inline_image_without_url_fallback(
    monkeypatch, invalid_value
):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "fixture-key")
    monkeypatch.setenv(
        "QWEN_IMAGE_ENDPOINT",
        "https://workspace.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/images/generations",
    )
    requests = []
    original_client = httpx.AsyncClient

    def handler(request):
        requests.append(request)
        assert request.method == "POST"
        return httpx.Response(
            200,
            json={"data": [{
                "b64_json": invalid_value,
                "url": "https://cdn.aliyuncs.com/should-not-fallback.png",
            }]},
        )

    def mock_client(*args, **kwargs):
        return original_client(*args, transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr("app.image_provider.httpx.AsyncClient", mock_client)
    with pytest.raises(ImageProviderError):
        asyncio.run(generate_ai_background("蓝色科技风"))
    assert len(requests) == 1


def test_compatible_provider_bounds_inline_image_before_decoding(monkeypatch):
    import app.image_provider as provider

    monkeypatch.setenv("DASHSCOPE_API_KEY", "fixture-key")
    monkeypatch.setenv(
        "QWEN_IMAGE_ENDPOINT",
        "https://workspace.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/images/generations",
    )
    monkeypatch.setattr(provider, "MAX_GENERATED_IMAGE_BYTES", 4)
    requests = []
    original_client = httpx.AsyncClient

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"data": [{"b64_json": "A" * 64}]})

    monkeypatch.setattr("app.image_provider.httpx.AsyncClient", lambda *a, **kw:
                        original_client(*a, transport=httpx.MockTransport(handler), **kw))
    with pytest.raises(ImageProviderError):
        asyncio.run(generate_ai_background("蓝色科技风"))
    assert len(requests) == 1


def test_compatible_provider_rejects_unsupported_inline_image_format(monkeypatch):
    import base64
    import io
    from PIL import Image

    monkeypatch.setenv("DASHSCOPE_API_KEY", "fixture-key")
    monkeypatch.setenv(
        "QWEN_IMAGE_ENDPOINT",
        "https://workspace.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/images/generations",
    )
    buffer = io.BytesIO()
    Image.new("RGB", (64, 64)).save(buffer, format="TIFF")
    payload = base64.b64encode(buffer.getvalue()).decode("ascii")
    original_client = httpx.AsyncClient
    monkeypatch.setattr(
        "app.image_provider.httpx.AsyncClient",
        lambda *a, **kw: original_client(
            *a, transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json={"data": [{"b64_json": payload}]})
            ), **kw
        ),
    )
    with pytest.raises(ImageProviderError):
        asyncio.run(generate_ai_background("蓝色科技风"))


def test_failed_paid_provider_request_is_not_retried_by_transport(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "fixture-key")
    monkeypatch.setenv(
        "QWEN_IMAGE_ENDPOINT",
        "https://workspace.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/images/generations",
    )
    requests = []
    original_client = httpx.AsyncClient

    def handler(request):
        requests.append(request)
        return httpx.Response(503, text="upstream unavailable")

    monkeypatch.setattr("app.image_provider.httpx.AsyncClient", lambda *a, **kw:
                        original_client(*a, transport=httpx.MockTransport(handler), **kw))
    with pytest.raises(ImageProviderError):
        asyncio.run(generate_ai_background("蓝色科技风"))
    assert len(requests) == 1
