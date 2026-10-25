# Integrate upstream modules without changing the evidence contract

This document records which upstream modules were inspected on 2026-10-25, which
code was adapted, and what needs separate validation. Refer to each upstream
project's license and pinned commit before bringing in further code.

## Shipped in this change: compatible image response parsing

`app/image_provider.py` borrows the byte-signature format detection and inline
`b64_json` response handling approach from EcomGen's
[`packages/providers/src/openai-compatible.ts`](https://github.com/linbei0/EcomGen/blob/966a7c0ec57dad766ecc3be9edaa07033ee78b12/packages/providers/src/openai-compatible.ts).
See [third-party notices](../THIRD_PARTY_NOTICES.md) for its MIT license and
copyright notice.

The public interface does not change:

```python
from app.image_provider import generate_ai_background

# The caller must already have claimed an Idempotency-Key for a paid request.
background = await generate_ai_background("蓝色科技风")
```

The existing Qwen compatible-mode endpoint can return either `data[0].url`
or `data[0].b64_json`. In both cases the image is decoded with Pillow into
RGB, then the existing composer overlays the **original** SKU, Logo and exact
promotion text. The provider does not redraw the SKU or set business prices.

For `b64_json`, the adapter checks encoded length before allocating image
bytes, validates Base64, identifies PNG/JPEG/WEBP by signature, validates actual
Pillow format, and bounds image size and decoded pixels. When both image URL
and Base64 are present, Base64 has priority. Invalid Base64 fails closed; it
does not trigger a second download or model request.

The compatible-mode **JSON response body** is also capped before parsing:
`httpx.AsyncClient.stream` stops reading if the decoded HTTP payload exceeds
`MAX_IMAGE_RESPONSE_BYTES`. The default is the maximum accepted image bytes
after Base64 expansion plus 1 MiB for JSON metadata. This protects the
response-receive stage, not only the subsequent image decode. The URL download
continues to use its separate `MAX_GENERATED_IMAGE_BYTES` limit.

For `url`, the adapter still accepts only HTTPS `aliyuncs.com` hosts and
their subdomains, with no redirects. It bounds the downloaded bytes and now
applies the same image-format and pixel checks. Untrusted Provider error
details, API keys and signed URLs are not echoed to clients.

**No automatic paid retry.** EcomGen also has a retrying generation transport.
That design was deliberately not copied: Poster Agent marks paid-call failures
as `UNKNOWN` and requires an explicit check before another paid request.
Whether a third-party endpoint honors `Idempotency-Key` cannot be assumed.
The execution ledger and local single-worker safety model remain unchanged.

```bash
uv run --locked python -m pytest tests/test_image_provider.py tests/test_image_provider_response_limit.py -q
uv run --locked python -m pytest -q
uv run --locked ruff check app tests scripts evals
```

The tests use `httpx.MockTransport` and synthetic images, with no external
model calls. A passing CI run validates protocol parsing, not actual Qwen
model availability, billing accuracy, image quality or production readiness.

## Follow-on candidates

| Upstream | Actual module / design | Decision | Reason and next proof |
| --- | --- | --- | --- |
| [EcomGen](https://github.com/linbei0/EcomGen), MIT | `packages/providers/src/openai-compatible.ts` response parser | Adapted in this PR | Keep local strict host allowlist and uncertain-charge policy; do not copy its retry loop |
| [MxPage](https://github.com/ziguishian/MxPage), MIT | `lib/ai/image-protocol.ts` model-to-protocol resolver | Candidate | Add an explicit capability manifest **only when** a second configured provider has offline contract tests; never probe by generating paid images |
| [product-poster-ai](https://github.com/chaochaoji/product-poster-ai), MIT | `product_ai/generation/mapper.py` category/usage mapping | Candidate | Convert marketing goals into a typed draft first, then require existing human approval and deterministic pricing validation |
| [gpt-image2-ecommerce](https://github.com/buluslan/gpt-image2-ecommerce), MIT | `references/campaign-style-lock.md` | Design reference | Lock palette, typography and campaign identity across variants without treating prompt consistency as pixel-level evidence |
| [Paper2Poster](https://github.com/Paper2Poster/Paper2Poster), MIT | `PosterAgent/` iterative layout pipeline | Research only | Academic-poster pipeline and heavy dependencies do not fit the present local ecommerce proof requirements |
| [Posttiva](https://github.com/reema010/Posttiva-Multi-Agent-AI) and [CreatiPoster](https://github.com/graphic-design-ai/creatiposter) | Segmentation and layered editing | Do not copy | No verified explicit license in current repository metadata; need permission and feasibility review first |
| [poster-design](https://github.com/palxiao/poster-design), AGPL-3.0 | Browser design editor | Do not copy | Different license obligations and frontend stack; not appropriate for this narrow Python change |

## Next feature in job-interview terms

Build an optional `CampaignPlan` draft for at least two variants that shares
a stable style fingerprint and a frozen SKU identity. The existing approval
workflow remains the gate before any paid model call. Each variant receives
its own final image hash, OCR report, reviewer decision and evaluated model
identity. Reuse the existing 4:5 layout and batch journal instead of making a
second storage or scoring system.

Evidence needed before describing this as an Agent Platform capability:

1. Input validation and intent-to-plan mapping, without hallucinated price.
2. Human-approved plan, stable version and campaign style identity.
3. Replayable provider adapter contract tests, including cancellation and
   `UNKNOWN` outcomes without silent paid retry.
4. Two complete SKU/variant outputs with separate OCR and human evidence;
   the required 20 independently reviewed, authorized real SKUs remain pending.

This is a local Demo and interview engineering artifact, not a production
multi-tenant platform or evidence of achieved real-business quality.
