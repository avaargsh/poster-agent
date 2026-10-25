"""Qwen 图像生成功能：只生成背景，不生成价格或 Logo。"""
from __future__ import annotations
import base64
import binascii
import io
import os
import warnings
from urllib.parse import urlparse
import httpx
from PIL import Image, ImageOps, UnidentifiedImageError

MAX_GENERATED_IMAGE_BYTES = 14 * 1024 * 1024


class ImageProviderError(RuntimeError):
    pass


def _allowed_provider_url(url: str) -> bool:
    p = urlparse(url)
    host = p.hostname or ""
    return p.scheme == "https" and (host == "aliyuncs.com" or host.endswith(".aliyuncs.com"))


def make_prompt(style: str) -> str:
    return ("Square luxury ecommerce promotional visual BACKGROUND ONLY. "
            "No text, no numbers, no logos, no product, no typography, no people. "
            "Clear negative space and soft lighting, pleasant gradient, premium polished look. "
            "Avoid busy details. Visual mood: " + style)



# Adapted from linbei0/EcomGen (MIT), packages/providers/src/openai-compatible.ts:
# read image signatures rather than trusting response MIME metadata.
# See THIRD_PARTY_NOTICES.md for upstream source and license.
def _image_format(blob: bytes) -> str | None:
    if blob.startswith(b"\x89PNG\r\n\x1a\n"):
        return "PNG"
    if blob.startswith(b"\xff\xd8\xff"):
        return "JPEG"
    if len(blob) >= 12 and blob[:4] == b"RIFF" and blob[8:12] == b"WEBP":
        return "WEBP"
    return None


def _decode_inline_image(encoded: object) -> bytes:
    # Bound Base64 length before allocating decoded bytes.
    if not isinstance(encoded, str) or not encoded:
        raise ImageProviderError("模型响应包含无效的 Base64 图片")
    if len(encoded) > 4 * ((MAX_GENERATED_IMAGE_BYTES + 2) // 3) + 4:
        raise ImageProviderError("模型返回图片过大")
    try:
        blob = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ImageProviderError("模型响应包含无效的 Base64 图片") from exc
    if len(blob) > MAX_GENERATED_IMAGE_BYTES:
        raise ImageProviderError("模型返回图片过大")
    return blob


def _open_image(blob: bytes) -> Image.Image:
    if len(blob) > MAX_GENERATED_IMAGE_BYTES:
        raise ImageProviderError("模型返回图片过大")
    if _image_format(blob) is None:
        raise ImageProviderError("模型返回的图片格式不受支持")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(blob)) as image:
                if image.format not in {"PNG", "JPEG", "WEBP"}:
                    raise ImageProviderError("模型返回的图片格式不受支持")
                width, height = image.size
                if width > 8192 or height > 8192 or width * height > 16_000_000:
                    raise ImageProviderError("模型返回图片像素或边长超限")
                image.load()
                return ImageOps.exif_transpose(image).convert("RGB")
    except (UnidentifiedImageError, OSError,
            Image.DecompressionBombWarning, Image.DecompressionBombError) as exc:
        raise ImageProviderError("模型返回的图片无法安全解码") from exc


async def generate_ai_background(style: str) -> Image.Image:
    key = os.getenv("DASHSCOPE_API_KEY", "")
    endpoint = os.getenv("QWEN_IMAGE_ENDPOINT", "")
    model = os.getenv("QWEN_IMAGE_MODEL", "qwen-image-3.0")
    if not key or not endpoint:
        raise ImageProviderError("AI 模式需要 DASHSCOPE_API_KEY 和 QWEN_IMAGE_ENDPOINT，参见 README")
    if not _allowed_provider_url(endpoint) or not endpoint.endswith("/images/generations"):
        raise ImageProviderError("QWEN_IMAGE_ENDPOINT 必须是阿里云 HTTPS compatible-mode /images/generations 地址")
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    payload = {"model": model, "prompt": make_prompt(style), "size": "1024x1024", "n": 1}
    timeout = httpx.Timeout(300.0, connect=20.0)
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            response = await client.post(endpoint, json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
            items = data.get("data") if isinstance(data, dict) else None
            if not isinstance(items, list) or not items or not isinstance(items[0], dict):
                raise ImageProviderError("模型响应没有有效的图片数据")
            result = items[0]
            # If Base64 is supplied but malformed, never fall back to a URL:
            # an ambiguous payload must not silently change its trusted source.
            if "b64_json" in result:
                blob = _decode_inline_image(result["b64_json"])
            else:
                img_url = result.get("url")
                if not isinstance(img_url, str) or not _allowed_provider_url(img_url):
                    raise ImageProviderError("模型响应图片 URL 不在允许的阿里云域名范围内")
                async with client.stream("GET", img_url) as img_response:
                    img_response.raise_for_status()
                    chunks: list[bytes] = []
                    total = 0
                    async for chunk in img_response.aiter_bytes():
                        total += len(chunk)
                        if total > MAX_GENERATED_IMAGE_BYTES:
                            raise ImageProviderError("模型返回图片过大")
                        chunks.append(chunk)
                blob = b"".join(chunks)
            return _open_image(blob)
    except ImageProviderError:
        raise
    except (httpx.HTTPError, KeyError, IndexError, ValueError, UnidentifiedImageError, OSError) as exc:
        # 不回显完整异常、带签名的图片 URL 或 API 密钥
        raise ImageProviderError("调用图像模型失败，请检查 API Key、Workspace 区域、模型名称和网络连接") from exc
