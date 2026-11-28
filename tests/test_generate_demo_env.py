"""The standalone quick-start demo must honor its local font configuration."""

from __future__ import annotations

import os
import runpy
from pathlib import Path

import pytest
from PIL import ImageFont

import app.compositor


@pytest.mark.parametrize(
    ("exported_font", "expected_font"),
    [(None, "/local/dotenv-font.ttc"), ("/local/exported-font.ttc", "/local/exported-font.ttc")],
)
def test_generate_demo_honors_dotenv_without_overriding_shell(
    tmp_path, monkeypatch, exported_font, expected_font
):
    monkeypatch.chdir(tmp_path)
    dotenv_path = tmp_path / "dotenv-font.ttc"
    dotenv_path.touch()
    shell_path = tmp_path / "exported-font.ttc"
    shell_path.touch()
    (tmp_path / ".env").write_text(f"POSTER_FONT_PATH={dotenv_path}\n", encoding="utf-8")
    if exported_font is None:
        # Record cleanup even when load_dotenv creates a previously absent key.
        monkeypatch.setenv("POSTER_FONT_PATH", "")
        monkeypatch.delenv("POSTER_FONT_PATH", raising=False)
    else:
        monkeypatch.setenv("POSTER_FONT_PATH", str(shell_path))

    selected_fonts = []

    def record_font(*_args):
        selected_fonts.append(os.environ.get("POSTER_FONT_PATH"))
        return b"synthetic test output"

    monkeypatch.setattr(app.compositor, "compose", record_font)

    class Mask(bytes):
        size = (1, 1)

    class CompleteFont:
        def getmask(self, char):
            return Mask(b"b" if char in ("\uffff", "\U0010ffff") else b"a")

    monkeypatch.setattr(ImageFont, "truetype", lambda *_args, **_options: CompleteFont())
    runpy.run_module("scripts.generate_demo", run_name="__main__")

    assert [Path(font).name for font in selected_fonts] == [Path(expected_font).name]
    assert (tmp_path / "data/examples/sample_preview.png").is_file()


@pytest.mark.parametrize("existing_invalid_file", [False, True])
def test_generate_demo_rejects_bad_explicit_font_without_writing_examples(
    tmp_path, monkeypatch, capsys, existing_invalid_file
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("POSTER_FONT_PATH", "")
    monkeypatch.delenv("POSTER_FONT_PATH")
    bad_font = tmp_path / "bad-font.ttc"
    if existing_invalid_file:
        bad_font.write_text("not a loadable font", encoding="utf-8")
    (tmp_path / ".env").write_text(f"POSTER_FONT_PATH={bad_font}\n", encoding="utf-8")

    with pytest.raises(SystemExit) as exc:
        runpy.run_module("scripts.generate_demo", run_name="__main__")

    assert exc.value.code == 2
    assert "POSTER_FONT_PATH" in capsys.readouterr().err
    assert not (tmp_path / "data").exists()


def test_generate_demo_rejects_font_that_only_renders_missing_glyphs(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    font = tmp_path / "latin-only.ttf"
    font.touch()
    monkeypatch.setenv("POSTER_FONT_PATH", str(font))
    latin = ImageFont.load_default()
    monkeypatch.setattr(ImageFont, "truetype", lambda *_args, **_options: latin)
    monkeypatch.setattr(app.compositor, "compose", lambda *_args: b"synthetic")

    with pytest.raises(SystemExit) as exc:
        runpy.run_module("scripts.generate_demo", run_name="__main__")

    assert exc.value.code == 2
    assert "缺少" in capsys.readouterr().err
    assert not (tmp_path / "data").exists()


def test_generate_demo_rejects_font_missing_call_to_action(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    font = tmp_path / "partial-cjk.otf"
    font.touch()
    monkeypatch.setenv("POSTER_FONT_PATH", str(font))

    class Mask(bytes):
        size = (1, 1)

    class PartialCjkFont:
        def getmask(self, char):
            return Mask(b"a" if char in "双十一限时优惠智能清洁品质生活" else b"b")

    monkeypatch.setattr(ImageFont, "truetype", lambda *_args, **_options: PartialCjkFont())
    monkeypatch.setattr(app.compositor, "compose", lambda *_args: b"synthetic")

    with pytest.raises(SystemExit) as exc:
        runpy.run_module("scripts.generate_demo", run_name="__main__")

    assert exc.value.code == 2
    assert "缺少示例字符" in capsys.readouterr().err
    assert not (tmp_path / "data").exists()


@pytest.mark.parametrize("missing_char", ["手", "¥", "3", "·"])
def test_generate_demo_rejects_missing_visible_glyph(tmp_path, monkeypatch, capsys, missing_char):
    monkeypatch.chdir(tmp_path)
    font = tmp_path / "partial.otf"
    font.touch()
    monkeypatch.setenv("POSTER_FONT_PATH", str(font))

    class Mask(bytes):
        size = (1, 1)

    class PartialFont:
        def getmask(self, char):
            return Mask(b"b" if char in (missing_char, "\uffff", "\U0010ffff") else b"a")

    monkeypatch.setattr(ImageFont, "truetype", lambda *_args, **_options: PartialFont())
    monkeypatch.setattr(app.compositor, "compose", lambda *_args: b"synthetic")

    with pytest.raises(SystemExit) as exc:
        runpy.run_module("scripts.generate_demo", run_name="__main__")

    assert exc.value.code == 2
    assert missing_char in capsys.readouterr().err
    assert not (tmp_path / "data").exists()


@pytest.mark.parametrize("collision", ["directory", "symlink", "dangling-symlink"])
def test_generate_demo_reports_output_collision_before_writing(
    tmp_path, monkeypatch, capsys, collision
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("POSTER_FONT_PATH", "")
    monkeypatch.setattr(app.compositor, "compose", lambda *_args: b"synthetic")
    out = tmp_path / "data/examples"
    out.mkdir(parents=True)
    product = out / "sample_product.png"
    product.write_bytes(b"previous product")
    logo = out / "sample_logo.png"
    if collision == "directory":
        logo.mkdir()
    else:
        target = tmp_path / "other-file.png"
        if collision == "symlink":
            target.write_bytes(b"other file")
        logo.symlink_to(target)

    with pytest.raises(SystemExit) as exc:
        runpy.run_module("scripts.generate_demo", run_name="__main__")

    assert exc.value.code == 2
    assert "sample_logo.png" in capsys.readouterr().err
    assert product.read_bytes() == b"previous product"
    if collision == "symlink":
        assert target.read_bytes() == b"other file"
    elif collision == "dangling-symlink":
        assert not target.exists()
    assert not (out / "sample_preview.png").exists()
