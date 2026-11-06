"""Operator-pinned local model bundles for opt-in DesignDocument OCR.

These digests verify files as seen by this local process, not independent model
authenticity or a guarantee of blocked network egress. Production must also
restrict outbound traffic and pin the PaddleOCR/PaddlePaddle packages.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path

_MAX_ENTRIES = 256
_MAX_TOTAL_BYTES = 4 * 1024**3
_MAX_FILE_BYTES = 2 * 1024**3
_MAX_DEPTH = 8
_SHA256_LENGTH = 64
_PROFILE_KEYS = {
    "schema_version",
    "text_detection_model_dir",
    "text_recognition_model_dir",
    "text_detection_sha256",
    "text_recognition_sha256",
}


class OCRModelProfileError(RuntimeError):
    """Required model paths or their pinned file contents are unverifiable."""


@dataclass(frozen=True)
class LocalOCRModelProfile:
    detector_dir: str
    recognizer_dir: str
    fingerprint: str


def _hash_regular_file(path: Path, *, expected_bytes: int) -> str:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
        with os.fdopen(fd, "rb") as stream:
            observed = os.fstat(stream.fileno())
            if not stat.S_ISREG(observed.st_mode) or observed.st_size != expected_bytes:
                raise OCRModelProfileError("local model file changed during verification")
            digest = hashlib.sha256()
            total = 0
            while chunk := stream.read(1024 * 1024):
                total += len(chunk)
                if total > expected_bytes:
                    raise OCRModelProfileError("local model file changed during verification")
                digest.update(chunk)
            if total != expected_bytes:
                raise OCRModelProfileError("local model file changed during verification")
            return digest.hexdigest()
    except (OSError, ValueError):
        raise OCRModelProfileError("local model file cannot be verified") from None


def model_directory_sha256(directory: Path) -> str:
    """SHA-256 of ordered relative paths + length + content hashes.

    Symlinks, special files, unreadable entries and unbounded trees are rejected.
    Re-running this across an attempt detects ordinary mutation, but cannot
    prevent TOCTOU if another actor can write the model directory.
    """
    path = Path(directory)
    try:
        if not path.is_absolute() or path.resolve(strict=True) != path:
            raise OCRModelProfileError("model directory must be canonical and absolute")
        if not stat.S_ISDIR(path.lstat().st_mode):
            raise OCRModelProfileError("model directory is not a real directory")

        entries: list[tuple[str, int, str]] = []
        total_bytes = 0

        def fail_walk(exc: OSError) -> None:
            raise OCRModelProfileError("cannot traverse local model directory") from exc

        for root, dirs, filenames in os.walk(
            path, topdown=True, followlinks=False, onerror=fail_walk
        ):
            current = Path(root)
            if len(current.relative_to(path).parts) > _MAX_DEPTH:
                raise OCRModelProfileError("model directory nesting exceeds limit")
            for name in dirs:
                metadata = (current / name).lstat()
                if not stat.S_ISDIR(metadata.st_mode):
                    raise OCRModelProfileError("model directory contains symlink or special entry")
            for name in filenames:
                file_path = current / name
                metadata = file_path.lstat()
                if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > _MAX_FILE_BYTES:
                    raise OCRModelProfileError("model directory contains unsafe file")
                total_bytes += metadata.st_size
                if total_bytes > _MAX_TOTAL_BYTES or len(entries) >= _MAX_ENTRIES:
                    raise OCRModelProfileError("model directory exceeds verification limits")
                digest = _hash_regular_file(file_path, expected_bytes=metadata.st_size)
                entries.append((file_path.relative_to(path).as_posix(), metadata.st_size, digest))
        if not entries:
            raise OCRModelProfileError("model directory is empty")

        data = json.dumps(
            sorted(entries), ensure_ascii=False, separators=(",", ":"), allow_nan=False
        )
        return hashlib.sha256(data.encode("utf-8")).hexdigest()
    except OCRModelProfileError:
        raise
    except (OSError, RuntimeError, ValueError):
        raise OCRModelProfileError("local model directory cannot be verified") from None


def load_model_profile(raw: str | None) -> LocalOCRModelProfile:
    """Fail closed unless operator pins two exact local model trees."""
    if not isinstance(raw, str) or len(raw) > 4096:
        raise OCRModelProfileError("local OCR model profile is required")
    try:
        cfg = json.loads(raw)
    except (TypeError, ValueError):
        raise OCRModelProfileError("invalid local OCR model profile JSON") from None
    if (
        not isinstance(cfg, dict)
        or set(cfg) != _PROFILE_KEYS
        or type(cfg["schema_version"]) is not int
        or cfg["schema_version"] != 1
    ):
        raise OCRModelProfileError("unsupported local OCR model profile schema")

    detector = cfg["text_detection_model_dir"]
    recognizer = cfg["text_recognition_model_dir"]
    expected_det = cfg["text_detection_sha256"]
    expected_rec = cfg["text_recognition_sha256"]
    if (
        not isinstance(detector, str)
        or not isinstance(recognizer, str)
        or detector == recognizer
        or not isinstance(expected_det, str)
        or not isinstance(expected_rec, str)
        or len(expected_det) != _SHA256_LENGTH
        or len(expected_rec) != _SHA256_LENGTH
        or any(ch not in "0123456789abcdef" for ch in expected_det + expected_rec)
    ):
        raise OCRModelProfileError("invalid model directories or SHA-256 pins")
    observed_det = model_directory_sha256(Path(detector))
    observed_rec = model_directory_sha256(Path(recognizer))
    if not hmac.compare_digest(expected_det, observed_det) or not hmac.compare_digest(
        expected_rec, observed_rec
    ):
        raise OCRModelProfileError("pinned local OCR model files changed")

    canonical = json.dumps(
        cfg, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    )
    return LocalOCRModelProfile(
        detector_dir=detector,
        recognizer_dir=recognizer,
        fingerprint=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    )
