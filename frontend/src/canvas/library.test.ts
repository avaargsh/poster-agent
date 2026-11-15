import assert from 'node:assert/strict';
import test from 'node:test';
import { parseDesignPage } from './library';

const item = { document_id: 'doc_a', title: '标题 <原文>', revision: 3 };

test('parses bounded design pages and terminal empty pages', () => {
    assert.deepEqual(parseDesignPage({ designs: [item], next_cursor: 'doc_a' }), {
        designs: [item],
        next_cursor: 'doc_a',
    });
    assert.deepEqual(parseDesignPage({ designs: [], next_cursor: null }), {
        designs: [],
        next_cursor: null,
    });
});

test('accepts backend-valid Unicode code point lengths', () => {
    const unicode = { ...item, title: '😀'.repeat(200) };
    assert.equal(
        parseDesignPage({ designs: [unicode], next_cursor: null }).designs[0]?.title,
        unicode.title,
    );
    assert.throws(() =>
        parseDesignPage({
            designs: [{ ...unicode, title: unicode.title + '字' }],
            next_cursor: null,
        }),
    );
});

test('rejects malformed identities, duplicate entries and mismatched cursors', () => {
    for (const page of [
        { designs: [item], next_cursor: 'doc_b' },
        { designs: [item, item], next_cursor: null },
        { designs: [{ ...item, revision: 1.5 }], next_cursor: null },
        { designs: [{ ...item, document_id: '../private' }], next_cursor: null },
        { designs: Array(51).fill(item), next_cursor: null },
        { designs: [], next_cursor: 'doc_a' },
    ])
        assert.throws(() => parseDesignPage(page));
});
