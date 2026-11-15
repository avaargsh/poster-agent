import { CanvasError, parseDesign, readSvg, request, validId } from './api';
import type { Design } from './api';

export interface EditProposal {
    proposal_id: string;
    document_id: string;
    summary: string;
    base_revision: number;
    status: 'PENDING' | 'STALE' | 'APPLIED';
    applied_revision: number | null;
    base: Design;
    candidate: Design;
}

export function validProposalId(value: string) {
    return /^prop_[a-f0-9]{32}$/.test(value);
}

export function parseProposal(value: unknown, documentId: string): EditProposal {
    if (typeof value !== 'object' || value === null)
        throw new CanvasError('INVALID_RESPONSE', '建议数据不完整。');
    const row = Object.fromEntries(Object.entries(value));
    if (
        typeof row.proposal_id !== 'string' ||
        !validProposalId(row.proposal_id) ||
        row.document_id !== documentId ||
        typeof row.summary !== 'string' ||
        row.summary.length > 500
    )
        throw new CanvasError('INVALID_RESPONSE', '建议标识或说明无效。');
    if (row.status !== 'PENDING' && row.status !== 'STALE' && row.status !== 'APPLIED')
        throw new CanvasError('INVALID_RESPONSE', '建议状态不受支持。');
    const base = parseDesign({ document: row.base_document });
    const candidate = parseDesign({ document: row.candidate_document });
    if (
        base.document_id !== documentId ||
        candidate.document_id !== documentId ||
        base.revision !== row.base_revision ||
        candidate.revision !== base.revision
    )
        throw new CanvasError('INVALID_RESPONSE', '建议与基础版本不一致。');
    const applied = row.applied_revision;
    if (
        applied !== null &&
        (typeof applied !== 'number' || !Number.isSafeInteger(applied) || applied < 2)
    )
        throw new CanvasError('INVALID_RESPONSE', '应用版本无效。');
    if (
        (row.status === 'APPLIED') !== (applied !== null) ||
        (applied !== null && applied !== base.revision + 1)
    )
        throw new CanvasError('INVALID_RESPONSE', '应用凭据不完整。');
    return {
        proposal_id: row.proposal_id,
        document_id: documentId,
        summary: row.summary,
        base_revision: base.revision,
        status: row.status,
        applied_revision: applied,
        base,
        candidate,
    };
}

export async function readProposal(
    id: string,
    proposalId: string,
    signal?: AbortSignal,
): Promise<EditProposal> {
    if (!validId(id) || !validProposalId(proposalId))
        throw new CanvasError('INVALID_ID', '修改建议 ID 无效。');
    return parseProposal(
        await request<unknown>(
            `/api/designs/${id}/proposals/${proposalId}`,
            { signal },
            (response) => response.json(),
        ),
        id,
    );
}

export async function latestProposalIds(id: string, signal?: AbortSignal): Promise<string[]> {
    const body = await request<unknown>(`/api/designs/${id}/proposals`, { signal }, (response) =>
        response.json(),
    );
    if (
        typeof body !== 'object' ||
        body === null ||
        !('proposals' in body) ||
        !Array.isArray(body.proposals)
    )
        throw new CanvasError('INVALID_RESPONSE', '建议列表不完整。');
    return body.proposals.map((item: unknown) => {
        if (
            typeof item !== 'object' ||
            item === null ||
            !('proposal_id' in item) ||
            typeof item.proposal_id !== 'string' ||
            !validProposalId(item.proposal_id)
        )
            throw new CanvasError('INVALID_RESPONSE', '建议列表标识无效。');
        return item.proposal_id;
    });
}

export async function proposalPreview(proposal: EditProposal, signal?: AbortSignal) {
    return readSvg(
        `/api/designs/${proposal.document_id}/proposals/${proposal.proposal_id}/preview.svg`,
        signal,
    );
}

export async function applyProposal(id: string, proposalId: string): Promise<Design> {
    if (!validId(id) || !validProposalId(proposalId))
        throw new CanvasError('INVALID_ID', '修改建议 ID 无效。');
    return parseDesign(
        await request<unknown>(
            `/api/designs/${id}/proposals/${proposalId}/apply`,
            { method: 'POST' },
            (response) => response.json(),
        ),
    );
}
