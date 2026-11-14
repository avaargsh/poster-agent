export interface NodeBase {
    node_id: string;
    role: string;
    x: number;
    y: number;
    width: number;
    height: number;
    rotation: number;
    opacity: number;
    visible: boolean;
    locked: boolean;
}

export type DesignNode =
    | (NodeBase & {
          node_type: 'text';
          color: string;
          font_size: number;
          lines: { text: string; x: number; y: number }[];
      })
    | (NodeBase & { node_type: 'image' })
    | (NodeBase & { node_type: 'rect'; fill: string });

export interface Design {
    document_id: string;
    revision: number;
    page: { width: number; height: number };
    nodes: DesignNode[];
    facts: { price: string };
}

export type Operation =
    | { op: 'set_text'; node_id: string; text: string }
    | { op: 'set_price'; price: string; source_text: string }
    | { op: 'set_style'; node_id: string; color?: string; font_size?: number; opacity?: number }
    | {
          op: 'transform_node';
          node_id: string;
          x?: number;
          y?: number;
          width?: number;
          height?: number;
          rotation?: number;
      }
    | { op: 'set_node_lock'; node_id: string; locked: boolean }
    | { op: 'reorder_node'; node_id: string; index: number }
    | { op: 'undo' | 'redo' };

export interface EditRequest {
    command_id: string;
    expected_revision: number;
    actor: 'user';
    operations: Operation[];
}

export class CanvasError extends Error {
    constructor(
        public code: string,
        message: string,
    ) {
        super(message);
    }
}

export const validId = (value: string) => /^[a-z0-9][a-z0-9_-]{0,63}$/.test(value);
export const previewUrl = (design: Design) =>
    `/api/designs/${design.document_id}/revisions/${design.revision}/preview.svg`;

function object(value: unknown): Record<string, unknown> {
    if (typeof value !== 'object' || value === null || Array.isArray(value)) {
        throw new CanvasError('INVALID_RESPONSE', '设计数据格式不正确，请重新载入。');
    }
    return Object.fromEntries(Object.entries(value));
}

function text(value: unknown): string {
    if (typeof value !== 'string') throw new CanvasError('INVALID_RESPONSE', '设计字段不完整。');
    return value;
}

function number(value: unknown, min: number, max: number): number {
    if (typeof value !== 'number' || !Number.isFinite(value) || value < min || value > max) {
        throw new CanvasError('INVALID_RESPONSE', '设计数值超出支持范围。');
    }
    return value;
}

function boolean(value: unknown): boolean {
    if (typeof value !== 'boolean') throw new CanvasError('INVALID_RESPONSE', '图层状态无效。');
    return value;
}

function color(value: unknown): string {
    const result = text(value);
    if (!/^#[a-f\d]{6}$/i.test(result)) throw new CanvasError('INVALID_RESPONSE', '图层颜色无效。');
    return result;
}

function parseNode(value: unknown): DesignNode {
    const row = object(value);
    const id = text(row.node_id);
    if (!/^[a-z][a-z0-9_-]{0,63}$/.test(id))
        throw new CanvasError('INVALID_RESPONSE', '图层标识无效。');
    const base: NodeBase = {
        node_id: id,
        role: text(row.role),
        x: number(row.x, -8192, 8192),
        y: number(row.y, -8192, 8192),
        width: number(row.width, Number.MIN_VALUE, 4096),
        height: number(row.height, Number.MIN_VALUE, 4096),
        rotation: number(row.rotation, -360, 360),
        opacity: number(row.opacity, 0, 1),
        visible: boolean(row.visible),
        locked: boolean(row.locked),
    };
    if (row.node_type === 'image') return { ...base, node_type: 'image' };
    if (row.node_type === 'rect') return { ...base, node_type: 'rect', fill: color(row.fill) };
    if (
        row.node_type === 'text' &&
        Array.isArray(row.lines) &&
        row.lines.length > 0 &&
        row.lines.length <= 12
    ) {
        return {
            ...base,
            node_type: 'text',
            color: color(row.color),
            font_size: number(row.font_size, 8, 256),
            lines: row.lines.map((value) => {
                const line = object(value);
                return {
                    text: text(line.text),
                    x: number(line.x, -8192, 8192),
                    y: number(line.y, -8192, 8192),
                };
            }),
        };
    }
    throw new CanvasError('INVALID_RESPONSE', '当前版本不支持这类图层。');
}

export function parseDesign(value: unknown): Design {
    const envelope = object(value);
    const row = object(envelope.document);
    const page = object(row.page);
    const facts = object(row.facts);
    const id = text(row.document_id);
    if (row.schema_version !== 1 || !validId(id))
        throw new CanvasError('INVALID_RESPONSE', '设计版本或标识不受支持。');
    const revision = number(row.revision, 1, Number.MAX_SAFE_INTEGER);
    if (
        !Number.isInteger(revision) ||
        !Array.isArray(row.nodes) ||
        row.nodes.length > 200 ||
        !row.nodes.length
    ) {
        throw new CanvasError('INVALID_RESPONSE', '设计版本或图层清单无效。');
    }
    const nodes = row.nodes.map(parseNode);
    if (new Set(nodes.map((node) => node.node_id)).size !== nodes.length)
        throw new CanvasError('INVALID_RESPONSE', '图层标识重复。');
    const price = text(facts.price);
    if (!/^\d{1,6}\.\d{2}$/.test(price))
        throw new CanvasError('INVALID_RESPONSE', '设计价格格式无效。');
    return {
        document_id: id,
        revision,
        page: { width: number(page.width, 64, 4096), height: number(page.height, 64, 4096) },
        nodes,
        facts: { price },
    };
}

const messages: Record<string, string> = {
    REVISION_CONFLICT: '设计已在其他页面更新，当前修改尚未保存。请载入最新版本后重新编辑。',
    LOCKED_NODE: '该图层已锁定，请先解锁。',
    NOTHING_TO_UNDO: '当前没有可撤销的修改。',
    NOTHING_TO_REDO: '当前没有可重做的修改。',
    NOT_FOUND: '找不到这份设计，请检查设计 ID 或本机存储位置。',
    ASSET_INVALID: '商品素材无效或已损坏，请检查图片文件。',
    DESIGN_STORAGE_BUSY: '设计存储正由另一个服务使用，请关闭重复启动的服务后重试。',
};

async function checked(response: Response): Promise<Response> {
    if (response.ok) return response;
    let code = 'REQUEST_FAILED';
    try {
        const body = object(await response.json());
        if (body.error) code = text(object(body.error).code);
    } catch {
        /* Non-JSON error pages use the status-based feedback below. */
    }
    throw new CanvasError(
        code,
        messages[code] ?? `请求未完成（${response.status}），请检查输入或重新载入。`,
    );
}

async function request<T>(
    path: string,
    init: RequestInit,
    read: (response: Response) => Promise<T>,
): Promise<T> {
    const controller = new AbortController();
    const abort = () => controller.abort();
    init.signal?.addEventListener('abort', abort, { once: true });
    if (init.signal?.aborted) controller.abort();
    const timer = window.setTimeout(abort, 20000);
    try {
        const response = await checked(await fetch(path, { ...init, signal: controller.signal }));
        return await read(response);
    } catch (error) {
        if (error instanceof CanvasError) throw error;
        throw new CanvasError('CONNECTION_FAILED', '连接中断或超时，请检查本机服务后重试。');
    } finally {
        window.clearTimeout(timer);
        init.signal?.removeEventListener('abort', abort);
    }
}

export async function readDesign(id: string, signal?: AbortSignal): Promise<Design> {
    if (!validId(id)) throw new CanvasError('INVALID_ID', '请输入有效的设计 ID。');
    return parseDesign(
        await request<unknown>(`/api/designs/${id}`, { signal }, (response) => response.json()),
    );
}

export async function createDesign(form: FormData): Promise<Design> {
    return parseDesign(
        await request<unknown>('/api/designs', { method: 'POST', body: form }, (response) =>
            response.json(),
        ),
    );
}

export async function editDesign(id: string, edit: EditRequest): Promise<Design> {
    return parseDesign(
        await request<unknown>(
            `/api/designs/${id}/commands`,
            {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(edit),
            },
            (response) => response.json(),
        ),
    );
}

export async function readPreview(design: Design, signal?: AbortSignal): Promise<string> {
    const { type, source } = await request(previewUrl(design), { signal }, async (response) => ({
        type: response.headers.get('content-type'),
        source: await response.text(),
    }));
    if (!type?.startsWith('image/svg+xml'))
        throw new CanvasError('INVALID_PREVIEW', '预览格式不正确。');
    if (/<!doctype|<!entity/i.test(source))
        throw new CanvasError('INVALID_PREVIEW', '预览包含不受支持的内容。');
    const svg = new DOMParser().parseFromString(source, 'image/svg+xml');
    const tags = new Set(['svg', 'g', 'rect', 'image', 'text', 'tspan']);
    if (svg.querySelector('parsererror') || svg.documentElement.localName !== 'svg')
        throw new CanvasError('INVALID_PREVIEW', '预览解析失败。');
    for (const element of [svg.documentElement, ...svg.documentElement.querySelectorAll('*')]) {
        if (element.namespaceURI !== 'http://www.w3.org/2000/svg' || !tags.has(element.localName))
            throw new CanvasError('INVALID_PREVIEW', '预览图层格式不受支持。');
        for (const attribute of element.attributes) {
            if (
                /^on/i.test(attribute.name) ||
                attribute.name === 'style' ||
                /url\s*\(/i.test(attribute.value)
            )
                throw new CanvasError('INVALID_PREVIEW', '预览包含不安全的属性。');
            if (
                attribute.localName === 'href' &&
                !attribute.value.startsWith('data:image/png;base64,')
            )
                throw new CanvasError('INVALID_PREVIEW', '预览不能加载外部图片。');
        }
    }
    return svg.documentElement.outerHTML;
}

export function layerName(node: DesignNode): string {
    const labels: Record<string, string> = {
        title: '主标题',
        subtitle: '副标题',
        price: '活动价',
        original_price: '原价',
        product: '商品',
        logo: 'Logo',
        background: '背景',
        cta: '按钮文案',
        decoration: '装饰',
    };
    return Object.hasOwn(labels, node.role) ? labels[node.role] : node.node_id;
}
