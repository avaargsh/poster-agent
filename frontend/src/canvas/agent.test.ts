import assert from 'node:assert/strict';
import test from 'node:test';
import { parseAgentRun } from './agent';

const row = {
    document_id: 'doc_agent',
    request_id: 'request_agent_001',
    base_revision: 1,
    prompt: '改大价格字号',
    status: 'RUNNING',
    error_code: null,
    proposal_id: null,
};

test('preserves durable running and terminal task states', () => {
    assert.equal(parseAgentRun(row, 'doc_agent').status, 'RUNNING');
    assert.equal(parseAgentRun({ ...row, status: 'CANCELLED' }, 'doc_agent').status, 'CANCELLED');
    assert.equal(
        parseAgentRun({ ...row, status: 'UNKNOWN', error_code: 'SERVER_INTERRUPTED' }, 'doc_agent')
            .status,
        'UNKNOWN',
    );
});
test('rejects wrong design identities and successful runs without a proposal', () => {
    for (const value of [
        { ...row, document_id: 'doc_other' },
        { ...row, status: 'SUCCEEDED' },
        { ...row, proposal_id: '../other' },
        { ...row, request_id: '../private' },
        { ...row, status: 'COMPLETED' },
    ])
        assert.throws(() => parseAgentRun(value, 'doc_agent'));
});
