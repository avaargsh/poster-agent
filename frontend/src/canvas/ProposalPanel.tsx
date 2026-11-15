import { useEffect, useRef, useState } from 'react';
import { CanvasError, layerName } from './api';
import type { Design } from './api';
import { latestProposalIds, proposalPreview, readProposal, validProposalId } from './proposals';
import type { EditProposal } from './proposals';

export interface ProposalView {
    proposal: EditProposal;
    svg: string;
}

function differences(proposal: EditProposal): string[] {
    const changes: string[] = [];
    if (proposal.base.facts.price !== proposal.candidate.facts.price)
        changes.push(`活动价 ¥${proposal.base.facts.price} → ¥${proposal.candidate.facts.price}`);
    for (const node of proposal.candidate.nodes) {
        const base = proposal.base.nodes.find((item) => item.node_id === node.node_id);
        if (!base) continue;
        const name = layerName(node);
        if (node.node_type === 'text' && base.node_type === 'text') {
            const before = base.lines.map((line) => line.text).join('');
            const after = node.lines.map((line) => line.text).join('');
            if (before !== after && node.role !== 'price')
                changes.push(`${name}文案 ${before} → ${after}`);
            if (base.font_size !== node.font_size)
                changes.push(`${name}字号 ${base.font_size} → ${node.font_size}`);
            if (base.color !== node.color)
                changes.push(`${name}颜色 ${base.color} → ${node.color}`);
        }
        if (node.node_type === 'rect' && base.node_type === 'rect' && base.fill !== node.fill)
            changes.push(`${name}颜色 ${base.fill} → ${node.fill}`);
        for (const [field, label] of [
            ['x', 'X'],
            ['y', 'Y'],
            ['width', '宽度'],
            ['height', '高度'],
            ['rotation', '旋转'],
            ['opacity', '透明度'],
        ] satisfies [keyof typeof base, string][]) {
            if (base[field] !== node[field])
                changes.push(`${name}${label} ${base[field]} → ${node[field]}`);
        }
    }
    if (
        proposal.base.nodes.map((node) => node.node_id).join() !==
        proposal.candidate.nodes.map((node) => node.node_id).join()
    )
        changes.push('图层顺序已调整');
    return changes;
}

export function ProposalPanel({
    design,
    busy,
    hasDraft,
    onPreview,
    onApply,
    onClose,
}: {
    design: Design;
    busy: boolean;
    hasDraft: boolean;
    onPreview: (value: ProposalView | null) => void;
    onApply: (proposalId: string) => Promise<boolean>;
    onClose: () => void;
}) {
    const [proposal, setProposal] = useState<EditProposal | null>(null);
    const [selectedId, setSelectedId] = useState(
        () => new URL(location.href).searchParams.get('proposal') ?? '',
    );
    const [ids, setIds] = useState<string[]>([]);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');
    const [refresh, setRefresh] = useState(0);
    const [showCandidate, setShowCandidate] = useState(false);
    const sequence = useRef(0);
    useEffect(() => {
        const controller = new AbortController();
        const current = ++sequence.current;
        setLoading(true);
        setError('');
        setShowCandidate(false);
        setProposal(null);
        onPreview(null);
        async function load() {
            try {
                const latest = await latestProposalIds(design.document_id, controller.signal);
                if (current !== sequence.current || controller.signal.aborted) return;
                setIds(latest);
                const id = selectedId || latest[0];
                if (!id) {
                    setProposal(null);
                    return;
                }
                if (!validProposalId(id)) throw new CanvasError('INVALID_ID', '修改建议链接无效。');
                const result = await readProposal(design.document_id, id, controller.signal);
                if (current !== sequence.current || controller.signal.aborted) return;
                setProposal(result);
                const url = new URL(location.href);
                url.searchParams.set('proposal', id);
                history.replaceState(null, '', url);
            } catch (problem) {
                if (current === sequence.current && !controller.signal.aborted)
                    setError(problem instanceof Error ? problem.message : '建议载入失败。');
            } finally {
                if (current === sequence.current && !controller.signal.aborted) setLoading(false);
            }
        }
        void load();
        return () => {
            controller.abort();
            ++sequence.current;
        };
    }, [design.document_id, design.revision, selectedId, refresh, onPreview]);

    useEffect(() => () => onPreview(null), [onPreview]);

    async function preview() {
        if (!proposal || busy) return;
        const current = sequence.current;
        setLoading(true);
        setError('');
        try {
            const svg = await proposalPreview(proposal);
            if (current !== sequence.current) return;
            onPreview({ proposal, svg });
            setShowCandidate(true);
        } catch (problem) {
            if (current === sequence.current)
                setError(problem instanceof Error ? problem.message : '预览失败。');
        } finally {
            if (current === sequence.current) setLoading(false);
        }
    }

    const stale =
        proposal?.status === 'STALE' ||
        (proposal?.status === 'PENDING' && proposal.base_revision !== design.revision);
    return (
        <aside className="canvas-inspector canvas-proposals" aria-label="修改建议">
            <div className="canvas-panel-title">
                <h2>修改建议</h2>
                <button type="button" onClick={onClose}>
                    返回属性
                </button>
            </div>
            <button
                type="button"
                disabled={busy || loading}
                onClick={() => setRefresh((value) => value + 1)}
            >
                刷新建议
            </button>
            {ids.length > 0 && (
                <label className="canvas-field">
                    选择建议
                    <select
                        aria-label="选择修改建议"
                        disabled={busy || loading}
                        value={proposal?.proposal_id ?? ''}
                        onChange={(event) => setSelectedId(event.target.value)}
                    >
                        {[...new Set([...(proposal ? [proposal.proposal_id] : []), ...ids])].map(
                            (id) => (
                                <option key={id} value={id}>
                                    {id}
                                </option>
                            ),
                        )}
                    </select>
                </label>
            )}
            {loading && (
                <p role="status" className="canvas-hint">
                    正在载入建议…
                </p>
            )}
            {error && (
                <p role="alert" className="canvas-field-error">
                    {error}
                </p>
            )}
            {!loading && !proposal && !error && (
                <p className="canvas-hint">
                    当前设计还没有修改建议。Agent 提交后，可在这里刷新查看。
                </p>
            )}
            {proposal && (
                <>
                    <h3>建议说明</h3>
                    <p className="canvas-proposal-summary">{proposal.summary}</p>
                    <p className="canvas-hint">基于版本 {proposal.base_revision}</p>
                    <ul className="canvas-proposal-changes">
                        {differences(proposal).map((change, index) => (
                            <li key={index}>{change}</li>
                        ))}
                    </ul>
                    <div className="canvas-proposal-preview-actions">
                        <button
                            type="button"
                            disabled={busy || loading}
                            onClick={() => {
                                onPreview(null);
                                setShowCandidate(false);
                            }}
                        >
                            当前设计
                        </button>
                        <button
                            type="button"
                            disabled={busy || loading}
                            aria-pressed={showCandidate}
                            onClick={() => void preview()}
                        >
                            预览建议
                        </button>
                    </div>
                    {stale && (
                        <p role="status" className="canvas-field-error">
                            建议已过期，请基于最新版本重新提出。
                        </p>
                    )}
                    {proposal.status === 'APPLIED' && (
                        <p role="status" className="canvas-hint">
                            已应用于版本 {proposal.applied_revision}
                        </p>
                    )}
                    {hasDraft && proposal.status === 'PENDING' && !stale && (
                        <p className="canvas-hint">应用建议会放弃当前未保存的属性草稿。</p>
                    )}
                    <button
                        type="button"
                        className="canvas-primary canvas-save"
                        disabled={
                            busy ||
                            loading ||
                            stale ||
                            proposal.status !== 'PENDING' ||
                            !showCandidate
                        }
                        onClick={() =>
                            void onApply(proposal.proposal_id).then(() =>
                                setRefresh((value) => value + 1),
                            )
                        }
                    >
                        {hasDraft ? '放弃属性草稿并应用建议' : '应用修改建议'}
                    </button>
                </>
            )}
        </aside>
    );
}
