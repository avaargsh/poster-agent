import { CanvasError, request, validId } from './api';

export interface DesignSummary {
    document_id: string;
    title: string;
    revision: number;
}
export interface DesignPage {
    designs: DesignSummary[];
    next_cursor: string | null;
}

export function parseDesignPage(value: unknown): DesignPage {
    if (
        typeof value !== 'object' ||
        value === null ||
        !('designs' in value) ||
        !('next_cursor' in value)
    )
        throw new CanvasError('INVALID_RESPONSE', '设计列表不完整。');
    if (!Array.isArray(value.designs) || value.designs.length > 50)
        throw new CanvasError('INVALID_RESPONSE', '设计列表大小无效。');
    const designs = value.designs.map((item: unknown) => {
        if (
            typeof item !== 'object' ||
            item === null ||
            !('document_id' in item) ||
            typeof item.document_id !== 'string' ||
            !validId(item.document_id) ||
            !('title' in item) ||
            typeof item.title !== 'string' ||
            Array.from(item.title).length > 200 ||
            !('revision' in item) ||
            typeof item.revision !== 'number' ||
            !Number.isSafeInteger(item.revision) ||
            item.revision < 1
        )
            throw new CanvasError('INVALID_RESPONSE', '设计列表条目无效。');
        return { document_id: item.document_id, title: item.title, revision: item.revision };
    });
    const cursor = value.next_cursor;
    if (
        cursor !== null &&
        (typeof cursor !== 'string' || !validId(cursor) || cursor !== designs.at(-1)?.document_id)
    )
        throw new CanvasError('INVALID_RESPONSE', '设计列表游标无效。');
    if (new Set(designs.map((item) => item.document_id)).size !== designs.length)
        throw new CanvasError('INVALID_RESPONSE', '设计列表包含重复条目。');
    return { designs, next_cursor: cursor };
}

export async function readDesignPage(
    cursor: string | null,
    signal: AbortSignal,
): Promise<DesignPage> {
    const query = new URLSearchParams({ limit: '10' });
    if (cursor) query.set('cursor', cursor);
    return parseDesignPage(
        await request(`/api/designs?${query}`, { signal }, (response) => response.json()),
    );
}
