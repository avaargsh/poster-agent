import { useCallback, useEffect, useRef, useState } from 'react';
import { CanvasError } from './api';
import { readDesignPage } from './library';
import type { DesignPage } from './library';

export function DesignLibrary({
    currentId,
    revision,
    disabled,
    onOpen,
}: {
    currentId: string | null;
    revision: number | null;
    disabled: boolean;
    onOpen: (id: string) => void;
}) {
    const [page, setPage] = useState<DesignPage | null>(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');
    const requestId = useRef(0);
    const controller = useRef<AbortController | null>(null);
    const load = useCallback(async (cursor: string | null) => {
        controller.current?.abort();
        const control = new AbortController();
        controller.current = control;
        const id = ++requestId.current;
        setLoading(true);
        setError('');
        try {
            const next = await readDesignPage(cursor, control.signal);
            if (id !== requestId.current) return;
            setPage((previous) => {
                if (!cursor || !previous) return next;
                const ids = new Set(previous.designs.map((item) => item.document_id));
                return {
                    designs: [
                        ...previous.designs,
                        ...next.designs.filter((item) => !ids.has(item.document_id)),
                    ],
                    next_cursor: next.next_cursor,
                };
            });
        } catch (reason) {
            if (id !== requestId.current || control.signal.aborted) return;
            setError(reason instanceof CanvasError ? reason.message : '设计库暂时无法载入。');
        } finally {
            if (id === requestId.current) setLoading(false);
        }
    }, []);
    useEffect(() => {
        void load(null);
        return () => {
            ++requestId.current;
            controller.current?.abort();
        };
    }, [load, currentId, revision]);

    return (
        <section className="canvas-library" aria-label="设计库" aria-busy={loading}>
            <div className="canvas-library-heading">
                <h3>设计库</h3>
                <button type="button" disabled={loading} onClick={() => void load(null)}>
                    刷新列表
                </button>
            </div>
            {error && (
                <p className="canvas-hint" role="alert">
                    {error}
                </p>
            )}
            {page?.designs.map((item) => (
                <button
                    type="button"
                    className="canvas-library-item"
                    key={item.document_id}
                    title={item.document_id}
                    disabled={disabled}
                    aria-current={item.document_id === currentId ? 'true' : undefined}
                    onClick={() => onOpen(item.document_id)}
                >
                    <span>{item.title}</span>
                    <small>版本 {item.revision}</small>
                </button>
            ))}
            {loading && (
                <p className="canvas-hint" role="status">
                    正在载入设计库…
                </p>
            )}
            {!loading && !error && page?.designs.length === 0 && (
                <p className="canvas-hint">还没有设计，上传商品图片即可创建。</p>
            )}
            {page?.next_cursor && (
                <button
                    type="button"
                    disabled={loading}
                    onClick={() => void load(page.next_cursor)}
                >
                    加载更多设计
                </button>
            )}
            {error && (
                <button type="button" disabled={loading} onClick={() => void load(null)}>
                    重试载入设计库
                </button>
            )}
        </section>
    );
}
