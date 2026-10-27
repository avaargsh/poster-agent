"""M6 SKU review triage is a local draft navigator, never quality approval."""

from fastapi.testclient import TestClient

from app.main import app


def test_m6_sku_triage_is_accessible_and_navigates_without_new_write_api():
    page = TestClient(app).get("/")
    assert page.status_code == 200
    html = page.text
    for marker in (
        'id="m6ReviewQueueFilter"',
        'id="m6ReviewQueueSummary"',
        'id="m6ReviewQueue"',
        'aria-label="M6 SKU 待办清单"',
        "function renderM6ReviewQueue()",
        "m6ReviewDrafts.get(sku+'/'+variant)",
        "loadM6OCRReview(batchId,sku)",
        "m6ReviewQueueFilter').addEventListener('change'",
        "待人工验收",
    ):
        assert marker in html


def test_m6_triage_never_reuses_stale_frozen_evidence_on_refresh_error():
    html = TestClient(app).get("/").text
    # Pending/failed verification hides old actionable links and gates drafts.
    assert "let m6ReviewEvidenceReady=false;" in html
    assert "if(!m6ReviewEvidenceReady||!m6ReviewSource" in html
    assert "m6ReviewEvidenceReady=false;\n  renderM6ReviewQueue();" in html
    assert "m6ReviewEvidenceReady=true;\n  updateM6ReviewProgress();" in html
    assert "m6ReviewEvidenceReady=false;\n      renderM6ReviewQueue();" in html
    # Selecting a saved sidecar during verification failure must not re-enable import.
    assert "m6ReviewEvidenceReady&&m6ReviewSource&&currentBatchId===m6ReviewSource.batchId" in html
    assert "if(!file||!m6ReviewEvidenceReady||!source" in html
    assert "button.disabled=!m6ReviewEvidenceReady||!input.files.length;" in html
