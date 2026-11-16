import { useEffect, useRef, useState } from 'react';
import type { Design } from './api';
import { readHistoricalView, readRevisionPage } from './history';
import type { HistoryView, RevisionPage } from './history';

const labels: Record<string, string> = {
    create: '创建',
    set_text: '修改文案',
    set_price: '修改价格',
    set_style: '调整外观',
    transform_node: '调整位置',
    set_node_lock: '调整锁定',
    reorder_node: '调整图层顺序',
    undo: '撤销',
    redo: '重做',
    restore_revision: '恢复历史版本',
};

export function HistoryPanel({
    design,
    busy,
    hasDraft,
    view,
    onPreview,
    onRestore,
    onReload,
    onClose,
}: {
    design: Design;
    busy: boolean;
    hasDraft: boolean;
    view: HistoryView | null;
    onPreview: (view: HistoryView | null) => void;
    onRestore: (revision: number) => Promise<boolean>;
    onReload: () => void;
    onClose: () => void;
}) {
    const [page, setPage] = useState<RevisionPage | null>(null);
    const [loading, setLoading] = useState(false);
    const [previewing, setPreviewing] = useState<number | null>(null);
    const [error, setError] = useState('');
    const listControl = useRef<AbortController | null>(null);
    const previewControl = useRef<AbortController | null>(null);

    async function load(cursor: number | null) {
        listControl.current?.abort();
        const control = new AbortController();
        listControl.current = control;
        setLoading(true);
        setError('');
        try {
            const next = await readRevisionPage(design.document_id, cursor, control.signal);
            if (!control.signal.aborted)
                setPage((previous) => {
                    if (cursor === null || !previous) return next;
                    const seen = new Set(previous.revisions.map((item) => item.revision));
                    return {
                        ...next,
                        revisions: [
                            ...previous.revisions,
                            ...next.revisions.filter((item) => !seen.has(item.revision)),
                        ],
                    };
                });
        } catch (reason) {
            if (!control.signal.aborted)
                setError(reason instanceof Error ? reason.message : '历史列表无法载入。');
        } finally {
            if (!control.signal.aborted) setLoading(false);
        }
    }

    async function preview(revision: number) {
        previewControl.current?.abort();
        const control = new AbortController();
        previewControl.current = control;
        setPreviewing(revision);
        setError('');
        onPreview(null);
        try {
            const next = await readHistoricalView(design.document_id, revision, control.signal);
            if (!control.signal.aborted) {
                onPreview(next);
            }
        } catch (reason) {
            if (!control.signal.aborted)
                setError(reason instanceof Error ? reason.message : '历史预览无法载入。');
        } finally {
            if (!control.signal.aborted) setPreviewing(null);
        }
    }
    useEffect(() => {
        void load(null);
        return () => {
            listControl.current?.abort();
            previewControl.current?.abort();
        };
    }, []);
    const stale = page !== null && page.current_revision !== design.revision;
    return (
        <aside className="canvas-inspector canvas-history" aria-label="历史版本">
            <div className="canvas-history-heading">
                <h2>历史版本</h2>
                <button type="button" onClick={onClose}>
                    关闭历史
                </button>
            </div>
            <p>当前保存版本 {design.revision}</p>
            <button type="button" disabled={loading || busy} onClick={() => void load(null)}>
                刷新历史
            </button>
            {error && <p role="alert">{error}</p>}
            {stale && (
                <div role="status">
                    <p>设计已有更新，请载入最新版本后恢复。</p>
                    <button type="button" disabled={busy} onClick={onReload}>
                        {hasDraft ? '放弃属性草稿并载入最新版本' : '载入最新版本'}
                    </button>
                </div>
            )}
            <div className="canvas-history-list" aria-busy={loading}>
                {page?.revisions.map((item) => (
                    <button
                        type="button"
                        key={item.revision}
                        disabled={busy}
                        aria-label={`预览版本 ${item.revision}`}
                        aria-pressed={view?.design.revision === item.revision}
                        onClick={() => void preview(item.revision)}
                    >
                        <strong>
                            版本 {item.revision}
                            {item.revision === design.revision ? ' · 当前' : ''}
                        </strong>
                        <span>{item.title}</span>
                        <small>
                            {[...new Set(item.operations.map((op) => labels[op] ?? '编辑'))].join(
                                '、',
                            )}
                        </small>
                        <time dateTime={item.created_at}>
                            {new Date(item.created_at).toLocaleString('zh-CN')}
                        </time>
                    </button>
                ))}
            </div>
            {loading && <p role="status">正在载入历史…</p>}
            {page?.next_cursor && (
                <button
                    type="button"
                    disabled={busy || loading}
                    onClick={() => void load(page.next_cursor)}
                >
                    更早版本
                </button>
            )}
            {previewing !== null && <p role="status">正在预览版本 {previewing}…</p>}
            {view && (
                <div className="canvas-history-restore">
                    <p>正在只读预览版本 {view.design.revision}。</p>
                    <p className="canvas-hint">
                        恢复会将此版本的内容保存为新版本，包括图层锁定状态和价格。
                    </p>
                    <button
                        type="button"
                        className="canvas-primary"
                        disabled={
                            busy ||
                            stale ||
                            previewing !== null ||
                            view.design.revision === design.revision
                        }
                        onClick={() => void onRestore(view.design.revision)}
                    >
                        {hasDraft ? '放弃属性草稿并恢复此版本' : '恢复为新版本'}
                    </button>
                </div>
            )}
        </aside>
    );
}
