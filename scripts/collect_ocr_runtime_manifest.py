"""Collect local environment observations, never runtime attestation or inference."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import stat
import subprocess
from importlib import metadata
from pathlib import Path

from app.designs.models import RENDERER_VERSION
from app.designs.ocr_model_policy import OCRModelProfileError, load_model_profile

_MAX_FONT_BYTES = 128 * 1024 * 1024


class ManifestError(ValueError):
    """Required environment observations cannot be collected."""


def _git_identity(repo: Path) -> str:
    def git(*args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(repo), *args],
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        ).stdout.strip()

    if Path(git("rev-parse", "--show-toplevel")).resolve() != repo.resolve():
        raise ManifestError("repository root required")
    commit = git("rev-parse", "HEAD")
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ManifestError("invalid git identity")
    if git("status", "--porcelain", "--untracked-files=normal"):
        raise ManifestError("working tree must be clean")
    return commit


def _font_file_identity(info: os.stat_result) -> tuple[int, int, int, int, int, int]:
    # Ignore atime: a concurrent reader need not change the measured file.
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _font_identity(path: Path) -> dict:
    if not path.is_absolute() or path.resolve(strict=True) != path:
        raise ManifestError("font path must be canonical and absolute")
    initial = path.lstat()
    if not stat.S_ISREG(initial.st_mode) or not 0 < initial.st_size <= _MAX_FONT_BYTES:
        raise ManifestError("invalid font file")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    with os.fdopen(os.open(path, flags), "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or _font_file_identity(before) != _font_file_identity(
            initial
        ):
            raise ManifestError("font changed before measurement")
        data = stream.read(_MAX_FONT_BYTES + 1)
        after = os.fstat(stream.fileno())
        # Reading may alter atime; compare the relevant identity and write metadata.
        if (
            len(data) != before.st_size
            or _font_file_identity(after) != _font_file_identity(before)
            or path.resolve(strict=True) != path
            or _font_file_identity(path.lstat()) != _font_file_identity(before)
        ):
            raise ManifestError("font changed during measurement")
    return {"size_bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def _packages() -> dict[str, str]:
    result = {name: metadata.version(name) for name in ("paddleocr", "CairoSVG")}
    paddle = {}
    for name in ("paddlepaddle", "paddlepaddle-gpu"):
        try:
            paddle[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            continue
    if len(paddle) != 1:
        raise ManifestError("exactly one Paddle distribution required")
    return {**result, **paddle}


def collect_manifest(repo: Path, fonts: list[Path], raw_profile: str | None) -> dict:
    """Measure the invoking environment; no claim about an external server process."""
    try:
        if not 1 <= len(fonts) <= 8 or len(set(fonts)) != len(fonts):
            raise ManifestError("one to eight distinct operator font paths required")
        commit = _git_identity(repo)
        profile = load_model_profile(raw_profile)
        config = json.loads(raw_profile)
        result = {
            "schema_version": 2,
            "evidence_kind": "LOCAL_ENVIRONMENT_OBSERVED_UNATTESTED",
            "git_commit": commit,
            "renderer_version": RENDERER_VERSION,
            "platform": {
                "system": platform.system(),
                "release": platform.release(),
                "machine": platform.machine(),
                "python": platform.python_version(),
            },
            "packages": _packages(),
            "fonts": [_font_identity(path) for path in fonts],
            "model_profile_sha256": profile.fingerprint,
            "detection_sha256": config["text_detection_sha256"],
            "recognition_sha256": config["text_recognition_sha256"],
            "font_selection_verified": False,
            "server_process_verified": False,
            "egress_denied_verified": False,
            "runtime_attested": False,
            "quality_verified": False,
            "release_ready": False,
        }
        if _git_identity(repo) != commit:
            raise ManifestError("git identity changed during measurement")
        return result
    except ManifestError:
        raise
    except (
        OSError,
        ValueError,
        RuntimeError,
        TypeError,
        subprocess.SubprocessError,
        metadata.PackageNotFoundError,
        OCRModelProfileError,
    ):
        raise ManifestError("local environment measurement refused") from None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--font", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        repo, output = args.repo.resolve(), args.output.resolve()
        if output.is_relative_to(repo):
            raise ManifestError("output must be outside repository")
        result = collect_manifest(
            repo, args.font, os.environ.get("POSTER_DESIGN_OCR_MODEL_CONFIG_JSON")
        )
        raw = json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        with os.fdopen(os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as stream:
            stream.write(raw + "\n")
    except (ManifestError, OSError):
        parser.exit(2, "Local OCR environment measurement refused; inspect operator setup.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
