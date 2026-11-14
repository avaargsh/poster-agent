import assert from 'node:assert/strict';
import test from 'node:test';
import { inspectorValues, isInspectorDirty, mergeInspectorDraft } from './inspectorDraft';
import type { DesignNode } from './api';

const node: DesignNode = {
    node_type: 'text',
    node_id: 'title',
    role: 'title',
    x: 72,
    y: 88,
    width: 880,
    height: 250,
    rotation: 0,
    opacity: 1,
    locked: false,
    visible: true,
    color: '#ffffff',
    font_size: 64,
    lines: [{ text: '已保存标题', x: 72, y: 152 }],
};

test('preserves draft copy while refreshing a separately committed position', () => {
    const base = inspectorValues(node, '99.90');
    const draft = { ...base, copy: '尚未保存的文案' };
    const incoming = inspectorValues({ ...node, x: 125 }, '99.90');
    const merged = mergeInspectorDraft(base, draft, incoming);
    assert.equal(merged.copy, draft.copy);
    assert.equal(merged.x, '125');
    assert.equal(isInspectorDirty(incoming, merged), true);
});

test('retains a cleared numeric field and recognizes canonical numeric saves', () => {
    const base = inspectorValues(node, '88.80');
    const draft = { ...base, width: '', price: '88.8', x: '72.00' };
    const incoming = inspectorValues({ ...node, y: 110 }, '99.90');
    const merged = mergeInspectorDraft(base, draft, incoming);
    assert.equal(merged.width, '');
    assert.equal(merged.y, '110');
    assert.equal(merged.price, '99.90');
    assert.equal(isInspectorDirty(base, { ...base, price: '88.8', x: '72.00' }), false);
});
