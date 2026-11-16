import { useEffect, useRef, useState } from 'react';
import { CanvasError } from './api';
import type { Design } from './api';
import { approveTarget, downloadApproved, readApprovalTarget } from './exports';
import type { ApprovalTarget } from './exports';

export function ExportPanel({
    design,
    disabled,
    onClose,
}: {
    design: Design;
    disabled: boolean;
    onClose: () => void;
}) {
    const [target, setTarget] = useState<ApprovalTarget | null>(null);
    const [busy, setBusy] = useState(false);
    const [checked, setChecked] = useState(false);
    const [token, setToken] = useState('');
    const [notice, setNotice] = useState('');
    const [error, setError] = useState('');
    const approvalId = useRef(crypto.randomUUID());
    const control = useRef(new AbortController());

    function describe(reason: unknown) {
        if (reason instanceof CanvasError && reason.code === 'DESIGN_RASTER_FONT_UNAVAILABLE')
            return 'PNG 渲染缺少设计声明的固定字体，请安装 Noto Sans CJK SC 并重新启动服务。';
        if (reason instanceof CanvasError && reason.code === 'REVISION_CONFLICT')
            return '设计已更新，请重新载入并核对新版本。';
        if (reason instanceof CanvasError && reason.code === 'DESIGN_RASTER_UNAVAILABLE')
            return '当前服务缺少 PNG 渲染组件，可下载 SVG，或按导出文档安装 CairoSVG。';
        if (reason instanceof CanvasError && reason.code === 'DESIGN_REVIEWER_UNAUTHENTICATED')
            return '此服务需要审核凭据，请输入具有相应权限的令牌后重试。';
        return reason instanceof Error ? reason.message : '操作结果无法确认，请重试。';
    }

    async function load(signal: AbortSignal) {
        setBusy(true);
        setError('');
        try {
            const next = await readApprovalTarget(design, signal);
            if (!signal.aborted) setTarget(next);
        } catch (reason) {
            if (!signal.aborted) setError(describe(reason));
        } finally {
            if (!signal.aborted) setBusy(false);
        }
    }
    useEffect(() => {
        const controller = new AbortController();
        control.current = controller;
        void load(controller.signal);
        return () => controller.abort();
    }, []);

    async function approve() {
        if (!target) return;
        const signal = control.current.signal;
        setBusy(true);
        setError('');
        setNotice('');
        try {
            await approveTarget(target, approvalId.current, token, signal);
            const next = await readApprovalTarget(design, signal);
            if (!signal.aborted) {
                setTarget(next);
                setNotice('已确认当前版本，可选择格式下载。');
            }
        } catch (reason) {
            if (!signal.aborted) setError(describe(reason));
        } finally {
            if (!signal.aborted) setBusy(false);
        }
    }

    async function download(format: 'svg' | 'png') {
        if (!target) return;
        const signal = control.current.signal;
        setBusy(true);
        setError('');
        setNotice('');
        try {
            const blob = await downloadApproved(target, format, token, signal);
            if (signal.aborted) return;
            const url = URL.createObjectURL(blob);
            const anchor = document.createElement('a');
            anchor.href = url;
            anchor.download = `${design.document_id}-v${design.revision}.${format}`;
            document.body.append(anchor);
            anchor.click();
            anchor.remove();
            setTimeout(() => URL.revokeObjectURL(url), 1000);
            setNotice(`已开始下载版本 ${design.revision} 的 ${format.toUpperCase()} 文件。`);
        } catch (reason) {
            if (!signal.aborted) setError(describe(reason));
        } finally {
            if (!signal.aborted) setBusy(false);
        }
    }

    return (
        <aside className="canvas-inspector canvas-export" aria-label="审核与导出" aria-busy={busy}>
            <div className="canvas-export-heading">
                <h2>审核与导出</h2>
                <button type="button" onClick={onClose}>
                    关闭导出
                </button>
            </div>
            <p>当前保存版本 {design.revision}</p>
            <p className="canvas-hint">请核对画布中的文案、价格与商品图片。导出文件绑定此版本。</p>
            {error && <p role="alert">{error}</p>}
            {notice && <p role="status">{notice}</p>}
            {!target && (
                <button
                    type="button"
                    disabled={busy}
                    onClick={() => void load(control.current.signal)}
                >
                    重新核对版本
                </button>
            )}
            <fieldset disabled={busy || disabled}>
                <label className="canvas-field">
                    审核凭据（如已配置）
                    <input
                        type="password"
                        autoComplete="off"
                        aria-label="审核凭据"
                        value={token}
                        onChange={(event) => setToken(event.target.value)}
                    />
                </label>
                {target?.approved && (
                    <p className="canvas-export-approved">已确认 · 版本 {target.revision}</p>
                )}
                <label className="canvas-export-check">
                    <input
                        type="checkbox"
                        checked={checked}
                        onChange={(event) => setChecked(event.target.checked)}
                    />
                    已核对该版本的文案、价格和商品图片
                </label>
                <button
                    type="button"
                    className="canvas-primary"
                    disabled={!target || !checked}
                    onClick={() => void approve()}
                >
                    确认当前版本
                </button>
                <div className="canvas-export-formats">
                    <button
                        type="button"
                        disabled={!target?.approved}
                        onClick={() => void download('svg')}
                    >
                        下载 SVG
                    </button>
                    <button
                        type="button"
                        disabled={!target?.approved}
                        onClick={() => void download('png')}
                    >
                        下载 PNG
                    </button>
                </div>
            </fieldset>
        </aside>
    );
}
