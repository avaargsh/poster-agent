import { CanvasError, request } from './api';
import { validProposalId } from './proposals';

export interface AgentRequest {
    request_id: string;
    expected_revision: number;
    prompt: string;
}
export interface AgentRun {
    request_id: string;
    document_id: string;
    base_revision: number;
    prompt: string;
    status: 'RUNNING' | 'SUCCEEDED' | 'FAILED' | 'CANCELLED' | 'UNKNOWN';
    error_code: string | null;
    proposal_id: string | null;
}
export interface AgentCapabilities {
    enabled: boolean;
    available: boolean;
}

export function parseAgentRun(value: unknown, documentId: string): AgentRun {
    if (typeof value !== 'object' || value === null)
        throw new CanvasError('INVALID_RESPONSE', 'AI 任务数据不完整。');
    const row = Object.fromEntries(Object.entries(value));
    if (
        row.document_id !== documentId ||
        typeof row.request_id !== 'string' ||
        !/^[A-Za-z0-9_-]{8,128}$/.test(row.request_id) ||
        typeof row.base_revision !== 'number' ||
        !Number.isSafeInteger(row.base_revision) ||
        row.base_revision < 1 ||
        typeof row.prompt !== 'string' ||
        Array.from(row.prompt).length > 2000
    )
        throw new CanvasError('INVALID_RESPONSE', 'AI 任务标识或需求无效。');
    if (
        row.status !== 'RUNNING' &&
        row.status !== 'SUCCEEDED' &&
        row.status !== 'FAILED' &&
        row.status !== 'CANCELLED' &&
        row.status !== 'UNKNOWN'
    )
        throw new CanvasError('INVALID_RESPONSE', 'AI 任务状态无效。');
    if (
        row.error_code !== null &&
        (typeof row.error_code !== 'string' || !/^[A-Z_]{1,64}$/.test(row.error_code))
    )
        throw new CanvasError('INVALID_RESPONSE', 'AI 任务错误状态无效。');
    if (
        row.proposal_id !== null &&
        (typeof row.proposal_id !== 'string' || !validProposalId(row.proposal_id))
    )
        throw new CanvasError('INVALID_RESPONSE', 'AI 任务建议标识无效。');
    if (row.status === 'SUCCEEDED' && row.proposal_id === null)
        throw new CanvasError('INVALID_RESPONSE', 'AI 任务缺少建议结果。');
    return {
        request_id: row.request_id,
        document_id: documentId,
        base_revision: row.base_revision,
        prompt: row.prompt,
        status: row.status,
        error_code: row.error_code,
        proposal_id: row.proposal_id,
    };
}

export async function agentCapabilities(signal: AbortSignal): Promise<AgentCapabilities> {
    const value: unknown = await request('/api/design-agent/capabilities', { signal }, (response) =>
        response.json(),
    );
    if (
        typeof value !== 'object' ||
        value === null ||
        !('enabled' in value) ||
        typeof value.enabled !== 'boolean' ||
        !('available' in value) ||
        typeof value.available !== 'boolean'
    )
        throw new CanvasError('INVALID_RESPONSE', 'AI 服务状态无效。');
    return { enabled: value.enabled, available: value.available };
}

export async function listAgentRuns(documentId: string, signal: AbortSignal): Promise<AgentRun[]> {
    const value: unknown = await request(
        `/api/designs/${documentId}/agent-runs`,
        { signal },
        (response) => response.json(),
    );
    if (
        typeof value !== 'object' ||
        value === null ||
        !('runs' in value) ||
        !Array.isArray(value.runs) ||
        value.runs.length > 20
    )
        throw new CanvasError('INVALID_RESPONSE', 'AI 任务列表无效。');
    return value.runs.map((row: unknown) => parseAgentRun(row, documentId));
}

export async function readAgentRun(documentId: string, requestId: string, signal: AbortSignal) {
    const result = parseAgentRun(
        await request(
            `/api/designs/${documentId}/agent-runs/${requestId}`,
            { signal },
            (response) => response.json(),
        ),
        documentId,
    );
    if (result.request_id !== requestId)
        throw new CanvasError('INVALID_RESPONSE', 'AI 任务响应与请求不一致。');
    return result;
}

export async function submitAgentRun(documentId: string, body: AgentRequest, signal: AbortSignal) {
    const result = parseAgentRun(
        await request(
            `/api/designs/${documentId}/agent-runs`,
            {
                signal,
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body),
            },
            (response) => response.json(),
        ),
        documentId,
    );
    if (result.request_id !== body.request_id || result.base_revision !== body.expected_revision)
        throw new CanvasError('INVALID_RESPONSE', 'AI 任务响应与请求不一致。');
    return result;
}

export async function cancelAgentRun(documentId: string, requestId: string, signal: AbortSignal) {
    const result = parseAgentRun(
        await request(
            `/api/designs/${documentId}/agent-runs/${requestId}/cancel`,
            { signal, method: 'POST' },
            (response) => response.json(),
        ),
        documentId,
    );
    if (result.request_id !== requestId)
        throw new CanvasError('INVALID_RESPONSE', 'AI 停止响应与请求不一致。');
    return result;
}
