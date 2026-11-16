import assert from 'node:assert/strict';
import test from 'node:test';
import { parseApprovalTarget } from './exports';

const design = { document_id: 'doc_export', revision: 3 };
const target = {
    ...design,
    spec_digest: 'a'.repeat(64),
    asset_manifest_digest: 'b'.repeat(64),
    approved: false,
};

test('binds export approval to the exact saved design version', () => {
    assert.deepEqual(parseApprovalTarget(target, design), target);
    assert.throws(() => parseApprovalTarget({ ...target, revision: 4 }, design));
    assert.throws(() => parseApprovalTarget({ ...target, document_id: 'doc_other' }, design));
});

test('rejects malformed approval digests and status', () => {
    for (const row of [
        { ...target, spec_digest: '' },
        { ...target, asset_manifest_digest: 'x'.repeat(64) },
        { ...target, approved: 'true' },
        null,
    ])
        assert.throws(() => parseApprovalTarget(row, design));
});
