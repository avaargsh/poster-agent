"""Browser smoke tests must find native Chrome and avoid user runtime storage."""

import os
from pathlib import Path

from browser_support import browser_test_environment, chromium_launch_options


def test_native_macos_chrome_is_found_without_linux_command(monkeypatch, tmp_path):
    import browser_support

    chrome = tmp_path / "Google Chrome.app/Contents/MacOS/Google Chrome"
    chrome.parent.mkdir(parents=True)
    chrome.touch()
    monkeypatch.setattr(browser_support.shutil, "which", lambda _: None)
    monkeypatch.setattr(browser_support, "MAC_CHROME", chrome)
    assert chromium_launch_options() == {"executable_path": str(chrome)}


def test_chrome_symlink_is_resolved_for_bundle_framework_lookup(monkeypatch, tmp_path):
    import browser_support

    chrome = tmp_path / "Chrome.app/Contents/MacOS/Chrome"
    chrome.parent.mkdir(parents=True)
    chrome.touch()
    link = tmp_path / "google-chrome"
    link.symlink_to(chrome)
    monkeypatch.setattr(browser_support.shutil, "which", lambda _: str(link))
    assert chromium_launch_options()["executable_path"] == str(chrome)


def test_playwright_default_is_used_when_system_chrome_is_missing(monkeypatch, tmp_path):
    import browser_support

    monkeypatch.setattr(browser_support.shutil, "which", lambda _: None)
    monkeypatch.setattr(browser_support, "MAC_CHROME", tmp_path / "missing")
    assert chromium_launch_options() == {}


def test_browser_runtime_paths_are_disposable_and_parent_environment_unchanged(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("POSTER_DATA_DIR", "/user/real/posters")
    monkeypatch.setenv("POSTER_EXECUTION_DB", "/user/real/executions.sqlite3")
    before = dict(os.environ)
    env = browser_test_environment(tmp_path)
    for key in ("POSTER_DATA_DIR", "POSTER_EXECUTION_DB", "POSTER_CHECKPOINT_DB"):
        assert Path(env[key]).is_relative_to(tmp_path)
    assert dict(os.environ) == before
