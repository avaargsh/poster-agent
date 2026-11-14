import assert from 'node:assert/strict';
import test from 'node:test';
import { CanvasError, layerName, parseDesign, previewUrl } from './api';

function response() {
    const base = {
        x: 72,
        y: 88,
        width: 400,
        height: 120,
        rotation: 0,
        opacity: 1,
        visible: true,
        locked: false,
    };
    return {
        document: {
            schema_version: 1,
            document_id: 'doc_contract',
            revision: 4,
            page: { width: 1024, height: 1024 },
            facts: { price: '99.90', currency: 'CNY' },
            nodes: [
                {
                    ...base,
                    node_type: 'text',
                    node_id: 'title',
                    role: 'title',
                    color: '#ffffff',
                    font_size: 64,
                    lines: [{ text: '夏日清凉', x: 72, y: 152 }],
                },
                {
                    ...base,
                    node_type: 'text',
                    node_id: 'price',
                    role: 'price',
                    color: '#67e8f9',
                    font_size: 88,
                    lines: [{ text: '¥99.90', x: 72, y: 840 }],
                },
                {
                    ...base,
                    node_type: 'image',
                    node_id: 'product',
                    role: 'product',
                    asset_id: 'product',
                },
                {
                    ...base,
                    node_type: 'rect',
                    node_id: 'accent',
                    role: 'decoration',
                    fill: '#67e8f9',
                },
            ],
            asset_refs: [{ asset_id: 'product' }],
        },
    };
}

test('projects a versioned document without losing the exact decimal price', () => {
    const design = parseDesign(response());
    assert.equal(design.facts.price, '99.90');
    assert.equal(design.nodes.length, 4);
    assert.equal(previewUrl(design), '/api/designs/doc_contract/revisions/4/preview.svg');
});

test('rejects unsupported schema versions and invalid document identities', () => {
    const future = response();
    future.document.schema_version = 2;
    assert.throws(() => parseDesign(future), CanvasError);
    const invalid = response();
    invalid.document.document_id = '../other';
    assert.throws(() => parseDesign(invalid), CanvasError);
});

test('rejects non-finite coordinates and duplicate layer identities', () => {
    const invalid = response();
    invalid.document.nodes[0].x = Infinity;
    assert.throws(() => parseDesign(invalid), CanvasError);
    const duplicate = response();
    duplicate.document.nodes[1].node_id = 'title';
    assert.throws(() => parseDesign(duplicate), CanvasError);
});

test('unknown layer roles have a safe readable fallback', () => {
    const design = parseDesign(response());
    const node = { ...design.nodes[0], role: '__proto__' };
    assert.equal(layerName(node), node.node_id);
});
