import { CanvasError, parseDesign, readPreview, request } from './api';
import type { Design } from './api';

export interface RevisionSummary {
    revision: number;
    title: string;
    created_at: string;
    operations: string[];
}
export interface RevisionPage {
    current_revision: number;
    revisions: RevisionSummary[];
    next_cursor: number | null;
}
export interface HistoryView {
    design: Design;
    svg: string;
}

export function parseRevisionPage(value: unknown, documentId: string): RevisionPage {
    if (typeof value !== 'object' || value === null)
        throw new CanvasError('INVALID_RESPONSE', '历史数据不完整。');
    const row = Object.fromEntries(Object.entries(value));
    if (
        row.document_id !== documentId ||
        typeof row.current_revision !== 'number' ||
        !Number.isSafeInteger(row.current_revision) ||
        row.current_revision < 1 ||
        !Array.isArray(row.revisions) ||
        row.revisions.length > 50
    )
        throw new CanvasError('INVALID_RESPONSE', '历史数据的设计或版本无效。');
    const current = row.current_revision;
    const revisions = row.revisions.map((value: unknown): RevisionSummary => {
        if (typeof value !== 'object' || value === null)
            throw new CanvasError('INVALID_RESPONSE', '历史条目无效。');
        const item = Object.fromEntries(Object.entries(value));
        if (
            typeof item.revision !== 'number' ||
            !Number.isSafeInteger(item.revision) ||
            item.revision < 1 ||
            item.revision > current ||
            typeof item.title !== 'string' ||
            Array.from(item.title).length > 200 ||
            typeof item.created_at !== 'string' ||
            !Number.isFinite(Date.parse(item.created_at)) ||
            !Array.isArray(item.operations) ||
            item.operations.length < 1 ||
            item.operations.length > 20 ||
            !item.operations.every((op: unknown) => typeof op === 'string' && op.length <= 64)
        )
            throw new CanvasError('INVALID_RESPONSE', '历史条目的内容无效。');
        const operations: string[] = item.operations;
        return {
            revision: item.revision,
            title: item.title,
            created_at: item.created_at,
            operations,
        };
    });
    if (
        revisions.some((item, index) => {
            const previous = revisions[index - 1];
            return previous !== undefined && item.revision >= previous.revision;
        })
    )
        throw new CanvasError('INVALID_RESPONSE', '历史版本顺序无效。');
    const cursor = row.next_cursor;
    if (
        cursor !== null &&
        (typeof cursor !== 'number' ||
            !Number.isSafeInteger(cursor) ||
            cursor <= 1 ||
            cursor !== revisions.at(-1)?.revision)
    )
        throw new CanvasError('INVALID_RESPONSE', '历史分页游标无效。');
    return { current_revision: current, revisions, next_cursor: cursor };
}

export async function readRevisionPage(
    documentId: string,
    cursor: number | null,
    signal: AbortSignal,
) {
    const query = new URLSearchParams({ limit: '20' });
    if (cursor !== null) query.set('cursor', String(cursor));
    return parseRevisionPage(
        await request(`/api/designs/${documentId}/revisions?${query}`, { signal }, (response) =>
            response.json(),
        ),
        documentId,
    );
}

export async function readHistoricalView(
    documentId: string,
    revision: number,
    signal: AbortSignal,
): Promise<HistoryView> {
    const design = parseDesign(
        await request(`/api/designs/${documentId}/revisions/${revision}`, { signal }, (response) =>
            response.json(),
        ),
    );
    if (design.document_id !== documentId || design.revision !== revision)
        throw new CanvasError('INVALID_RESPONSE', '历史预览与所选版本不一致。');
    return { design, svg: await readPreview(design, signal) };
}
