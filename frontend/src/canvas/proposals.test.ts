import assert from 'node:assert/strict';
import test from 'node:test';
import { parseProposal } from './proposals';
import { CanvasError } from './api';

function payload(applied: number | null = null) {
    const node = {
        node_id: 'price',
        node_type: 'text',
        role: 'price',
        x: 72,
        y: 700,
        width: 600,
        height: 120,
        rotation: 0,
        opacity: 1,
        visible: true,
        locked: false,
        color: '#67e8f9',
        font_size: 88,
        lines: [{ text: '¥99.90', x: 72, y: 788 }],
    };
    const base = {
        schema_version: 1,
        document_id: 'doc_example',
        revision: 3,
        page: { width: 1024, height: 1024 },
        facts: { price: '99.90' },
        nodes: [node],
    };
    return {
        proposal_id: 'prop_' + 'a'.repeat(32),
        document_id: base.document_id,
        summary: '放大价格',
        base_revision: 3,
        status: 'PENDING',
        applied_revision: applied,
        base_document: base,
        candidate_document: { ...base, nodes: [{ ...node, font_size: 100 }] },
    };
}

test('suggestion candidate remains bound to its historical source revision', () => {
    const result = parseProposal(payload(), 'doc_example');
    assert.equal(result.base.revision, 3);
    assert.equal(result.candidate.revision, 3);
    assert.equal(result.status, 'PENDING');
});

test('a suggestion cannot cross document or revision identity', () => {
    assert.throws(() => parseProposal(payload(), 'doc_other'), CanvasError);
    const invalid = payload();
    invalid.candidate_document.revision = 4;
    assert.throws(() => parseProposal(invalid, 'doc_example'), CanvasError);
});

test('application status requires the matching new revision receipt', () => {
    const missing = payload();
    missing.status = 'APPLIED';
    assert.throws(() => parseProposal(missing, 'doc_example'), CanvasError);
    missing.applied_revision = 4;
    assert.equal(parseProposal(missing, 'doc_example').applied_revision, 4);
    missing.applied_revision = 9;
    assert.throws(() => parseProposal(missing, 'doc_example'), CanvasError);
});
