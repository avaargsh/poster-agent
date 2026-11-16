import assert from 'node:assert/strict';
import test from 'node:test';
import { parseRevisionPage } from './history';

const row = {
    revision: 3,
    title: '😀'.repeat(150),
    created_at: '2026-11-16T08:00:00Z',
    operations: ['restore_revision'],
};
const page = {
    document_id: 'doc_history',
    current_revision: 3,
    revisions: [row, { ...row, revision: 2 }],
    next_cursor: 2,
};

test('accepts bounded descending history and Unicode titles', () => {
    assert.equal(parseRevisionPage(page, 'doc_history').next_cursor, 2);
    assert.deepEqual(
        parseRevisionPage({ ...page, revisions: [], next_cursor: null }, 'doc_history').revisions,
        [],
    );
});

test('rejects mixed identities, invalid order and unbound cursors', () => {
    for (const value of [
        { ...page, document_id: 'doc_other' },
        { ...page, current_revision: 2 },
        { ...page, revisions: [row, row] },
        { ...page, next_cursor: 3 },
        { ...page, revisions: [{ ...row, created_at: 'invalid' }] },
        { ...page, revisions: [{ ...row, operations: [] }] },
    ])
        assert.throws(() => parseRevisionPage(value, 'doc_history'));
});
