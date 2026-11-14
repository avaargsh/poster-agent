import type { DesignNode } from './api';

export function inspectorValues(node: DesignNode, price: string) {
    return {
        x: String(node.x),
        y: String(node.y),
        width: String(node.width),
        height: String(node.height),
        rotation: String(node.rotation),
        opacity: String(node.opacity),
        price,
        copy: node.node_type === 'text' ? node.lines.map((line) => line.text).join('') : '',
        color:
            node.node_type === 'text'
                ? node.color
                : node.node_type === 'rect'
                  ? node.fill
                  : '#000000',
        fontSize: node.node_type === 'text' ? String(node.font_size) : '48',
    };
}

type Values = ReturnType<typeof inspectorValues>;
export interface InspectorDraft {
    baseline: Values;
    values: Values;
}
const numericKeys = [
    'x',
    'y',
    'width',
    'height',
    'rotation',
    'opacity',
    'fontSize',
] satisfies (keyof Values)[];

function sameNumber(left: string, right: string) {
    return (
        Boolean(left.trim() && right.trim()) &&
        Number.isFinite(Number(left)) &&
        Number(left) === Number(right)
    );
}

function decimal(value: string): string | null {
    if (!/^\d{1,6}(\.\d{1,2})?$/.test(value)) return null;
    const [whole, cents = ''] = value.split('.');
    return `${whole.replace(/^0+(?=\d)/, '')}.${cents.padEnd(2, '0')}`;
}

export function samePrice(left: string, right: string) {
    const normalized = decimal(left);
    return normalized !== null && normalized === decimal(right);
}

export function mergeInspectorDraft(baseline: Values, current: Values, incoming: Values): Values {
    const merged = { ...current };
    for (const key of numericKeys)
        if (sameNumber(current[key], baseline[key])) merged[key] = incoming[key];
    for (const key of ['copy', 'color'] satisfies (keyof Values)[])
        if (current[key] === baseline[key]) merged[key] = incoming[key];
    if (samePrice(current.price, baseline.price)) merged.price = incoming.price;
    return merged;
}

export function isInspectorDirty(baseline: Values, current: Values) {
    return (
        numericKeys.some((key) => !sameNumber(current[key], baseline[key])) ||
        current.copy !== baseline.copy ||
        current.color !== baseline.color ||
        !samePrice(current.price, baseline.price)
    );
}
