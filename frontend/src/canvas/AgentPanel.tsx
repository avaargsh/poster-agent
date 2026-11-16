import { useEffect, useRef, useState } from 'react';
import type { Design } from './api';
import { CanvasError } from './api';
import {
    agentCapabilities,
    cancelAgentRun,
    listAgentRuns,
    readAgentRun,
    submitAgentRun,
} from './agent';
import type { AgentCapabilities, AgentRequest, AgentRun } from './agent';

const statusText = {
    RUNNING: '正在生成建议',
    SUCCEEDED: '建议已生成',
    FAILED: '生成失败',
    CANCELLED: '已停止',
    UNKNOWN: '结果待核对',
};
const errorText: Record<string, string> = {
    MODEL_TIMEOUT: '模型执行超时，可检查已有建议后再决定是否发起新任务。',
    REVISION_CONFLICT: '设计版本已变化，请基于当前版本发起新任务。',
    MODEL_EXECUTION_FAILED: 'Codex 执行未完成，请检查本机登录状态与服务配置。',
    NO_EDIT_PROPOSAL: '模型没有生成可保存的修改建议，可补充具体需求后再试。',
    SERVER_INTERRUPTED: '服务中断，执行结果待核对。已有建议可单独查看。',
};

export function AgentPanel({
    design,
    busy,
    drafts,
    onProposal,
    onClose,
}: {
    design: Design;
    busy: boolean;
    drafts: Map<string, string>;
    onProposal: (id: string) => void;
    onClose: () => void;
}) {
    const [prompt, setPrompt] = useState(() => drafts.get(design.document_id) ?? '');
    const [capabilities, setCapabilities] = useState<AgentCapabilities | null>(null);
    const [runs, setRuns] = useState<AgentRun[]>([]);
    const [run, setRun] = useState<AgentRun | null>(null);
    const [pending, setPending] = useState<AgentRequest | null>(null);
    const [working, setWorking] = useState(false);
    const [error, setError] = useState('');
    const life = useRef(new AbortController());
    const writing = useRef(false);

    function draft(value: string) {
        setPrompt(value);
        drafts.set(design.document_id, value);
    }
    function update(next: AgentRun) {
        setRun(next);
        setRuns((previous) =>
            [next, ...previous.filter((item) => item.request_id !== next.request_id)].slice(0, 20),
        );
    }
    async function load(signal: AbortSignal) {
        setWorking(true);
        setError('');
        try {
            const [config, rows] = await Promise.all([
                agentCapabilities(signal),
                listAgentRuns(design.document_id, signal),
            ]);
            if (signal.aborted) return;
            setCapabilities(config);
            setRuns(rows);
            setRun(rows[0] ?? null);
            if (!drafts.has(design.document_id) && rows[0]) draft(rows[0].prompt);
        } catch (reason) {
            if (!signal.aborted)
                setError(reason instanceof Error ? reason.message : 'AI 服务暂时不可用。');
        } finally {
            if (!signal.aborted) setWorking(false);
        }
    }
    useEffect(() => {
        const controller = new AbortController();
        life.current = controller;
        void load(controller.signal);
        return () => controller.abort();
    }, []);
    useEffect(() => {
        if (run?.status !== 'RUNNING') return;
        const id = run.request_id;
        const controller = new AbortController();
        let timer: ReturnType<typeof setTimeout>;
        async function poll() {
            try {
                const next = await readAgentRun(design.document_id, id, controller.signal);
                if (controller.signal.aborted) return;
                update(next);
                setError('');
                if (next.status !== 'RUNNING') return;
            } catch (reason) {
                if (controller.signal.aborted) return;
                setError(reason instanceof Error ? reason.message : '任务状态无法读取。');
            }
            timer = setTimeout(() => void poll(), 1200);
        }
        timer = setTimeout(() => void poll(), 800);
        return () => {
            controller.abort();
            clearTimeout(timer);
        };
    }, [run?.request_id, run?.status]);

    async function submit(body: AgentRequest) {
        if (writing.current) return;
        writing.current = true;
        setWorking(true);
        setError('');
        setPending(body);
        try {
            const next = await submitAgentRun(design.document_id, body, life.current.signal);
            if (!life.current.signal.aborted) {
                update(next);
                setPending(null);
            }
        } catch (reason) {
            if (!life.current.signal.aborted) {
                setError(
                    reason instanceof Error ? reason.message : '提交结果待核对，请重试原请求。',
                );
                if (
                    reason instanceof CanvasError &&
                    [
                        'AGENT_BUSY',
                        'AGENT_UNAVAILABLE',
                        'REVISION_CONFLICT',
                        'IDEMPOTENCY_CONFLICT',
                        'INVALID_COMMAND',
                    ].includes(reason.code)
                )
                    setPending(null);
            }
        } finally {
            writing.current = false;
            if (!life.current.signal.aborted) setWorking(false);
        }
    }
    async function cancel() {
        if (!run || writing.current) return;
        writing.current = true;
        setWorking(true);
        setError('');
        try {
            const next = await cancelAgentRun(
                design.document_id,
                run.request_id,
                life.current.signal,
            );
            if (!life.current.signal.aborted) update(next);
        } catch (reason) {
            if (!life.current.signal.aborted)
                setError(
                    reason instanceof Error ? reason.message : '停止结果待核对，请刷新任务状态。',
                );
        } finally {
            writing.current = false;
            if (!life.current.signal.aborted) setWorking(false);
        }
    }

    return (
        <aside className="canvas-inspector canvas-agent" aria-label="AI 修改">
            <div className="canvas-agent-heading">
                <h2>AI 修改</h2>
                <button type="button" onClick={onClose}>
                    关闭 AI
                </button>
            </div>
            <p className="canvas-hint">
                使用本机已登录的 Codex 账号与 CLI
                默认模型。设计文本、图层及素材元数据会发送到其模型服务。
            </p>
            {capabilities && !capabilities.available && (
                <p role="status">Codex 任务服务尚未就绪，请先配置本地服务。</p>
            )}
            {error && <p role="alert">{error}</p>}
            <form
                onSubmit={(event) => {
                    event.preventDefault();
                    void submit({
                        request_id: crypto.randomUUID(),
                        expected_revision: design.revision,
                        prompt: prompt.trim(),
                    });
                }}
            >
                <label className="canvas-field">
                    修改需求
                    <textarea
                        aria-label="AI 修改需求"
                        value={prompt}
                        maxLength={2000}
                        disabled={working}
                        placeholder="把活动价字号改为100，保留商品位置"
                        onChange={(event) => draft(event.target.value)}
                    />
                </label>
                <p className="canvas-hint">
                    基于当前保存版本 {design.revision} 生成建议，预览后由你确认应用。
                </p>
                <button
                    className="canvas-primary"
                    type="submit"
                    disabled={
                        busy ||
                        working ||
                        !capabilities?.available ||
                        run?.status === 'RUNNING' ||
                        pending !== null ||
                        !prompt.trim()
                    }
                >
                    使用 Codex 生成建议
                </button>
            </form>
            {pending && (
                <button
                    type="button"
                    disabled={working || busy}
                    onClick={() => void submit(pending)}
                >
                    重试原 AI 请求
                </button>
            )}
            <button type="button" disabled={working} onClick={() => void load(life.current.signal)}>
                刷新 AI 任务
            </button>
            {run && (
                <section className="canvas-agent-task" aria-label="当前 AI 任务">
                    <p role="status">
                        {statusText[run.status]} · 基于版本 {run.base_revision}
                    </p>
                    <p className="canvas-agent-prompt">{run.prompt}</p>
                    {run.error_code && run.status !== 'RUNNING' && (
                        <p>
                            {errorText[run.error_code] ??
                                (run.status === 'CANCELLED'
                                    ? '任务已停止，已生成的建议保留待确认。'
                                    : '任务未完成，可检查已有建议和服务配置。')}
                        </p>
                    )}
                    {run.status === 'RUNNING' && (
                        <>
                            <p className="canvas-hint">关闭面板后任务继续，可重新打开查看。</p>
                            <button type="button" disabled={working} onClick={() => void cancel()}>
                                停止 AI 任务
                            </button>
                        </>
                    )}
                    {run.proposal_id && (
                        <button
                            type="button"
                            disabled={busy || working}
                            onClick={() => {
                                if (run.proposal_id) onProposal(run.proposal_id);
                            }}
                        >
                            查看并预览建议
                        </button>
                    )}
                </section>
            )}
            {runs.length > 1 && (
                <div className="canvas-agent-runs" aria-label="最近 AI 任务">
                    {runs.map((item) => (
                        <button
                            key={item.request_id}
                            type="button"
                            disabled={working}
                            onClick={() => setRun(item)}
                        >
                            {statusText[item.status]} ·{' '}
                            {Array.from(item.prompt).slice(0, 28).join('')}
                        </button>
                    ))}
                </div>
            )}
        </aside>
    );
}
