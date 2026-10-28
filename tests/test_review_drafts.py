"""Partial progress is durable, isolated, and never an official review."""

from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from app import storage
from app.main import app

client = TestClient(app)
VARIANTS = ("square", "feed_4x5_native", "feed_4x5_native_product_focus")


@pytest.fixture
def poster(tmp_path, monkeypatch, product_bytes):
    monkeypatch.setenv("POSTER_DATA_DIR", str(tmp_path / "posters"))
    monkeypatch.setenv("POSTER_EXECUTION_DB", str(tmp_path / "executions.sqlite3"))
    response = client.post(
        "/api/posters",
        files={"product": ("sku.png", product_bytes, "image/png")},
        data={"title": "审核进度测试", "price": "199", "background_mode": "local"},
    )
    assert response.status_code == 201, response.text
    return response.json()["poster_id"]


def endpoint(pid, variant="square", reviewer="reviewer-a", version=1):
    return (
        f"/api/posters/{pid}/versions/{version}/review-draft?variant={variant}&reviewer={reviewer}"
    )


def payload(info, **changes):
    return {
        "source_sha256": info["source_sha256"],
        "expected_revision": info["revision"],
        "fields": {"title": {"judgment": "", "reason": "只写了一部分，还未完成"}},
        **changes,
    }


@pytest.mark.parametrize("variant", VARIANTS)
def test_partial_draft_survives_new_client_without_becoming_a_review(poster, variant):
    url = endpoint(poster, variant)
    initial = client.get(url)
    assert initial.status_code == 200, initial.text
    assert initial.json()["revision"] == 0
    saved = client.put(url, json=payload(initial.json()))
    assert saved.status_code == 200, saved.text
    assert saved.json()["revision"] == 1
    with TestClient(app) as fresh:
        restored = fresh.get(url).json()
    assert restored["fields"] == {"title": {"judgment": "", "reason": "只写了一部分，还未完成"}}
    assert restored["revision"] == 1
    assert restored["status"] == "DRAFT"
    assert restored["automatically_accepted"] is False
    review_url = f"/api/posters/{poster}/versions/1/" + (
        "human-review" if variant == "square" else f"exports/{variant}/human-review"
    )
    assert client.get(review_url).json()["review_count"] == 0


def test_reviewer_template_and_version_are_separate(poster):
    info = client.get(endpoint(poster)).json()
    assert client.put(endpoint(poster), json=payload(info)).status_code == 200
    for variant, reviewer in [
        ("square", "reviewer-b"),
        ("feed_4x5_native", "reviewer-a"),
        ("feed_4x5_native_product_focus", "reviewer-a"),
    ]:
        empty = client.get(endpoint(poster, variant, reviewer)).json()
        assert empty["revision"] == 0 and empty["fields"] == {}
    assert client.post(f"/api/posters/{poster}/revisions", json={"price": "189"}).status_code == 201
    assert client.get(endpoint(poster, version=2)).json()["revision"] == 0
    assert client.get(endpoint(poster)).json()["revision"] == 1


def test_two_tabs_cannot_overwrite_each_other(poster):
    url = endpoint(poster)
    info = client.get(url).json()
    a = payload(info, fields={"title": {"judgment": "correct", "reason": "first"}})
    b = payload(info, fields={"title": {"judgment": "defective", "reason": "second"}})
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda body: client.put(url, json=body), [a, b]))
    assert sorted(r.status_code for r in results) == [200, 409]
    winner = next(r.json() for r in results if r.status_code == 200)
    assert client.get(url).json()["fields"] == winner["fields"]


def test_changed_source_rejects_old_payload_and_never_reuses_old_draft(poster):
    url = endpoint(poster)
    info = client.get(url).json()
    assert client.put(url, json=payload(info)).status_code == 200
    image = storage.version_path(poster, 1)
    raw = image.read_bytes()
    image.write_bytes(raw + b"changed")
    assert client.put(url, json=payload(info)).status_code == 409
    current = client.get(url).json()
    assert current["source_sha256"] != info["source_sha256"]
    assert current["fields"] == {} and current["revision"] == 0


def test_official_submission_is_not_masked_by_an_older_draft(poster):
    url = endpoint(poster)
    initial = client.get(url).json()
    assert client.put(url, json=payload(initial)).status_code == 200
    review_url = f"/api/posters/{poster}/versions/1/human-review"
    ctx = client.get(review_url).json()
    submitted = client.post(
        review_url,
        json={
            "reviewer": "reviewer-a",
            "image_sha256": ctx["image_sha256"],
            "spec_sha256": ctx["spec_sha256"],
            "fields": {
                key: {"judgment": "correct", "reason": ""} for key in ctx["fields_to_review"]
            },
        },
    )
    assert submitted.status_code == 201
    assert client.get(url).json()["fields"] == {}
    assert client.get(url).json()["revision"] == 0
    assert client.put(url, json=payload(initial)).status_code == 409


def test_invalid_fields_and_oversized_requests_are_rejected(poster):
    url = endpoint(poster)
    info = client.get(url).json()
    assert (
        client.put(
            url, json=payload(info, fields={"imaginary": {"judgment": "correct"}})
        ).status_code
        == 422
    )
    assert client.put(url, json=payload(info, expected_revision=True)).status_code == 422
    assert client.put(url, content=b"x" * 20_000).status_code == 413
    assert client.get(url).json()["revision"] == 0


def test_retry_after_lost_acknowledgment_does_not_create_a_false_conflict(poster):
    url = endpoint(poster)
    request = payload(client.get(url).json())
    first = client.put(url, json=request)
    retry = client.put(url, json=request)
    assert retry.status_code == 200, retry.text
    assert first.json()["revision"] == retry.json()["revision"] == 1


def test_atomic_write_failure_preserves_previous_progress(poster, monkeypatch):
    url = endpoint(poster)
    assert client.put(url, json=payload(client.get(url).json())).status_code == 200
    before = client.get(url).json()

    def fail_replace(*args):
        raise OSError("simulated disk failure")

    with monkeypatch.context() as changes:
        changes.setattr(storage.os, "replace", fail_replace)
        response = client.put(url, json=payload(before, fields={"title": {"judgment": "correct"}}))
    assert response.status_code == 409
    assert client.get(url).json() == before


def test_corrupt_or_symlinked_draft_is_never_overwritten(poster, tmp_path):
    url = endpoint(poster)
    request = payload(client.get(url).json())
    assert client.put(url, json=request).status_code == 200
    folder = storage.poster_dir(poster) / "versions" / "v1.square.review_drafts"
    path = next(folder.glob("*.json"))
    path.write_text("broken draft")
    assert client.get(url).status_code == 409
    assert client.put(url, json=request).status_code == 409
    assert path.read_text() == "broken draft"
    path.unlink()
    outside = tmp_path / "outside.json"
    outside.write_text("private outside file")
    path.symlink_to(outside)
    assert client.get(url).status_code == 409
    assert client.put(url, json=request).status_code == 409
    assert outside.read_text() == "private outside file"


def test_versions_directory_cannot_redirect_draft_writes(poster, tmp_path):
    url = endpoint(poster)
    request = payload(client.get(url).json())
    versions = storage.poster_dir(poster) / "versions"
    outside = tmp_path / "outside-versions"
    versions.rename(outside)
    versions.symlink_to(outside, target_is_directory=True)
    assert client.get(url).status_code == 409
    assert client.put(url, json=request).status_code == 409
    assert not list(outside.glob("*.review_drafts"))


def test_api_documents_partial_progress_and_static_assets_stay_local_only():
    schema = client.get("/openapi.json").json()["paths"][
        "/api/posters/{poster_id}/versions/{version}/review-draft"
    ]["put"]["requestBody"]["content"]["application/json"]["schema"]
    field = schema["properties"]["fields"]["additionalProperties"]
    assert "" in field["properties"]["judgment"]["enum"]
    assert "$ref" not in str(schema)
    assert client.get("/static/review-drafts.js").status_code == 200
    assert (
        client.get("/static/review-drafts.js", headers={"Host": "untrusted.example"}).status_code
        == 403
    )


def test_browser_reload_replaces_removed_fields_with_formal_baseline():
    """A different tab may remove a field; reload must not retain stale input."""
    import shutil
    import subprocess
    from pathlib import Path

    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is needed to exercise the browser adapter")
    script = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const html = fs.readFileSync('app/templates/index.html', 'utf8');
const start = html.indexOf('function reviewDraftSession(');
const end = html.indexOf('const workspaceNames=', start);
const judgment = {value:'defective'}, reason = {value:'stale unsaved reason'};
const row = {getAttribute:()=> 'title',querySelector:s=>s==='[data-judgment]'?judgment:reason};
const container = {querySelectorAll:()=>[row]};
const $ = id=>id==='humanFields'?container:{};
const errorText = value=>String(value);
const window = {ReviewDraftAutosave:class {constructor(options){this.options=options;}}};
eval(html.slice(start,end));
const context = {poster_id:'test',version:1,fields_to_review:['title']};
const session = reviewDraftSession(context,'square','reviewer-a','humanFields','data-human-field','human');
session.options.apply({});
assert.equal(judgment.value,'');
assert.equal(reason.value,'');
context.existing_review = {fields:{title:{judgment:'correct',reason:'formal baseline'}}};
session.options.apply({});
assert.equal(judgment.value,'correct');
assert.equal(reason.value,'formal baseline');
session.options.apply({title:{judgment:'unsure',reason:'new draft'}});
assert.equal(judgment.value,'unsure');
assert.equal(reason.value,'new draft');
"""
    result = subprocess.run(
        [node, "-e", script],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
