import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { ArrowDown, ArrowUp, LockKeyhole, UnlockKeyhole } from 'lucide-react';
import type { FormEvent } from 'react';
import { layerName } from './api';
import type { Design, DesignNode, Operation } from './api';
import {
    inspectorValues,
    isInspectorDirty,
    mergeInspectorDraft,
    samePrice,
} from './inspectorDraft';
import type { InspectorDraft } from './inspectorDraft';

export function Inspector({
    design,
    node,
    busy,
    onEdit,
    drafts,
    onDirtyChange,
}: {
    design: Design;
    node: DesignNode;
    busy: boolean;
    onEdit: (operations: Operation[]) => Promise<boolean>;
    drafts: Map<string, InspectorDraft>;
    onDirtyChange: (nodeId: string, dirty: boolean) => void;
}) {
    const cached = drafts.get(node.node_id);
    const [values, setValues] = useState(
        () => cached?.values ?? inspectorValues(node, design.facts.price),
    );
    const baseline = useRef(cached?.baseline ?? inspectorValues(node, design.facts.price));
    const latest = useRef(values);
    latest.current = values;
    const { x, y, width, height, rotation, opacity, copy, price, color, fontSize } = values;
    function change(key: keyof typeof values, value: string) {
        setValues((current) => ({ ...current, [key]: value }));
    }
    useLayoutEffect(() => {
        const previous = baseline.current;
        const incoming = inspectorValues(node, design.facts.price);
        baseline.current = incoming;
        setValues((current) => mergeInspectorDraft(previous, current, incoming));
    }, [node, design.facts.price]);
    useEffect(() => {
        onDirtyChange(node.node_id, isInspectorDirty(baseline.current, values));
    }, [node.node_id, values, onDirtyChange]);
    useEffect(
        () => () => {
            drafts.set(node.node_id, { baseline: baseline.current, values: latest.current });
        },
        [drafts, node.node_id],
    );
    const [error, setError] = useState('');
    const index = design.nodes.findIndex((item) => item.node_id === node.node_id);
    const editableText =
        node.node_type === 'text' && ['title', 'subtitle', 'cta'].includes(node.role);

    async function save(event: FormEvent<HTMLFormElement>) {
        event.preventDefault();
        setError('');
        const operations: Operation[] = [];
        const geometry: Extract<Operation, { op: 'transform_node' }> = {
            op: 'transform_node',
            node_id: node.node_id,
        };
        const fields = { x, y, width, height, rotation };
        for (const key of [
            'x',
            'y',
            'width',
            'height',
            'rotation',
        ] satisfies (keyof typeof fields)[]) {
            const value = fields[key];
            if (!value.trim() || !Number.isFinite(Number(value))) {
                setError('请填写有效的位置和尺寸。');
                return;
            }
            if (Number(value) !== node[key]) geometry[key] = Number(value);
        }
        if (Object.keys(geometry).length > 2) operations.push(geometry);
        const style: Extract<Operation, { op: 'set_style' }> = {
            op: 'set_style',
            node_id: node.node_id,
        };
        if (!opacity.trim() || !Number.isFinite(Number(opacity))) {
            setError('请填写有效的透明度。');
            return;
        }
        if (Number(opacity) !== node.opacity) style.opacity = Number(opacity);
        if (node.node_type === 'text') {
            if (color !== node.color) style.color = color;
            if (!fontSize.trim() || !Number.isFinite(Number(fontSize))) {
                setError('请填写有效的字号。');
                return;
            }
            if (Number(fontSize) !== node.font_size) style.font_size = Number(fontSize);
            if (editableText && copy !== node.lines.map((line) => line.text).join(''))
                operations.push({ op: 'set_text', node_id: node.node_id, text: copy });
        } else if (node.node_type === 'rect' && color !== node.fill) style.color = color;
        if (Object.keys(style).length > 2) operations.push(style);
        if (node.role === 'price' && !samePrice(price, design.facts.price)) {
            if (!/^\d{1,6}(\.\d{1,2})?$/.test(price) || Number(price) <= 0) {
                setError('价格需大于0，最多保留两位小数。');
                return;
            }
            operations.push({
                op: 'set_price',
                price,
                source_text: `用户在画布中将活动价设为 ${price} 元`,
            });
        }
        if (!operations.length) {
            setError('当前没有需要保存的修改。');
            return;
        }
        await onEdit(operations);
    }

    return (
        <aside className="canvas-inspector" aria-label="图层属性">
            <div className="canvas-panel-title">
                <h2>{layerName(node)}</h2>
                <span>
                    {node.node_type === 'text'
                        ? '文字'
                        : node.node_type === 'image'
                          ? '图片'
                          : '形状'}
                </span>
            </div>
            <div className="canvas-layer-actions">
                <button
                    type="button"
                    disabled={busy}
                    onClick={() =>
                        void onEdit([
                            { op: 'set_node_lock', node_id: node.node_id, locked: !node.locked },
                        ])
                    }
                >
                    {node.locked ? <UnlockKeyhole size={16} /> : <LockKeyhole size={16} />}
                    {node.locked ? '解锁图层' : '锁定图层'}
                </button>
                <button
                    type="button"
                    aria-label="上移一层"
                    disabled={busy || node.locked || index === design.nodes.length - 1}
                    onClick={() =>
                        void onEdit([
                            { op: 'reorder_node', node_id: node.node_id, index: index + 1 },
                        ])
                    }
                >
                    <ArrowUp size={16} />
                </button>
                <button
                    type="button"
                    aria-label="下移一层"
                    disabled={busy || node.locked || index === 0}
                    onClick={() =>
                        void onEdit([
                            { op: 'reorder_node', node_id: node.node_id, index: index - 1 },
                        ])
                    }
                >
                    <ArrowDown size={16} />
                </button>
            </div>
            {node.locked && <p className="canvas-hint">解锁后可以修改这个图层。</p>}
            <form onSubmit={(event) => void save(event)}>
                <fieldset disabled={busy || node.locked}>
                    {editableText && (
                        <label className="canvas-field">
                            图层文案
                            <textarea
                                aria-label="图层文案"
                                maxLength={200}
                                required
                                value={copy}
                                onChange={(event) => change('copy', event.target.value)}
                            />
                        </label>
                    )}
                    {node.role === 'price' && (
                        <label className="canvas-field">
                            活动价（元）
                            <input
                                aria-label="活动价"
                                inputMode="decimal"
                                required
                                value={price}
                                onChange={(event) => change('price', event.target.value)}
                            />
                        </label>
                    )}
                    <h3>位置与尺寸</h3>
                    <div className="canvas-field-grid">
                        <label>
                            X
                            <input
                                aria-label="图层 X"
                                type="number"
                                min={-8192}
                                max={8192}
                                step="any"
                                required
                                value={x}
                                onChange={(event) => change('x', event.target.value)}
                            />
                        </label>
                        <label>
                            Y
                            <input
                                aria-label="图层 Y"
                                type="number"
                                min={-8192}
                                max={8192}
                                step="any"
                                required
                                value={y}
                                onChange={(event) => change('y', event.target.value)}
                            />
                        </label>
                        <label>
                            宽度
                            <input
                                aria-label="图层宽度"
                                type="number"
                                min={0.01}
                                max={4096}
                                step="any"
                                required
                                value={width}
                                onChange={(event) => change('width', event.target.value)}
                            />
                        </label>
                        <label>
                            高度
                            <input
                                aria-label="图层高度"
                                type="number"
                                min={0.01}
                                max={4096}
                                step="any"
                                required
                                value={height}
                                onChange={(event) => change('height', event.target.value)}
                            />
                        </label>
                        <label>
                            旋转
                            <input
                                aria-label="图层旋转"
                                type="number"
                                min={-360}
                                max={360}
                                step="any"
                                required
                                value={rotation}
                                onChange={(event) => change('rotation', event.target.value)}
                            />
                        </label>
                        <label>
                            透明度
                            <input
                                aria-label="图层透明度"
                                type="number"
                                min={0}
                                max={1}
                                step={0.05}
                                required
                                value={opacity}
                                onChange={(event) => change('opacity', event.target.value)}
                            />
                        </label>
                    </div>
                    {node.node_type !== 'image' && (
                        <>
                            <h3>外观</h3>
                            <div className="canvas-field-grid">
                                <label>
                                    颜色
                                    <input
                                        aria-label="图层颜色"
                                        type="color"
                                        value={color}
                                        onChange={(event) => change('color', event.target.value)}
                                    />
                                </label>
                                {node.node_type === 'text' && (
                                    <label>
                                        字号
                                        <input
                                            aria-label="图层字号"
                                            type="number"
                                            min={8}
                                            max={256}
                                            step="any"
                                            required
                                            value={fontSize}
                                            onChange={(event) =>
                                                change('fontSize', event.target.value)
                                            }
                                        />
                                    </label>
                                )}
                            </div>
                        </>
                    )}
                    {error && (
                        <p role="alert" className="canvas-field-error">
                            {error}
                        </p>
                    )}
                    <button className="canvas-primary canvas-save" type="submit">
                        保存修改
                    </button>
                </fieldset>
            </form>
        </aside>
    );
}
