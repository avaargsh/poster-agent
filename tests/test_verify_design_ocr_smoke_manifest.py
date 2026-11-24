import pytest

from scripts.verify_design_ocr_smoke_manifest import (
    ManifestBindingError,
    verify_manifest_binding,
)


def _manifest_and_journal():
    manifest = {
        "schema_version": 1,
        "evidence_kind": "LOCAL_ENVIRONMENT_OBSERVED_UNATTESTED",
        "git_commit": "a" * 40,
        "renderer_version": "design-svg@1",
        "platform": {
            "system": "Linux",
            "release": "6.8.0",
            "machine": "x86_64",
            "python": "3.12.8",
        },
        "packages": {
            "paddleocr": "3.3.0",
            "CairoSVG": "2.7.1",
            "paddlepaddle": "3.2.0",
        },
        "fonts": [{"size_bytes": 1234, "sha256": "b" * 64}],
        "model_profile_sha256": "c" * 64,
        "detection_sha256": "d" * 64,
        "recognition_sha256": "e" * 64,
        "font_selection_verified": False,
        "server_process_verified": False,
        "egress_denied_verified": False,
        "runtime_attested": False,
        "quality_verified": False,
        "release_ready": False,
    }
    journal = {
        "operation_id": "ocr_" + "f" * 32,
        "renderer_version": "design-svg@1",
        "rasterizer": "cairosvg/2.7.1",
        "status": "EXECUTED",
        "reason": None,
        "replayed": False,
        "runtime_attested": False,
        "quality_verified": False,
        "release_ready": False,
        "model_profile_sha256": "c" * 64,
        "runtime": {
            "source": "local_paddleocr_adapter",
            "adapter_reported_source": "paddleocr_adapter",
            "python_version": "3.12.8",
            "machine": "x86_64",
            "paddleocr_package_version": "3.3.0",
            "paddlepaddle_package_version": "3.2.0",
        },
    }
    return manifest, journal


def test_manifest_binds_checkout_model_and_runtime_to_executed_journal():
    manifest, journal = _manifest_and_journal()

    report = verify_manifest_binding(manifest, journal, "a" * 40)

    assert report["status"] == "LOCAL_MANIFEST_MATCHES_JOURNAL_UNATTESTED"
    assert report["git_commit"] == "a" * 40
    assert report["operation_id"] == journal["operation_id"]
    assert report["model_profile_sha256"] == journal["model_profile_sha256"]
    assert report["renderer_version_match"] is True
    assert report["rasterizer_match"] is True
    assert report["runtime_package_versions_match"] is True
    assert report["runtime_python_and_machine_match"] is True
    assert report["font_identity_count"] == 1
    assert report["server_process_verified"] is False
    assert report["egress_denied_verified"] is False
    assert report["font_selection_verified"] is False
    assert report["runtime_attested"] is False
    assert report["quality_verified"] is False
    assert report["release_ready"] is False


@pytest.mark.parametrize(
    ("mutation", "expected_commit"),
    [
        (lambda manifest, journal: None, "0" * 40),
        (lambda manifest, journal: journal.update(model_profile_sha256="0" * 64), "a" * 40),
        (lambda manifest, journal: journal["runtime"].update(python_version="3.11.9"), "a" * 40),
        (lambda manifest, journal: journal["runtime"].update(machine="aarch64"), "a" * 40),
        (
            lambda manifest, journal: journal["runtime"].update(paddleocr_package_version="3.2.0"),
            "a" * 40,
        ),
        (
            lambda manifest, journal: journal["runtime"].update(
                paddlepaddle_package_version="3.1.0"
            ),
            "a" * 40,
        ),
        (lambda manifest, journal: manifest.update(egress_denied_verified=True), "a" * 40),
        (lambda manifest, journal: manifest["packages"].pop("CairoSVG"), "a" * 40),
        (lambda manifest, journal: manifest.update(fonts=[]), "a" * 40),
        (
            lambda manifest, journal: manifest["fonts"][0].update(size_bytes=True),
            "a" * 40,
        ),
    ],
)
def test_manifest_refuses_mismatched_or_overstated_environment(mutation, expected_commit):
    manifest, journal = _manifest_and_journal()
    mutation(manifest, journal)

    with pytest.raises(ManifestBindingError):
        verify_manifest_binding(manifest, journal, expected_commit)


def test_manifest_requires_exactly_one_paddle_distribution():
    manifest, journal = _manifest_and_journal()
    manifest["packages"]["paddlepaddle-gpu"] = "3.2.0"

    with pytest.raises(ManifestBindingError):
        verify_manifest_binding(manifest, journal, "a" * 40)


def test_manifest_rejects_unknown_fields():
    manifest, journal = _manifest_and_journal()
    manifest["server_pid"] = 123

    with pytest.raises(ManifestBindingError):
        verify_manifest_binding(manifest, journal, "a" * 40)


def test_manifest_does_not_promote_operator_assertions_to_attestation():
    manifest, journal = _manifest_and_journal()
    manifest["server_process_verified"] = False
    manifest["font_selection_verified"] = False
    manifest["egress_denied_verified"] = False

    report = verify_manifest_binding(manifest, journal, "a" * 40)

    assert report["status"] == "LOCAL_MANIFEST_MATCHES_JOURNAL_UNATTESTED"
    assert all(
        report[key] is False
        for key in (
            "server_process_verified",
            "font_selection_verified",
            "egress_denied_verified",
            "runtime_attested",
            "quality_verified",
            "release_ready",
        )
    )


def test_manifest_accepts_gpu_paddle_distribution_when_runtime_matches():
    manifest, journal = _manifest_and_journal()
    manifest["packages"]["paddlepaddle-gpu"] = manifest["packages"].pop("paddlepaddle")

    report = verify_manifest_binding(manifest, journal, "a" * 40)

    assert report["runtime_package_versions_match"] is True


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("status", "UNKNOWN"),
        ("replayed", True),
        ("release_ready", True),
    ],
)
def test_manifest_requires_initial_unattested_executed_journal(field, value):
    manifest, journal = _manifest_and_journal()
    journal[field] = value

    with pytest.raises(ManifestBindingError):
        verify_manifest_binding(manifest, journal, "a" * 40)


@pytest.mark.parametrize(
    ("location", "field", "value"),
    [
        ("manifest", "renderer_version", "design-svg@2"),
        ("journal", "renderer_version", "design-svg@2"),
        ("journal", "rasterizer", "cairosvg/2.7.2"),
    ],
)
def test_manifest_refuses_renderer_or_rasterizer_mismatch(location, field, value):
    manifest, journal = _manifest_and_journal()
    target = manifest if location == "manifest" else journal
    target[field] = value

    with pytest.raises(ManifestBindingError):
        verify_manifest_binding(manifest, journal, "a" * 40)


def test_manifest_rejects_missing_renderer_identity_on_both_sides():
    manifest, journal = _manifest_and_journal()
    manifest["renderer_version"] = None
    journal["renderer_version"] = None

    with pytest.raises(ManifestBindingError):
        verify_manifest_binding(manifest, journal, "a" * 40)
