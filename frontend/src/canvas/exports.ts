import { CanvasError, request } from './api';
import type { Design } from './api';

export interface ApprovalTarget {
    document_id: string;
    revision: number;
    spec_digest: string;
    asset_manifest_digest: string;
    approved: boolean;
}

export function parseApprovalTarget(
    value: unknown,
    design: Pick<Design, 'document_id' | 'revision'>,
): ApprovalTarget {
    if (typeof value !== 'object' || value === null)
        throw new CanvasError('INVALID_RESPONSE', '导出版本信息不完整。');
    const row = Object.fromEntries(Object.entries(value));
    if (row.document_id !== design.document_id || row.revision !== design.revision)
        throw new CanvasError('REVISION_CONFLICT', '设计版本已变化，请重新载入后核对。');
    if (
        typeof row.spec_digest !== 'string' ||
        !/^[a-f0-9]{64}$/.test(row.spec_digest) ||
        typeof row.asset_manifest_digest !== 'string' ||
        !/^[a-f0-9]{64}$/.test(row.asset_manifest_digest) ||
        typeof row.approved !== 'boolean'
    )
        throw new CanvasError('INVALID_RESPONSE', '导出版本摘要无效。');
    return {
        document_id: design.document_id,
        revision: design.revision,
        spec_digest: row.spec_digest,
        asset_manifest_digest: row.asset_manifest_digest,
        approved: row.approved,
    };
}

export async function readApprovalTarget(
    design: Pick<Design, 'document_id' | 'revision'>,
    signal: AbortSignal,
) {
    return parseApprovalTarget(
        await request(
            `/api/designs/${design.document_id}/approval-target`,
            { signal },
            (response) => response.json(),
        ),
        design,
    );
}

function authorization(token: string): HeadersInit {
    return token ? { Authorization: `Bearer ${token}` } : {};
}

export async function approveTarget(
    target: ApprovalTarget,
    id: string,
    token: string,
    signal: AbortSignal,
) {
    const result: unknown = await request(
        `/api/designs/${target.document_id}/approvals`,
        {
            method: 'POST',
            signal,
            headers: { ...authorization(token), 'Content-Type': 'application/json' },
            body: JSON.stringify({
                approval_id: id,
                expected_revision: target.revision,
                spec_digest: target.spec_digest,
                asset_manifest_digest: target.asset_manifest_digest,
            }),
        },
        (response) => response.json(),
    );
    if (
        typeof result !== 'object' ||
        result === null ||
        !('document_id' in result) ||
        result.document_id !== target.document_id ||
        !('revision' in result) ||
        result.revision !== target.revision ||
        !('spec_digest' in result) ||
        result.spec_digest !== target.spec_digest ||
        !('asset_manifest_digest' in result) ||
        result.asset_manifest_digest !== target.asset_manifest_digest
    )
        throw new CanvasError('INVALID_RESPONSE', '确认凭据与当前版本不一致，请重新核对。');
}

export async function downloadApproved(
    target: ApprovalTarget,
    format: 'svg' | 'png',
    token: string,
    signal: AbortSignal,
): Promise<Blob> {
    return request(
        `/api/designs/${target.document_id}/revisions/${target.revision}/approved.${format}`,
        { signal, headers: authorization(token) },
        async (response) => {
            const type = response.headers.get('content-type')?.split(';')[0].trim();
            if (type !== (format === 'svg' ? 'image/svg+xml' : 'image/png'))
                throw new CanvasError('INVALID_RESPONSE', '导出文件类型无效。');
            const blob = await response.blob();
            if (blob.size === 0 || blob.size > 24 * 1024 * 1024)
                throw new CanvasError('INVALID_RESPONSE', '导出文件大小无效。');
            return blob;
        },
    );
}
