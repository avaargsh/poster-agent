# Operator-local OCR runtime manifest

This command measures the invoking environment without loading or invoking
PaddleOCR. It is an observation, not attestation of a server process.

From the provisioned server checkout and its locked Python environment,
configure the exact same POSTER_DESIGN_OCR_MODEL_CONFIG_JSON used by the
server (see design-ocr-offline-model-pins.md). Select the actual intended
Chinese font files explicitly, using canonical absolute paths.

```sh
uv run --no-sync python -m scripts.collect_ocr_runtime_manifest \
  --font /absolute/path/to/font.ttf \
  --output /private/existing-parent/runtime-manifest.json
```

The repository must have no reported tracked or untracked changes (ignored
files are not measured); the destination must be new and outside the
repository. The collector checks Git HEAD before and after measurement,
OS/Python identity, the repository DesignDocument renderer version,
installed PaddleOCR/CairoSVG and exactly one Paddle distribution, hashes
of one to eight explicitly supplied font files, and the same bounded model
tree/profile pins used by the OCR journal. The offline verifier requires the
journal renderer version and `cairosvg/<installed-version>` rasterizer
identity to match this manifest. It
does not include absolute model/font paths, credentials or environment
variable dumps. The output is created exclusively with mode 0600.

Run this in the intended server environment before the smoke, retain
the manifest privately, and compare model_profile_sha256 with the
executed journal. Package metadata is not loaded-module evidence;
the explicit font hashes do not prove font fallback or selection.
A clean Git status is not a source or build attestation. Measurements
are sequential and do not prevent concurrent modification. No server
process identity, read-only mounts or network isolation are verified.
All authority flags remain false. The real model run, operator isolation
evidence and independent human calibration remain separate gates.
