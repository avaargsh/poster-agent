# Optional explicit CTA on new DesignDocument snapshots

The original `square-promo-v1` template creates only a title, product and
price. Existing designs are not rewritten. On new documents, callers may
optionally supply `cta` in the multipart `POST /api/designs` form:

```text
product=<uploaded image file>
title=双十一牙刷
price=199.90
cta=立即抢购
```

The server validates the supplied CTA (nonempty, XML-safe single line,
12-character maximum) and creates one bounded `TextNode(role="cta")`
within the 1024×1024 template below the price region. The deterministic
template ID includes CTA **only when it is explicitly provided**, preserving
the old three/four-node template output for omitted CTA. Repository-created
documents continue to use unique document IDs.

The CTA is part of the exact DesignDocument snapshot, so existing approval
SHA-256, approved SVG and OCR target bind it automatically. A supplied CTA
appears in `expected_fields.cta` and is no longer reported as missing. An
omitted CTA remains `missing_fields=["cta"]`, **not** an invented default.
Neither case proves OCR quality or permits release.

When the approved Brief Review Run contains a confirmed CTA and the
DesignDocument has an **explicit** CTA, the Brief-link operation rejects a
mismatch. Legacy designs with no CTA can still preserve their historical
title/price Brief provenance, but are not thereby certified to contain CTA;
the OCR target must continue to report the gap.

The fixed template position is a deterministic prototype layout, not
proof of actual pixel-level legibility. Before publishing, a trusted,
version-bound rasterization and genuine OCR run must validate title, price,
CTA visibility, clipping, contrast and other quality constraints.
