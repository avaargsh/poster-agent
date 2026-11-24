"""Bind a local OCR environment manifest to one verified DesignDocument journal.

This is an offline consistency check, not process attestation, egress proof,
font-selection proof, model-weight attestation, or quality acceptance.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from pathlib import Path

from scripts.verify_ocr_evidence_bundle import (
    _MAX_FILE_BYTES,
    _canonical,
    _verify,
)

_MAX_MANIFEST_BYTES = 64 * 1024
_SHA256 = re.compile(r"[0-9a-f]{64}")
_COMMIT = re.compile(r"[0-9a-f]{40}")
_FLAGS = (
    "font_selection_verified",
    "server_process_verified",
    "egress_denied_verified",
    "runtime_attested",
    "quality_verified",
    "release_ready",
)
_MANIFEST_KEYS = {
    "schema_version",
    "evidence_kind",
    "git_commit",
    "platform",
    "packages",
    "fonts",
    "model_profile_sha256",
    "detection_sha256",
    "recognition_sha256",
    *_FLAGS,
}


class ManifestBindingError(ValueError):
    """The local manifest does not consistently describe the OCR journal."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ManifestBindingError(message)


def _text(value: object) -> bool:
    return (
        isinstance(value, str)
        and 0 < len(value) <= 128
        and "\x00" not in value
    )


def _sha(value: object) -> bool:
    return isinstance(value, str) and _SHA256.fullmatch(value) is not None


def _unique(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        _require(key not in result, "duplicate JSON key")
        result[key] = value
    return result


def _nonfinite(value: str) -> None:
    raise ManifestBindingError("non-finite JSON number")


def _read_json(path: Path, limit: int) -> dict:
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    _require(len(raw) <= limit, "input exceeds size limit")
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique,
            parse_constant=_nonfinite,
        )
        _canonical(value)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError):
        raise ManifestBindingError("invalid bounded JSON input") from None
    _require(type(value) is dict, "expected JSON object")
    return value


def verify_manifest_binding(manifest: dict, journal: dict, expected_git_commit: str) -> dict:
    """Compare environment observations with a verified OCR journal.

    expected_git_commit is an operator-supplied checkout identity. It is not
    proof that the server process ran that checkout.
    """
    _require(type(manifest) is dict and set(manifest) == _MANIFEST_KEYS, "invalid manifest fields")
    _require(
        type(manifest["schema_version"]) is int
        and manifest["schema_version"] == 1
        and manifest["evidence_kind"] == "LOCAL_ENVIRONMENT_OBSERVED_UNATTESTED",
        "unsupported manifest schema",
    )
    _require(
        isinstance(expected_git_commit, str)
        and _COMMIT.fullmatch(expected_git_commit) is not None
        and manifest["git_commit"] == expected_git_commit,
        "checkout commit does not match operator input",
    )
    _require(all(manifest[key] is False for key in _FLAGS), "manifest contains an authority claim")

    platform_info = manifest["platform"]
    _require(
        type(platform_info) is dict
        and set(platform_info) == {"system", "release", "machine", "python"}
        and all(_text(value) for value in platform_info.values()),
        "invalid platform identity",
    )

    packages = manifest["packages"]
    _require(
        type(packages) is dict
        and set(packages)
        in (
            {"paddleocr", "CairoSVG", "paddlepaddle"},
            {"paddleocr", "CairoSVG", "paddlepaddle-gpu"},
        )
        and all(_text(value) for value in packages.values()),
        "exactly one Paddle distribution and CairoSVG are required",
    )
    paddle_distribution = (
        "paddlepaddle" if "paddlepaddle" in packages else "paddlepaddle-gpu"
    )

    fonts = manifest["fonts"]
    _require(isinstance(fonts, list) and 1 <= len(fonts) <= 8, "invalid font identities")
    for font in fonts:
        _require(
            type(font) is dict
            and set(font) == {"size_bytes", "sha256"}
            and type(font["size_bytes"]) is int
            and 0 < font["size_bytes"] <= 128 * 1024 * 1024
            and _sha(font["sha256"]),
            "invalid font identity",
        )

    for key in ("model_profile_sha256", "detection_sha256", "recognition_sha256"):
        _require(_sha(manifest[key]), "invalid model digest")

    _require(type(journal) is dict, "invalid journal")
    operation_id = journal.get("operation_id")
    _require(
        isinstance(operation_id, str)
        and re.fullmatch(r"ocr_[0-9a-f]{32}", operation_id) is not None
        and _sha(journal.get("model_profile_sha256"))
        and journal["model_profile_sha256"] == manifest["model_profile_sha256"],
        "model profile does not match executed journal",
    )

    runtime = journal.get("runtime")
    _require(
        type(runtime) is dict
        and runtime.get("source") == "local_paddleocr_adapter"
        and runtime.get("adapter_reported_source") == "paddleocr_adapter",
        "journal does not contain a local PaddleOCR runtime observation",
    )
    _require(
        runtime.get("python_version") == platform_info["python"]
        and runtime.get("machine") == platform_info["machine"],
        "Python or machine identity differs from journal",
    )
    _require(
        runtime.get("paddleocr_package_version") == packages["paddleocr"]
        and runtime.get("paddlepaddle_package_version") == packages[paddle_distribution],
        "Paddle package versions differ from journal",
    )

    canonical_manifest = _canonical(manifest).encode("utf-8")
    return {
        "schema_version": 1,
        "status": "LOCAL_MANIFEST_MATCHES_JOURNAL_UNATTESTED",
        "manifest_sha256": hashlib.sha256(canonical_manifest).hexdigest(),
        "git_commit": manifest["git_commit"],
        "operation_id": operation_id,
        "model_profile_sha256": manifest["model_profile_sha256"],
        "runtime_platform_match": True,
        "runtime_package_versions_match": True,
        "cairosvg_version_recorded": True,
        "font_identity_count": len(fonts),
        "operator_checkout_input_matches_manifest": True,
        "font_selection_verified": False,
        "server_process_verified": False,
        "egress_denied_verified": False,
        "runtime_attested": False,
        "quality_verified": False,
        "release_ready": False,
    }


def verify_smoke_files(
    manifest_path: Path,
    bundle_path: Path,
    expected_bundle_sha256: str,
    expected_git_commit: str,
) -> dict:
    manifest = _read_json(manifest_path, _MAX_MANIFEST_BYTES)
    bundle = _read_json(bundle_path, _MAX_FILE_BYTES)
    try:
        evidence = _verify(bundle, expected_bundle_sha256)
    except (ValueError, KeyError, TypeError, RecursionError):
        raise ManifestBindingError("evidence bundle verification failed") from None
    report = verify_manifest_binding(manifest, bundle["journal"], expected_git_commit)
    report["bundle_sha256"] = evidence["bundle_sha256"]
    report["evidence_status"] = evidence["status"]
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--expected-bundle-sha256", required=True)
    parser.add_argument(
        "--expected-git-commit",
        required=True,
        help="operator-observed checkout commit; not server-process attestation",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = verify_smoke_files(
            args.manifest,
            args.bundle,
            args.expected_bundle_sha256,
            args.expected_git_commit,
        )
        raw = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        with os.fdopen(
            os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w"
        ) as stream:
            stream.write(raw + "\n")
    except (ManifestBindingError, OSError, ValueError, TypeError, RecursionError):
        parser.exit(2, "OCR manifest binding refused; inspect private operator inputs.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
