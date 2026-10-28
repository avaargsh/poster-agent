from io import BytesIO

import pytest
from PIL import Image


@pytest.fixture
def product_bytes():
    buf = BytesIO()
    Image.new("RGBA", (260, 400), (10, 90, 150, 255)).save(buf, "PNG")
    return buf.getvalue()
