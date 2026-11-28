"""生成无版权外部依赖的教学示例商品与 Logo，并输出示范海报。"""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageFont

from app.compositor import classic_visible_text, compose, local_background
from app.schemas import PosterSpec

spec = PosterSpec(
    title="双十一限时优惠",
    subtitle="智能清洁 · 品质生活",
    price="199",
    original_price="399",
    style="蓝色科技风",
    layout_id="classic",
)


def _is_missing_demo_glyph(font: ImageFont.FreeTypeFont, char: str) -> bool:
    # FreeType substitutes the missing glyph for absent characters. Compare
    # against two Unicode noncharacters rather than assuming an empty mask.
    masks = [font.getmask(value) for value in (char, "\uffff", "\U0010ffff")]
    signatures = [(mask.size, bytes(mask)) for mask in masks]
    return signatures[0] == signatures[1] == signatures[2]


# Match the documented fresh-install workflow without overriding shell values.
load_dotenv(Path(".env"), override=False)

# Here the explicit override is the feature being tested: falling back can
# make a misspelled or unreadable path look as if it worked.
font_path = os.environ.get("POSTER_FONT_PATH")
if font_path:
    try:
        if not Path(font_path).is_file():
            raise OSError("not a regular font file")
        font = ImageFont.truetype(font_path, size=24)
    except OSError:
        print(
            "无法加载 POSTER_FONT_PATH 指定的字体；请检查路径或清空它以使用系统字体。",
            file=sys.stderr,
        )
        raise SystemExit(2) from None
    visible_chars = {
        char for text in classic_visible_text(spec) for char in text if not char.isspace()
    }
    missing = "".join(char for char in sorted(visible_chars) if _is_missing_demo_glyph(font, char))
    if missing:
        print(
            f"POSTER_FONT_PATH 指定的字体缺少示例字符：{missing}。请使用支持这些字符的中文字体。",
            file=sys.stderr,
        )
        raise SystemExit(2)

product = Image.new("RGBA", (500, 680), (0, 0, 0, 0))
draw = ImageDraw.Draw(product)
draw.rounded_rectangle((180, 25, 310, 585), radius=42, fill="#e8f8ff", outline="#9cb6ca", width=8)
draw.rounded_rectangle((210, 68, 280, 90), radius=10, fill="#2295d9")
draw.rounded_rectangle((218, 310, 272, 316), radius=3, fill="#20a3d7")
draw.rounded_rectangle((221, 560, 269, 650), radius=20, fill="#adcbdc")
logo = Image.new("RGBA", (310, 95), (0, 0, 0, 0))
ld = ImageDraw.Draw(logo)
ld.rounded_rectangle((0, 8, 78, 85), radius=22, fill="#69cbf8")
ld.ellipse((18, 28, 60, 67), fill="#163c7d")
preview = compose(spec, product, logo, local_background(spec.style))
out = Path("data/examples")
product_path = out / "sample_product.png"
logo_path = out / "sample_logo.png"
preview_path = out / "sample_preview.png"
try:
    out.mkdir(parents=True, exist_ok=True)
    for path in (product_path, logo_path, preview_path):
        if os.path.lexists(path) and (path.is_symlink() or not path.is_file()):
            raise OSError(f"{path} 不是可替换的普通文件")
    product.save(product_path)
    logo.save(logo_path)
    preview_path.write_bytes(preview)
except OSError as exc:
    print(f"无法写入示例图片：{exc}。请检查 data/examples 下的路径和写入权限。", file=sys.stderr)
    raise SystemExit(2) from None
print("已生成 data/examples/sample_product.png, sample_logo.png, sample_preview.png")
