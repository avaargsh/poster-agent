"""Deterministic local cutout diagnostics for offline M6 human review.

Machine metrics are triage signals, never automatic product quality PASS.
Operator-approved / genuine ONNX inference provenance is NOT inferred from a
saved transparent PNG. A saved mask may have been externally supplied.
"""
from __future__ import annotations

import hashlib
import io

from PIL import Image, ImageDraw, ImageOps

SCHEMA = "m6-cutout-evidence-1"
REVIEW_FIELDS = (
    "cohort_id", "sku_id", "poster_id", "source_version",
    "source_product_sha256", "cutout_sha256",
    "comparison_image_path", "comparison_image_sha256",
    "foreground_fraction", "soft_alpha_fraction", "edge_touch_fraction",
    "component_count", "retained_rgb_mae",
    "machine_risks", "candidate_origin", "model_sha256", "model_runtime_attested",
    "quality_state", "subject_integrity_review", "edge_artifact_review",
    "brand_mark_review", "reviewer_alias", "human_accept",
    "reviewer_notes", "reviewed_at_utc",
)


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _checkerboard(size: tuple[int, int]) -> Image.Image:
    result = Image.new("RGB", size, "#eeeeee")
    draw = ImageDraw.Draw(result)
    for y in range(0, size[1], 16):
        for x in range(0, size[0], 16):
            if ((x // 16) + (y // 16)) % 2:
                draw.rectangle(
                    (x, y, min(x + 15, size[0] - 1),
                     min(y + 15, size[1] - 1)),
                    fill="#bbbbbb",
                )
    return result


def comparison_png(original: Image.Image, cutout: Image.Image) -> bytes:
    """Two equally scaled panels: LEFT source, RIGHT alpha against checkerboard."""
    if original.size != cutout.size:
        raise ValueError("抠图与原图尺寸不匹配")
    canvas = Image.new("RGB", (1024, 512), "white")
    for x, candidate in ((0, original), (512, cutout)):
        panel = _checkerboard((512, 512))
        scaled = ImageOps.contain(candidate.convert("RGBA"), (480, 480),
                                  method=Image.Resampling.LANCZOS)
        panel.paste(scaled, ((512 - scaled.width) // 2,
                             (512 - scaled.height) // 2), scaled)
        canvas.paste(panel, (x, 0))
    output = io.BytesIO()
    canvas.save(output, format="PNG", optimize=True)
    return output.getvalue()


def _components(mask: Image.Image) -> int:
    """Count 4-connected components at fixed, bounded 64px granularity."""
    small = mask.resize((64, 64), resample=Image.Resampling.NEAREST)
    values = small.tobytes()
    visited = bytearray(4096)
    count = 0
    for start, val in enumerate(values):
        if val <= 127 or visited[start]:
            continue
        count += 1
        pending = [start]
        visited[start] = 1
        while pending:
            i = pending.pop()
            x, y = i % 64, i // 64
            for neighbor in (
                i - 1 if x else -1,
                i + 1 if x < 63 else -1,
                i - 64 if y else -1,
                i + 64 if y < 63 else -1,
            ):
                if neighbor >= 0 and not visited[neighbor] and values[neighbor] > 127:
                    visited[neighbor] = 1
                    pending.append(neighbor)
    return count


def inspect_cutout(original: Image.Image, cutout: Image.Image) -> dict:
    """Compute bounded pixel metrics and review priorities, without judging fidelity."""
    if original.size != cutout.size:
        raise ValueError("抠图候选尺寸与原图不一致")
    if original.width < 64 or original.height < 64:
        raise ValueError("输入图像过小")
    source = original.convert("RGBA")
    subject = cutout.convert("RGBA")
    # Always sample to a bounded raster: 256px maximum edge, no heavy numpy.
    sample = (max(1, round(original.width * min(1, 256 / max(original.size)))),
              max(1, round(original.height * min(1, 256 / max(original.size)))))
    src = source.resize(sample, Image.Resampling.BOX)
    fg = subject.resize(sample, Image.Resampling.BOX)
    alpha = fg.getchannel("A")
    values = alpha.tobytes()
    count = len(values)
    present = sum(value > 127 for value in values)
    soft = sum(0 < value < 255 for value in values)
    # Count alpha crossings along a 2-pixel frame on the fixed sampled image.
    w, h = sample
    edge = [
        values[y * w + x]
        for y in range(h) for x in range(w)
        if x < 2 or y < 2 or x >= w - 2 or y >= h - 2
    ]
    touches = sum(v > 127 for v in edge) / len(edge)
    # Compare visible RGB against source RGB only where retained mask exists.
    src_bytes = src.tobytes()
    fg_bytes = fg.tobytes()
    total_diff, retained = 0, 0
    for i, a in enumerate(values):
        if a > 127:
            j = 4 * i
            total_diff += sum(abs(src_bytes[j + k] - fg_bytes[j + k]) for k in range(3))
            retained += 1
    mae = (total_diff / (3 * retained)) if retained else None
    coverage = present / count
    soft_fraction = soft / count
    components = _components(alpha)
    risks: list[str] = []
    if coverage < 0.04:
        risks.append("LOW_FOREGROUND_COVERAGE")
    if coverage > 0.90:
        risks.append("HIGH_FOREGROUND_COVERAGE")
    if touches > 0.05:
        risks.append("SUBJECT_TOUCHES_FRAME")
    if components > 4:
        risks.append("FRAGMENTED_SUBJECT")
    if soft_fraction > 0.25:
        risks.append("LARGE_SOFT_ALPHA_REGION")
    if mae is not None and mae > 18:
        risks.append("RETAINED_FOREGROUND_COLOR_SHIFT")
    return {
        "schema_version": SCHEMA,
        "pixel_sampling": list(sample),
        "foreground_fraction": round(coverage, 6),
        "soft_alpha_fraction": round(soft_fraction, 6),
        "edge_touch_fraction": round(touches, 6),
        "component_count": components,
        "retained_rgb_mae": round(mae, 3) if mae is not None else None,
        "machine_risks": risks,
        "automatically_accepted": False,
        "requires_human_review": True,
        "candidate_origin": "USER_SUPPLIED_OR_PREVIEW_NOT_ATTESTED",
        "model_sha256": None,
        "model_runtime_attested": False,
    }


def evidence_from_pngs(original_raw: bytes, cutout_raw: bytes) -> tuple[dict, bytes]:
    """Calculate evidence from existing local artifacts, no model execution."""
    with Image.open(io.BytesIO(original_raw)) as original, Image.open(io.BytesIO(cutout_raw)) as cutout:
        original.load()
        cutout.load()
        if original.format != "PNG" or cutout.format != "PNG":
            raise ValueError("抠图证据必须从已保存 PNG 读取")
        if cutout.mode != "RGBA":
            raise ValueError("抠图证据缺少透明通道")
        from .cutout_service import verify_candidate
        verify_candidate(original, cutout)
        metrics = inspect_cutout(original, cutout)
        picture = comparison_png(original, cutout)
    return {
        **metrics,
        "source_png_sha256": _sha(original_raw),
        "cutout_png_sha256": _sha(cutout_raw),
        "comparison_png_sha256": _sha(picture),
    }, picture
