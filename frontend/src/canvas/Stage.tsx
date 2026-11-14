import { useLayoutEffect, useRef, useState } from 'react';
import type { KeyboardEvent, PointerEvent } from 'react';
import { Maximize2, Minus, Plus } from 'lucide-react';
import { layerName } from './api';
import type { Design, DesignNode, Operation } from './api';

interface Drag {
    node: DesignNode;
    revision: number;
    pointer: number;
    clientX: number;
    clientY: number;
    scaleX: number;
    scaleY: number;
    dx: number;
    dy: number;
}

export function Stage({
    design,
    svg,
    selected,
    busy,
    onSelect,
    onEdit,
}: {
    design: Design;
    svg: string | null;
    selected: string | null;
    busy: boolean;
    onSelect: (id: string) => void;
    onEdit: (operations: Operation[]) => Promise<boolean>;
}) {
    const surface = useRef<HTMLDivElement>(null);
    const viewport = useRef<HTMLDivElement>(null);
    const drag = useRef<Drag | null>(null);
    const [offset, setOffset] = useState({ x: 0, y: 0, revision: design.revision });
    const [zoom, setZoom] = useState<number | null>(null);
    const [fitZoom, setFitZoom] = useState(50);
    const displayZoom = zoom ?? fitZoom;
    const node = design.nodes.find((item) => item.node_id === selected);
    const dx = offset.revision === design.revision ? offset.x : 0;
    const dy = offset.revision === design.revision ? offset.y : 0;

    useLayoutEffect(() => {
        const element = viewport.current;
        if (!element) return;
        const measure = () => {
            const style = getComputedStyle(element);
            const width =
                element.clientWidth -
                parseFloat(style.paddingLeft) -
                parseFloat(style.paddingRight);
            const maximumHeight = parseFloat(style.maxHeight);
            const height = Number.isFinite(maximumHeight)
                ? maximumHeight - parseFloat(style.paddingTop) - parseFloat(style.paddingBottom)
                : Infinity;
            setFitZoom(
                Math.max(
                    10,
                    Math.min(width / design.page.width, height / design.page.height, 2) * 100,
                ),
            );
        };
        const observer = new ResizeObserver(measure);
        observer.observe(element);
        measure();
        return () => observer.disconnect();
    }, [design.page.width, design.page.height]);

    useLayoutEffect(() => {
        if (!node || !surface.current) return;
        const group = surface.current.querySelector(`g[data-node-id="${node.node_id}"]`);
        if (!group) return;
        const rotation = node.rotation
            ? `rotate(${node.rotation} ${node.x + node.width / 2} ${node.y + node.height / 2})`
            : '';
        group.setAttribute('transform', `translate(${dx} ${dy}) ${rotation}`);
    }, [svg, node, dx, dy]);

    function cancel() {
        drag.current = null;
        setOffset({ x: 0, y: 0, revision: design.revision });
    }

    function down(event: PointerEvent<HTMLDivElement>) {
        if (busy || event.button !== 0 || !(event.target instanceof Element)) return;
        const id = event.target.closest('[data-node-id]')?.getAttribute('data-node-id');
        const target = design.nodes.find((item) => item.node_id === id);
        if (!target) return;
        onSelect(target.node_id);
        surface.current?.focus();
        if (target.locked) return;
        const box = surface.current?.querySelector('svg')?.getBoundingClientRect();
        if (!box?.width || !box.height) return;
        event.preventDefault();
        event.currentTarget.setPointerCapture(event.pointerId);
        drag.current = {
            node: target,
            revision: design.revision,
            pointer: event.pointerId,
            clientX: event.clientX,
            clientY: event.clientY,
            scaleX: design.page.width / box.width,
            scaleY: design.page.height / box.height,
            dx: 0,
            dy: 0,
        };
        setOffset({ x: 0, y: 0, revision: design.revision });
    }

    function move(event: PointerEvent<HTMLDivElement>) {
        const current = drag.current;
        if (!current || current.pointer !== event.pointerId) return;
        current.dx = Math.round((event.clientX - current.clientX) * current.scaleX);
        current.dy = Math.round((event.clientY - current.clientY) * current.scaleY);
        setOffset({ x: current.dx, y: current.dy, revision: current.revision });
    }

    async function up(event: PointerEvent<HTMLDivElement>) {
        const current = drag.current;
        if (!current || current.pointer !== event.pointerId) return;
        drag.current = null;
        if (current.dx || current.dy) {
            await onEdit([
                {
                    op: 'transform_node',
                    node_id: current.node.node_id,
                    x: current.node.x + current.dx,
                    y: current.node.y + current.dy,
                },
            ]);
        }
        setOffset({ x: 0, y: 0, revision: current.revision });
    }

    function key(event: KeyboardEvent<HTMLDivElement>) {
        if (event.key === 'Escape') {
            cancel();
            return;
        }
        if (!node || node.locked || busy || drag.current) return;
        const step = event.shiftKey ? 10 : 1;
        let x = node.x,
            y = node.y;
        switch (event.key) {
            case 'ArrowLeft':
                x -= step;
                break;
            case 'ArrowRight':
                x += step;
                break;
            case 'ArrowUp':
                y -= step;
                break;
            case 'ArrowDown':
                y += step;
                break;
            default:
                return;
        }
        event.preventDefault();
        void onEdit([{ op: 'transform_node', node_id: node.node_id, x, y }]);
    }

    return (
        <section className="canvas-stage" aria-label="设计预览">
            <div className="canvas-stage-heading">
                <span>
                    {design.page.width} × {design.page.height}
                </span>
                <span>{node ? layerName(node) : '选择一个图层开始编辑'}</span>
            </div>
            <div className="canvas-scrollport" ref={viewport}>
                <div
                    className="canvas-paper"
                    style={{
                        width: `${(design.page.width * displayZoom) / 100}px`,
                        aspectRatio: `${design.page.width}/${design.page.height}`,
                    }}
                >
                    <div
                        ref={surface}
                        className={`canvas-surface${busy ? ' is-busy' : ''}`}
                        tabIndex={0}
                        role="region"
                        aria-label="海报画布"
                        aria-busy={busy}
                        onPointerDown={down}
                        onPointerMove={move}
                        onPointerUp={(event) => void up(event)}
                        onPointerCancel={cancel}
                        onKeyDown={key}
                        dangerouslySetInnerHTML={{ __html: svg ?? '' }}
                    />
                    {!svg && <div className="canvas-loading">预览暂不可用</div>}
                    {svg && node?.visible && (
                        <div
                            className={`canvas-selection${node.locked ? ' is-locked' : ''}`}
                            data-testid="canvas-selection"
                            style={{
                                left: `${((node.x + dx) / design.page.width) * 100}%`,
                                top: `${((node.y + dy) / design.page.height) * 100}%`,
                                width: `${(node.width / design.page.width) * 100}%`,
                                height: `${(node.height / design.page.height) * 100}%`,
                                transform: `rotate(${node.rotation}deg)`,
                            }}
                        >
                            <span>
                                {layerName(node)}
                                {node.locked ? ' · 已锁定' : ''}
                            </span>
                        </div>
                    )}
                </div>
            </div>
            <div className="canvas-zoom" aria-label="画布缩放">
                <button
                    type="button"
                    aria-label="缩小画布"
                    onClick={() => setZoom(Math.max(10, displayZoom - 25))}
                >
                    <Minus size={16} />
                </button>
                <output>{Math.round(displayZoom)}%</output>
                <button
                    type="button"
                    aria-label="放大画布"
                    onClick={() => setZoom(Math.min(200, displayZoom + 25))}
                >
                    <Plus size={16} />
                </button>
                <button type="button" onClick={() => setZoom(null)}>
                    <Maximize2 size={15} />
                    适应窗口
                </button>
            </div>
        </section>
    );
}
