"""生成无版权外部依赖的教学示例商品与 Logo，并输出示范海报。"""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageFont

from app.compositor import compose, local_background
from app.schemas import PosterSpec

# Match the documented fresh-install workflow without overriding shell values.
load_dotenv(Path(".env"), override=False)

# Here the explicit override is the feature being tested: falling back can
# make a misspelled or unreadable path look as if it worked.
font_path = os.environ.get("POSTER_FONT_PATH")
if font_path:
    try:
        if not Path(font_path).is_file():
            raise OSError("not a regular font file")
        ImageFont.truetype(font_path, size=24)
    except OSError:
        print("无法加载 POSTER_FONT_PATH 指定的字体；请检查路径或清空它以使用系统字体。", file=sys.stderr)
        raise SystemExit(2) from None

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
spec = PosterSpec(
    title="双十一限时优惠",
    subtitle="智能清洁 · 品质生活",
    price="199",
    original_price="399",
    style="蓝色科技风",
)
preview = compose(spec, product, logo, local_background(spec.style))
out = Path("data/examples")
out.mkdir(parents=True, exist_ok=True)
product.save(out / "sample_product.png")
logo.save(out / "sample_logo.png")
(out / "sample_preview.png").write_bytes(preview)
print("已生成 data/examples/sample_product.png, sample_logo.png, sample_preview.png")
