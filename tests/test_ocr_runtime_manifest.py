import hashlib
import json

import pytest

from app.designs.models import RENDERER_VERSION
from app.designs.ocr_model_policy import model_directory_sha256
from scripts.collect_ocr_runtime_manifest import ManifestError, collect_manifest


@pytest.fixture
def inputs(tmp_path, monkeypatch):
    from scripts import collect_ocr_runtime_manifest as collector

    detector = tmp_path / "det"
    recognizer = tmp_path / "rec"
    for directory in (detector, recognizer):
        directory.mkdir()
        (directory / "weights.bin").write_bytes(directory.name.encode())
    font = tmp_path / "font.ttf"
    font.write_bytes(b"synthetic font bytes")
    profile = {
        "schema_version": 1,
        "text_detection_model_dir": str(detector),
        "text_recognition_model_dir": str(recognizer),
        "text_detection_sha256": model_directory_sha256(detector),
        "text_recognition_sha256": model_directory_sha256(recognizer),
    }
    monkeypatch.setattr(collector, "_git_identity", lambda repo: "a" * 40)

    def version(package):
        if package == "paddlepaddle-gpu":
            raise collector.metadata.PackageNotFoundError(package)
        return "1.2.3"

    monkeypatch.setattr(collector.metadata, "version", version)
    return tmp_path, font, profile


def test_manifest_binds_local_bytes_without_paths_or_authority(inputs):
    root, font, profile = inputs
    result = collect_manifest(root, [font], json.dumps(profile))
    assert result["git_commit"] == "a" * 40
    assert result["schema_version"] == 2
    assert result["renderer_version"] == RENDERER_VERSION
    assert result["packages"]["paddleocr"] == "1.2.3"
    assert result["fonts"] == [
        {
            "size_bytes": len(font.read_bytes()),
            "sha256": hashlib.sha256(font.read_bytes()).hexdigest(),
        }
    ]
    assert result["detection_sha256"] == profile["text_detection_sha256"]
    assert result["recognition_sha256"] == profile["text_recognition_sha256"]
    assert result["runtime_attested"] is False
    assert result["quality_verified"] is False
    assert result["release_ready"] is False
    assert str(root) not in json.dumps(result)
    assert result["font_selection_verified"] is False


def test_manifest_rejects_drifted_model(inputs):
    root, font, profile = inputs
    (root / "det" / "weights.bin").write_bytes(b"changed")
    with pytest.raises(ManifestError):
        collect_manifest(root, [font], json.dumps(profile))


def test_manifest_rejects_missing_package(inputs, monkeypatch):
    from scripts import collect_ocr_runtime_manifest as collector

    root, font, profile = inputs

    def missing(package):
        raise collector.metadata.PackageNotFoundError(package)

    monkeypatch.setattr(collector.metadata, "version", missing)
    with pytest.raises(ManifestError):
        collect_manifest(root, [font], json.dumps(profile))


def test_manifest_rejects_symlink_font(inputs):
    root, font, profile = inputs
    link = root / "link.ttf"
    link.symlink_to(font)
    with pytest.raises(ManifestError):
        collect_manifest(root, [link], json.dumps(profile))


def test_font_identity_rejects_parent_directory_replacement(tmp_path, monkeypatch):
    from scripts import collect_ocr_runtime_manifest as collector

    directory = tmp_path / "fonts"
    directory.mkdir()
    font = directory / "font.ttf"
    font.write_bytes(b"original")
    original_fstat = collector.os.fstat
    calls = 0

    def replace_parent(fd):
        nonlocal calls
        result = original_fstat(fd)
        calls += 1
        if calls == 2:
            directory.rename(tmp_path / "old-fonts")
            directory.mkdir()
            font.write_bytes(b"replaced")
        return result

    monkeypatch.setattr(collector.os, "fstat", replace_parent)
    with pytest.raises(ManifestError):
        collector._font_identity(font)


@pytest.mark.parametrize(
    "kind", ["symlink", "existing", "missing_parent", "non_directory_parent", "inside_repo"]
)
def test_manifest_cli_refuses_invalid_output_before_measurement(tmp_path, monkeypatch, capsys, kind):
    from scripts import collect_ocr_runtime_manifest as collector

    repo = tmp_path / "repo"
    repo.mkdir()
    existing = tmp_path / "existing"
    existing.write_text("keep", encoding="utf-8")
    target = tmp_path / "uncreated.json"
    output = tmp_path / "manifest.json"
    if kind == "symlink":
        output.symlink_to(target)
    elif kind == "existing":
        output = existing
    elif kind == "missing_parent":
        output = tmp_path / "missing" / "manifest.json"
    elif kind == "non_directory_parent":
        output = existing / "manifest.json"
    else:
        output = repo / "manifest.json"

    def unexpected_measurement(*args, **kwargs):
        pytest.fail("invalid output reached model and environment measurement")

    monkeypatch.setattr(collector, "collect_manifest", unexpected_measurement)
    with pytest.raises(SystemExit) as exc:
        collector.main(
            ["--repo", str(repo), "--font", str(tmp_path / "unused.ttf"), "--output", str(output)]
        )
    assert exc.value.code == 2
    assert "measurement refused" in capsys.readouterr().err
    assert existing.read_text(encoding="utf-8") == "keep"
    assert not target.exists()
    if kind == "symlink":
        assert output.is_symlink()
    elif kind != "existing":
        assert not output.exists()


def test_manifest_cli_writes_new_private_output(tmp_path, monkeypatch):
    from scripts import collect_ocr_runtime_manifest as collector

    repo = tmp_path / "repo"
    repo.mkdir()
    output = tmp_path / "manifest.json"
    monkeypatch.setattr(collector, "collect_manifest", lambda *args: {"sample": True})
    assert collector.main(
        ["--repo", str(repo), "--font", str(tmp_path / "unused.ttf"), "--output", str(output)]
    ) == 0
    assert json.loads(output.read_text(encoding="utf-8")) == {"sample": True}
