# DesignDocument OCR: operator-pinned local models (v1)

This is an **opt-in, fail-closed model-source gate** for the local
`/api/designs/{id}/revisions/{rev}/ocr-runs` endpoint. It does **not**
attest model identity, enforce network egress isolation, certify OCR quality,
or permit publication. Legacy poster OCR entrypoints retain their existing
separate adapter configuration.

## Why these model paths are mandatory

PaddleOCR 3.3 documents that omitting `text_detection_model_dir` or
`text_recognition_model_dir` may download the default official model.
The DesignDocument path therefore refuses to invoke a model unless the
operator supplies both explicit local directories and pinned directory digests.

Official parameter documentation:
https://www.paddleocr.ai/v3.3.0/en/version3.x/pipeline_usage/OCR.html

## Operator procedure

1. Use an explicitly provisioned local environment containing pinned
   PaddleOCR/PaddlePaddle packages, installed Chinese fonts, the operator
   selected detection model and recognition model. Disable outbound access at
   the OS/container/firewall boundary. This application does **not** firewall
   the third-party OCR runtime.
2. Place the two models under **separate canonical absolute directories**.
   Avoid symlinks, hard-linked writable weights, network mounts, and any
   untrusted process with write access. Keep model files immutable while a
   service is running.
3. Derive full-tree SHA-256 digests after provisioning:

```bash
uv run --no-sync python - <<'PY'
from pathlib import Path
from app.designs.ocr_model_policy import model_directory_sha256

for label, location in (
    ("detection", "/ABSOLUTE/LOCAL/DETECTION/MODEL"),
    ("recognition", "/ABSOLUTE/LOCAL/RECOGNITION/MODEL"),
):
    print(label, model_directory_sha256(Path(location)))
PY
```

4. Configure the two required settings **before** starting the server:

```text
POSTER_DESIGN_OCR_RUNS_ENABLED=1
POSTER_DESIGN_OCR_MODEL_CONFIG_JSON={"schema_version":1,"text_detection_model_dir":"/ABSOLUTE/LOCAL/DETECTION/MODEL","text_recognition_model_dir":"/ABSOLUTE/LOCAL/RECOGNITION/MODEL","text_detection_sha256":"<computed lowercase SHA-256>","text_recognition_sha256":"<computed lowercase SHA-256>"}
```

   Keep `POSTER_DESIGN_REVIEWERS_JSON` configured with a named `export`
   principal as documented by the journal. Neither the model directories
   nor the API must be exposed through a remote reverse proxy.
5. Restart the local process after changing the model profile; do not hot
   replace weights. Use a new operation ID for a new model profile.

## Exact local digest profile

The directory digest is SHA-256 of compact, UTF-8 JSON containing a sorted
list of `[relative POSIX path, file byte length, file SHA-256]` for all
regular files under the model directory. The scanner rejects symlinks,
special files, empty trees, unreadable entries, more than 256 files, deep
nesting, individual files larger than 2 GiB and bundles larger than 4 GiB.
Those limits are a v1 local policy and must be reviewed for larger models.

The `model_profile_sha256` journal field hashes canonical JSON of the
configured pinned profile (including both absolute paths and their
directory digests). A profile with changed files is rejected before
admission; files are rechecked immediately before and after local inference.
If a mutation is detected **after** the durable `EXECUTING` write, the
operation becomes `UNKNOWN / MODEL_PROFILE_CHANGED`, without observations.
A replay with changed profile identity is not the same idempotent request.

Existing SQLite records are migrated with a nullable
`model_profile_sha256` field; historic model provenance remains `null`.
Previous records are not retrospectively authenticated.

## Security and evidence caveats

- Local SHA-256 measurements are **not independent signatures** or an
  attestation that PaddleOCR loaded exactly these bytes. An operator
  controlling both files and manifest can create a new matching hash.
- File verification before/after inference cannot rule out an adversarial
  time-of-check/time-of-use modification during the call. Use an
  OS-enforced read-only or immutable image and restricted model access.
- Passing explicit local paths is not proof that no library performs
  outbound network calls. Enforce egress denial and inspect real runtime
  traces before claiming offline operation.
- The source tree does not contain, download or ship model weights. Default
  CI uses synthetic model directories and a substituted OCR engine;
  it **does not** verify real PaddleOCR model availability or performance.
- `EXECUTED` still means local observations only:
  `runtime_attested=false`, `quality_verified=false`,
  `release_ready=false`. External signed execution evidence, calibrated
  field/spatial checks, human review and 20 authorized real SKU results
  remain separate Issue #14 gates.
