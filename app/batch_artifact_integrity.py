"""Shared integrity check for the immutable v1 artifacts created by M5g."""
from __future__ import annotations

import hashlib
import json
import re

from PIL import Image

from . import storage
from .schemas import PosterSpec

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class BatchArtifactIntegrityError(ValueError):
    """A saved M5g artifact no longer matches its creation receipt."""


def verify_batch_artifact(poster_id: str, journal_row: dict) -> None:
    """Validate immutable v1 files against the M5g request's saved receipt.

    This is a local integrity check, not a signature, OCR result or quality pass.
    """
    try:
        path = storage.root() / poster_id
        assets = path / "assets"
        versions = path / "versions"
        meta_path = path / "meta.json"
        if (
            path.is_symlink() or not path.is_dir()
            or assets.is_symlink() or versions.is_symlink()
            or meta_path.is_symlink()
        ):
            raise ValueError("invalid poster storage directory")
        # Published version check enforces the committed meta pointer; files
        # lying beyond the latest published version must not enter review.
        image_path = storage.version_path(poster_id, 1)
        spec_path = versions / "v1.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        spec = PosterSpec.model_validate(
            json.loads(spec_path.read_text(encoding="utf-8"))
        )
        if (
            not isinstance(meta, dict)
            or meta.get("poster_id") != poster_id
            or meta.get("background_mode") != "local"
            or spec.background_mode != "local"
        ):
            raise ValueError("stored poster metadata disagrees with local batch")
        receipt = meta.get("execution_receipt")
        if not isinstance(receipt, dict):
            raise ValueError("missing local execution receipt")
        key = journal_row.get("idempotency_key")
        expected = receipt.get("sha256")
        if (
            not isinstance(key, str) or not key
            or receipt.get("key") != key
            or not isinstance(expected, dict)
            or not isinstance(receipt.get("request_hash"), str)
            or not _SHA256.fullmatch(receipt["request_hash"])
        ):
            raise ValueError("wrong receipt for batch journal")
        files = {
            "product": assets / "product.png",
            "background": assets / "background.png",
            "render": image_path,
            "spec": spec_path,
        }
        logo_path = assets / "logo.png"
        if "logo" in expected:
            files["logo"] = logo_path
        elif logo_path.exists() or logo_path.is_symlink():
            raise ValueError("unexpected local logo asset")
        if set(expected) != set(files):
            raise ValueError("incomplete local receipt hashes")
        for name, file in files.items():
            digest = expected[name]
            if (
                file.is_symlink() or not file.is_file()
                or not isinstance(digest, str) or not _SHA256.fullmatch(digest)
                or hashlib.sha256(file.read_bytes()).hexdigest() != digest
            ):
                raise ValueError(f"stored {name} does not match receipt")
            if name != "spec":
                with Image.open(file) as image:
                    if image.format != "PNG":
                        raise ValueError("stored image is not PNG")
                    image.load()
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        raise BatchArtifactIntegrityError(
            "已保存的批次海报产物缺失、损坏或与执行凭据不一致"
        ) from exc
