"""The standalone quick-start demo must honor its local font configuration."""

from __future__ import annotations

import os
import runpy

import pytest

import app.compositor


@pytest.mark.parametrize(
    ("exported_font", "expected_font"),
    [(None, "/local/dotenv-font.ttc"), ("/local/exported-font.ttc", "/local/exported-font.ttc")],
)
def test_generate_demo_honors_dotenv_without_overriding_shell(
    tmp_path, monkeypatch, exported_font, expected_font
):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("POSTER_FONT_PATH=/local/dotenv-font.ttc\n", encoding="utf-8")
    if exported_font is None:
        # Record cleanup even when load_dotenv creates a previously absent key.
        monkeypatch.setenv("POSTER_FONT_PATH", "")
        monkeypatch.delenv("POSTER_FONT_PATH", raising=False)
    else:
        monkeypatch.setenv("POSTER_FONT_PATH", exported_font)

    selected_fonts = []

    def record_font(*_args):
        selected_fonts.append(os.environ.get("POSTER_FONT_PATH"))
        return b"synthetic test output"

    monkeypatch.setattr(app.compositor, "compose", record_font)
    runpy.run_module("scripts.generate_demo", run_name="__main__")

    assert selected_fonts == [expected_font]
    assert (tmp_path / "data/examples/sample_preview.png").is_file()
