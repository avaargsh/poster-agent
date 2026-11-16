import subprocess

import pytest

from app.designs.rasterization import RasterFontUnavailable, _require_fixed_fonts

SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><text font-family="Noto Sans CJK SC" font-weight="400">text</text><text font-family="Noto Sans CJK SC" font-weight="700">price</text></svg>'


@pytest.fixture(autouse=True)
def no_native_font_mutation(monkeypatch):
    monkeypatch.setattr("app.designs.rasterization._register_process_fonts", lambda paths: None)


def test_raster_requires_declared_family_and_styles(monkeypatch):
    monkeypatch.setattr("app.designs.rasterization.shutil.which", lambda name: "/bin/fc-match")
    calls = []

    def match(args, **kwargs):
        calls.append(args[-1])
        style = args[-1].split("style=")[1]
        return subprocess.CompletedProcess(
            args, 0, "Noto Sans CJK SC\n" + style + "\n/tmp/font.otf\n"
        )

    monkeypatch.setattr("app.designs.rasterization.subprocess.run", match)
    _require_fixed_fonts(SVG)
    assert set(calls) == {"Noto Sans CJK SC:style=Regular", "Noto Sans CJK SC:style=Bold"}


@pytest.mark.parametrize("output", ["Verdana\nRegular\n", "Noto Sans CJK SC\nThin\n", ""])
def test_raster_rejects_font_substitution(monkeypatch, output):
    monkeypatch.setattr("app.designs.rasterization.shutil.which", lambda name: "/bin/fc-match")
    monkeypatch.setattr(
        "app.designs.rasterization.subprocess.run",
        lambda *a, **kw: subprocess.CompletedProcess(a, 0, output),
    )
    with pytest.raises(RasterFontUnavailable):
        _require_fixed_fonts(SVG)


def test_raster_font_probe_fails_closed_and_has_a_timeout(monkeypatch):
    monkeypatch.setattr("app.designs.rasterization.shutil.which", lambda name: None)
    with pytest.raises(RasterFontUnavailable):
        _require_fixed_fonts(SVG)
    monkeypatch.setattr("app.designs.rasterization.shutil.which", lambda name: "/bin/fc-match")

    def timeout(args, **kwargs):
        assert kwargs["timeout"] == 3
        raise subprocess.TimeoutExpired(args, 3)

    monkeypatch.setattr("app.designs.rasterization.subprocess.run", timeout)
    with pytest.raises(RasterFontUnavailable):
        _require_fixed_fonts(SVG)


def test_raster_without_text_does_not_require_fonts(monkeypatch):
    monkeypatch.setattr("app.designs.rasterization.shutil.which", lambda name: None)
    _require_fixed_fonts(b'<svg xmlns="http://www.w3.org/2000/svg"/>')
