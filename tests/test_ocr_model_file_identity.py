import os

import pytest

from app.designs import ocr_model_policy as policy


def test_same_bytes_replacement_between_stat_and_open_is_rejected(tmp_path, monkeypatch):
    weights = tmp_path / "weights.bin"
    weights.write_bytes(b"same model bytes")
    original_open = os.open

    def replace_before_open(path, flags, *args, **kwargs):
        if path == weights:
            replacement = tmp_path / "replacement"
            replacement.write_bytes(weights.read_bytes())
            replacement.replace(weights)
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(policy.os, "open", replace_before_open)
    with pytest.raises(policy.OCRModelProfileError):
        policy.model_directory_sha256(tmp_path)


def test_same_size_mutation_during_read_is_rejected(tmp_path, monkeypatch):
    weights = tmp_path / "weights.bin"
    weights.write_bytes(b"original")
    original_fdopen = os.fdopen

    class MutatingReader:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.stream.close()

        def fileno(self):
            return self.stream.fileno()

        def read(self, size):
            chunk = self.stream.read(size)
            if chunk:
                weights.write_bytes(b"modified")
                os.utime(weights, ns=(1_000_000_000, 1_000_000_000))
            return chunk

    def mutate_after_read(fd, *args, **kwargs):
        return MutatingReader(original_fdopen(fd, *args, **kwargs))

    monkeypatch.setattr(policy.os, "fdopen", mutate_after_read)
    with pytest.raises(policy.OCRModelProfileError):
        policy.model_directory_sha256(tmp_path)


def test_unchanged_model_fingerprint_is_repeatable(tmp_path):
    (tmp_path / "weights.bin").write_bytes(b"stable weights")
    first = policy.model_directory_sha256(tmp_path)
    assert len(first) == 64
    assert policy.model_directory_sha256(tmp_path) == first
