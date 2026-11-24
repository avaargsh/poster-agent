"""Safe file-input tests for the offline DesignDocument OCR evidence verifier."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

import scripts.verify_ocr_evidence_bundle as verifier


def test_verify_bundle_reads_a_regular_file(tmp_path, monkeypatch):
    path = tmp_path / "bundle.json"
    path.write_text('{"fixture":"regular-file"}', encoding="utf-8")
    expected = "a" * 64
    monkeypatch.setattr(
        verifier,
        "_verify",
        lambda bundle, digest: {"bundle": bundle, "expected_sha256": digest},
    )

    assert verifier.verify_bundle(path, expected) == {
        "bundle": {"fixture": "regular-file"},
        "expected_sha256": expected,
    }


def test_verify_bundle_rejects_a_symlinked_input(tmp_path):
    source = tmp_path / "source.json"
    source.write_text("{}", encoding="utf-8")
    selected = tmp_path / "bundle.json"
    selected.symlink_to(source)

    with pytest.raises(verifier.EvidenceError, match="symlink"):
        verifier.verify_bundle(selected, "a" * 64)


@pytest.mark.skipif(os.name != "posix", reason="POSIX device-file contract")
def test_verify_bundle_rejects_character_devices():
    with pytest.raises(verifier.EvidenceError, match="regular file"):
        verifier.verify_bundle(Path(os.devnull), "a" * 64)


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO is POSIX-only")
def test_verify_bundle_rejects_fifo_without_blocking(tmp_path):
    fifo = tmp_path / "bundle.fifo"
    os.mkfifo(fifo)
    script = """
import sys
from pathlib import Path
from scripts.verify_ocr_evidence_bundle import EvidenceError, verify_bundle

try:
    verify_bundle(Path(sys.argv[1]), "a" * 64)
except EvidenceError as exc:
    if "regular file" not in str(exc):
        raise
else:
    raise AssertionError("FIFO was accepted as an evidence file")
"""
    subprocess.run(
        [sys.executable, "-c", script, str(fifo)],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
