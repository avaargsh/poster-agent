import { useCallback, useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import {
    ArrowLeft,
    ExternalLink,
    ImagePlus,
    Layers3,
    LockKeyhole,
    Redo2,
    Undo2,
} from 'lucide-react';
import {
    CanvasError,
    createDesign,
    editDesign,
    layerName,
    previewUrl,
    readDesign,
    readPreview,
    validId,
} from './api';
import type { Design, EditRequest, Operation } from './api';
import { Inspector } from './Inspector';
import { Stage } from './Stage';
import type { InspectorDraft } from './inspectorDraft';

interface Session {
    design: Design;
    svg: string | null;
}
interface Notice {
    text: string;
    code: string;
}
interface Recent {
    id: string;
    title: string;
}
interface FailedEdit {
    id: string;
    request: EditRequest;
}
const RECENT_KEY = 'poster.canvas.recent.v1';

function recentDesigns(): Recent[] {
    try {
        const value: unknown = JSON.parse(localStorage.getItem(RECENT_KEY) ?? '[]');
        if (!Array.isArray(value)) return [];
        return value
            .flatMap((entry: unknown) => {
                if (
                    typeof entry !== 'object' ||
                    entry === null ||
                    !('id' in entry) ||
                    !('title' in entry)
                )
                    return [];
                if (
                    typeof entry.id !== 'string' ||
                    !validId(entry.id) ||
                    typeof entry.title !== 'string'
                )
                    return [];
                return [{ id: entry.id, title: entry.title.slice(0, 35) }];
            })
            .slice(0, 10);
    } catch {
        return [];
    }
}

function describe(error: unknown): Notice {
    return error instanceof CanvasError
        ? { code: error.code, text: error.message }
        : { code: 'CONNECTION_FAILED', text: '请求结果无法确认，请检查服务后重新载入。' };
}

function setDocumentUrl(id: string) {
    const url = new URL(location.href);
    url.searchParams.set('document', id);
    history.replaceState(null, '', url);
}

export function CanvasApp() {
    const [session, setSession] = useState<Session | null>(null);
    const [selected, setSelected] = useState<string | null>(null);
    const [busy, setBusy] = useState<'load' | 'save' | 'create' | null>(null);
    const [notice, setNotice] = useState<Notice | null>(null);
    const [failedEdit, setFailedEdit] = useState<FailedEdit | null>(null);
    const [recent, setRecent] = useState(recentDesigns);
    const [openId, setOpenId] = useState('');
    const [storageNotice, setStorageNotice] = useState('');
    const [inspectorEpoch, setInspectorEpoch] = useState(0);
    const [dirtyNodes, setDirtyNodes] = useState<Record<string, boolean>>({});
    const drafts = useRef(new Map<string, InspectorDraft>());
    const active = useRef<Session | null>(null);
    const writing = useRef(false);
    const receipt = useRef<FailedEdit | null>(null);
    const loadSequence = useRef(0);
    const loadController = useRef<AbortController | null>(null);
    const markDirty = useCallback((id: string, dirty: boolean) => {
        setDirtyNodes((current) => (current[id] === dirty ? current : { ...current, [id]: dirty }));
    }, []);
    const hasDraft = Object.values(dirtyNodes).some(Boolean);

    function resetDrafts() {
        drafts.current = new Map();
        setDirtyNodes({});
        setInspectorEpoch((value) => value + 1);
    }

    useEffect(() => {
        if (!hasDraft) return;
        const warn = (event: BeforeUnloadEvent) => {
            event.preventDefault();
            event.returnValue = '';
        };
        window.addEventListener('beforeunload', warn);
        return () => window.removeEventListener('beforeunload', warn);
    }, [hasDraft]);

    function record(design: Design) {
        setDocumentUrl(design.document_id);
        const title = design.nodes.find((node) => node.role === 'title');
        const entry = {
            id: design.document_id,
            title:
                title?.node_type === 'text'
                    ? title.lines.map((line) => line.text).join('')
                    : design.document_id,
        };
        const list = [entry, ...recentDesigns().filter((item) => item.id !== entry.id)].slice(
            0,
            10,
        );
        setRecent(list);
        try {
            localStorage.setItem(RECENT_KEY, JSON.stringify(list));
            setStorageNotice('');
        } catch {
            setStorageNotice('最近记录未能写入浏览器，可通过当前网址继续打开设计。');
        }
    }

    function adopt(design: Design, svg: string | null) {
        const sameProject = active.current?.design.document_id === design.document_id;
        const next = { design, svg };
        active.current = next;
        setSession(next);
        setSelected((id) =>
            sameProject && design.nodes.some((node) => node.node_id === id)
                ? id
                : (design.nodes.find((node) => node.role === 'title')?.node_id ??
                  design.nodes[0]?.node_id ??
                  null),
        );
        record(design);
    }

    const open = useCallback(async (id: string) => {
        if (writing.current) return;
        loadController.current?.abort();
        const controller = new AbortController();
        loadController.current = controller;
        const sequence = ++loadSequence.current;
        setBusy('load');
        setNotice(null);
        try {
            const design = await readDesign(id.trim(), controller.signal);
            let svg: string | null = null;
            let previewError: Notice | null = null;
            try {
                svg = await readPreview(design, controller.signal);
            } catch (error) {
                previewError = {
                    ...describe(error),
                    code: 'PREVIEW_FAILED',
                    text: '设计已载入，预览加载失败。请重试预览。',
                };
            }
            if (sequence !== loadSequence.current || controller.signal.aborted) return;
            resetDrafts();
            adopt(design, svg);
            receipt.current = null;
            setFailedEdit(null);
            setNotice(previewError);
        } catch (error) {
            if (sequence === loadSequence.current && !controller.signal.aborted)
                setNotice(describe(error));
        } finally {
            if (sequence === loadSequence.current) setBusy(null);
        }
    }, []);

    useEffect(() => {
        const initial = new URL(location.href).searchParams.get('document');
        if (initial) void open(initial);
        const navigate = () => {
            const id = new URL(location.href).searchParams.get('document');
            if (writing.current) {
                if (active.current) setDocumentUrl(active.current.design.document_id);
                return;
            }
            if (id) void open(id);
        };
        window.addEventListener('popstate', navigate);
        return () => {
            window.removeEventListener('popstate', navigate);
            loadController.current?.abort();
        };
    }, [open]);

    async function create(event: FormEvent<HTMLFormElement>) {
        event.preventDefault();
        if (writing.current || receipt.current) return;
        const formElement = event.currentTarget;
        const data = new FormData(formElement);
        const image = data.get('product');
        if (!(image instanceof File) || !image.size || image.size > 8 * 1024 * 1024) {
            setNotice({ code: 'INVALID_IMAGE', text: '请选择大小不超过8 MB的商品图片。' });
            return;
        }
        if (!String(data.get('cta') ?? '').trim()) data.delete('cta');
        writing.current = true;
        ++loadSequence.current;
        loadController.current?.abort();
        setBusy('create');
        setNotice(null);
        try {
            const design = await createDesign(data);
            resetDrafts();
            adopt(design, null);
            formElement.reset();
            try {
                adopt(design, await readPreview(design));
            } catch {
                setNotice({
                    code: 'PREVIEW_FAILED',
                    text: '设计已保存，预览加载失败。请重试预览。',
                });
            }
        } catch (error) {
            setNotice(describe(error));
        } finally {
            writing.current = false;
            setBusy(null);
        }
    }

    async function submit(operations: Operation[], retry?: FailedEdit): Promise<boolean> {
        if (!active.current || writing.current || (receipt.current && !retry)) return false;
        const design = active.current.design;
        const attempt: FailedEdit = retry ?? {
            id: design.document_id,
            request: {
                command_id: crypto.randomUUID(),
                expected_revision: design.revision,
                actor: 'user',
                operations,
            },
        };
        writing.current = true;
        ++loadSequence.current;
        loadController.current?.abort();
        setBusy('save');
        setNotice(null);
        let acknowledged = false;
        try {
            const updated = await editDesign(attempt.id, attempt.request);
            acknowledged = true;
            receipt.current = null;
            setFailedEdit(null);
            adopt(updated, null);
            try {
                adopt(updated, await readPreview(updated));
            } catch {
                setNotice({
                    code: 'PREVIEW_FAILED',
                    text: '修改已保存，预览加载失败。请重试预览。',
                });
            }
            return true;
        } catch (error) {
            const problem = describe(error);
            if (!acknowledged && ['CONNECTION_FAILED', 'INVALID_RESPONSE'].includes(problem.code)) {
                receipt.current = attempt;
                setFailedEdit(attempt);
                setNotice({
                    code: 'RESULT_UNKNOWN',
                    text: '保存结果待确认。可重试原请求，或载入最新版本核对。',
                });
            } else {
                receipt.current = null;
                setFailedEdit(null);
                setNotice(problem);
            }
            return false;
        } finally {
            writing.current = false;
            setBusy(null);
        }
    }

    async function retryPreview() {
        if (!active.current || writing.current) return;
        const snapshot = active.current;
        const sequence = ++loadSequence.current;
        setBusy('load');
        try {
            const svg = await readPreview(snapshot.design);
            if (sequence === loadSequence.current) {
                adopt(snapshot.design, svg);
                setNotice(null);
            }
        } catch (error) {
            if (sequence === loadSequence.current)
                setNotice({ ...describe(error), code: 'PREVIEW_FAILED' });
        } finally {
            if (sequence === loadSequence.current) setBusy(null);
        }
    }

    const design = session?.design;
    const node = design?.nodes.find((item) => item.node_id === selected);
    const writeDisabled = Boolean(busy || failedEdit);
    return (
        <div className="canvas-app">
            <header className="canvas-topbar">
                <a className="canvas-back" href="/" aria-label="返回工作台">
                    <ArrowLeft size={17} />
                    <span>工作台</span>
                </a>
                <div className="canvas-brand">
                    <Layers3 size={22} />
                    <h1>图层画布</h1>
                </div>
                <div className="canvas-save-status" role="status">
                    {busy === 'save'
                        ? '正在保存…'
                        : busy === 'create'
                          ? '正在创建…'
                          : busy === 'load'
                            ? '正在载入…'
                            : failedEdit
                              ? '保存待核对'
                              : design
                                ? `${hasDraft ? '有未保存的属性修改' : '已保存'} · 版本 ${design.revision}`
                                : '准备创作'}
                </div>
                <div className="canvas-history-actions">
                    <button
                        type="button"
                        aria-label="撤销修改"
                        disabled={!design || writeDisabled}
                        onClick={() => void submit([{ op: 'undo' }])}
                    >
                        <Undo2 size={17} />
                        <span>撤销</span>
                    </button>
                    <button
                        type="button"
                        aria-label="重做修改"
                        disabled={!design || writeDisabled}
                        onClick={() => void submit([{ op: 'redo' }])}
                    >
                        <Redo2 size={17} />
                        <span>重做</span>
                    </button>
                </div>
            </header>
            {notice && (
                <div className="canvas-notice" role="alert">
                    <span>{notice.text}</span>
                    {failedEdit && (
                        <button
                            type="button"
                            disabled={Boolean(busy)}
                            onClick={() => void submit([], failedEdit)}
                        >
                            重试原保存请求
                        </button>
                    )}
                    {design && (failedEdit || notice.code === 'REVISION_CONFLICT') && (
                        <button
                            type="button"
                            disabled={Boolean(busy)}
                            onClick={() => void open(design.document_id)}
                        >
                            放弃草稿并载入最新版本
                        </button>
                    )}
                    {notice.code === 'PREVIEW_FAILED' && (
                        <button
                            type="button"
                            disabled={Boolean(busy)}
                            onClick={() => void retryPreview()}
                        >
                            重试预览
                        </button>
                    )}
                </div>
            )}
            <main className="canvas-layout">
                <aside className="canvas-library" aria-label="设计与图层">
                    <details className="canvas-create" open={!session}>
                        <summary>
                            <ImagePlus size={18} />
                            新建设计
                        </summary>
                        <form onSubmit={(event) => void create(event)}>
                            <fieldset disabled={writeDisabled}>
                                <label className="canvas-upload">
                                    商品图片
                                    <input
                                        aria-label="商品图片"
                                        name="product"
                                        type="file"
                                        accept="image/png,image/jpeg,image/webp"
                                        required
                                    />
                                    <small>PNG、JPG、WebP，最大8 MB</small>
                                </label>
                                <label className="canvas-field">
                                    主标题
                                    <input
                                        name="title"
                                        aria-label="新设计主标题"
                                        maxLength={35}
                                        placeholder="例如，夏日清凉特惠"
                                        required
                                    />
                                </label>
                                <label className="canvas-field">
                                    活动价（元）
                                    <input
                                        name="price"
                                        aria-label="新设计活动价"
                                        inputMode="decimal"
                                        pattern="[0-9]{1,6}(\.[0-9]{1,2})?"
                                        placeholder="99.90"
                                        required
                                    />
                                </label>
                                <label className="canvas-field">
                                    按钮文案（可选）
                                    <input
                                        name="cta"
                                        aria-label="新设计按钮文案"
                                        maxLength={12}
                                        placeholder="例如，立即选购"
                                    />
                                </label>
                                <button className="canvas-primary" type="submit">
                                    创建画布
                                </button>
                            </fieldset>
                        </form>
                    </details>
                    <details className="canvas-open">
                        <summary>打开已有设计</summary>
                        <form
                            onSubmit={(event) => {
                                event.preventDefault();
                                void open(openId);
                            }}
                        >
                            <label className="canvas-field">
                                设计 ID
                                <input
                                    aria-label="打开设计 ID"
                                    value={openId}
                                    onChange={(event) => setOpenId(event.target.value)}
                                    required
                                />
                            </label>
                            <button type="submit" disabled={writeDisabled}>
                                打开设计
                            </button>
                        </form>
                        {recent.length > 0 && (
                            <div className="canvas-recent">
                                <h3>最近打开</h3>
                                {recent.map((item) => (
                                    <button
                                        key={item.id}
                                        type="button"
                                        title={item.id}
                                        disabled={writeDisabled}
                                        onClick={() => void open(item.id)}
                                    >
                                        {item.title}
                                    </button>
                                ))}
                            </div>
                        )}
                    </details>
                    {storageNotice && (
                        <p className="canvas-hint" role="status">
                            {storageNotice}
                        </p>
                    )}
                    {design && (
                        <section className="canvas-layer-list" aria-label="图层列表">
                            <h2>
                                图层<span>{design.nodes.length}</span>
                            </h2>
                            {[...design.nodes].reverse().map((item) => (
                                <button
                                    key={item.node_id}
                                    type="button"
                                    className={item.node_id === selected ? 'is-selected' : ''}
                                    aria-pressed={item.node_id === selected}
                                    aria-label={`选择${layerName(item)}图层`}
                                    disabled={Boolean(busy)}
                                    onClick={() => setSelected(item.node_id)}
                                >
                                    <span className="canvas-layer-symbol">
                                        {item.node_type === 'text'
                                            ? 'T'
                                            : item.node_type === 'image'
                                              ? '▧'
                                              : '▭'}
                                    </span>
                                    <span>{layerName(item)}</span>
                                    {item.locked && <LockKeyhole size={14} />}
                                </button>
                            ))}
                        </section>
                    )}
                    {design && (
                        <div className="canvas-project-info">
                            <label className="canvas-field">
                                设计 ID
                                <input
                                    aria-label="当前设计 ID"
                                    readOnly
                                    value={design.document_id}
                                />
                            </label>
                            <a href={previewUrl(design)} target="_blank" rel="noreferrer">
                                打开 SVG 预览
                                <ExternalLink size={13} />
                            </a>
                        </div>
                    )}
                </aside>
                {session ? (
                    <Stage
                        design={session.design}
                        svg={session.svg}
                        selected={selected}
                        busy={writeDisabled}
                        onSelect={setSelected}
                        onEdit={submit}
                    />
                ) : (
                    <section className="canvas-empty">
                        <div className="canvas-empty-paper">
                            <ImagePlus size={42} strokeWidth={1} />
                        </div>
                        <h2>从一张商品图开始</h2>
                        <p>上传图片，填写标题和活动价，即可创建可编辑的海报。</p>
                    </section>
                )}
                {design && node ? (
                    <Inspector
                        key={`${design.document_id}:${inspectorEpoch}:${node.node_id}`}
                        design={design}
                        node={node}
                        busy={writeDisabled}
                        onEdit={submit}
                        drafts={drafts.current}
                        onDirtyChange={markDirty}
                    />
                ) : (
                    <aside className="canvas-inspector canvas-inspector-empty">
                        <h2>图层属性</h2>
                        <p>选择图层后，在此调整内容、位置和外观。</p>
                    </aside>
                )}
            </main>
        </div>
    );
}
