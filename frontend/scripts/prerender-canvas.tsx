import { readFile, writeFile } from 'node:fs/promises';

const manifest: Record<string, { file: string; css?: string[] }> = JSON.parse(
    await readFile('../app/static/canvas/.vite/manifest.json', 'utf8'),
);
const entry = manifest['src/canvas-main.tsx'];
if (!entry) throw new Error('缺少画布构建入口');
const styles = (entry.css ?? [])
    .map((file) => `<link rel="stylesheet" href="/static/canvas/${file}">`)
    .join('\n');
await writeFile(
    '../app/templates/canvas.html',
    `<!doctype html>
<html lang="zh-CN"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns=%27http://www.w3.org/2000/svg%27 viewBox=%270 0 32 32%27%3E%3Crect width=%2732%27 height=%2732%27 rx=%278%27 fill=%27%234f46e5%27/%3E%3C/svg%3E">
<title>图层画布 · Poster Agent</title>
${styles}
</head><body>
<p id="canvas-boot" role="status">正在载入图层画布。如长时间未完成，请刷新页面并检查本地服务。</p>
<noscript>请启用 JavaScript 后使用图层画布。</noscript>
<div id="canvas-root"></div>
<script defer src="/static/canvas/${entry.file}"></script>
</body></html>\n`,
);
await writeFile(
    '../app/static/canvas/THIRD_PARTY_LICENSES.txt',
    await readFile('../app/static/workbench/THIRD_PARTY_LICENSES.txt', 'utf8'),
);
console.info('已生成独立图层画布页面');
