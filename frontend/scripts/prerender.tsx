import { readFile, writeFile } from 'node:fs/promises';
import { renderToString } from 'react-dom/server';
import { App } from '../src/App';

const manifest: Record<string, { file: string; css?: string[] }> = JSON.parse(
    await readFile('../app/static/workbench/.vite/manifest.json', 'utf8'),
);
const entry = manifest['src/main.tsx'];
if (!entry) throw new Error('缺少前端构建入口');
const controller = await readFile('src/workbench.js', 'utf8');
if (/<\/script/i.test(controller)) throw new Error('控制器不能包含脚本结束标签');
const markup = renderToString(<App />);
const styles = (entry.css ?? [])
    .map((file) => `<link rel="stylesheet" href="/static/workbench/${file}">`)
    .join('\n');
const page = `<!doctype html>
<html lang="zh-CN"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns=%27http://www.w3.org/2000/svg%27 viewBox=%270 0 32 32%27%3E%3Crect width=%2732%27 height=%2732%27 rx=%278%27 fill=%27%234f46e5%27/%3E%3Cpath d=%27M9 7h14v18H9zM12 11h8M12 15h5%27 stroke=%27white%27 fill=%27none%27 stroke-width=%272%27/%3E%3C/svg%3E">
<meta name="theme-color" content="#f4f6fa">
<title>Poster Agent · 商品海报工作台</title>
${styles}
<style>html:not([data-ready]) #root{pointer-events:none}#boot-status{padding:12px 24px;background:#eef2ff;color:#3730a3;font:14px system-ui}html[data-ready] #boot-status{display:none}</style>
</head><body>
<div id="boot-status" role="status">正在载入工作台。如长时间未完成，请刷新页面并检查本地服务。</div>
<noscript><p>工作台需要启用 JavaScript。请在浏览器设置中启用后刷新。</p></noscript>
<div id="root" inert>${markup}</div>
<script src="/static/create-draft.js"></script>
<script src="/static/review-drafts.js"></script>
<script src="/static/workbench/${entry.file}"></script>
<script>
${controller}
if (document.documentElement.dataset.reactMounted === 'true') {
 document.documentElement.dataset.ready='true';
 document.getElementById('root').inert=false;
}
</script>
</body></html>\n`;
const packages = ['react', 'react-dom', 'scheduler', 'lucide-react', 'tailwindcss'];
const notices = await Promise.all(
    packages.map(async (name) => {
        const license = await readFile(`node_modules/${name}/LICENSE`, 'utf8');
        return `===== ${name} =====\n${license}`;
    }),
);
await writeFile('../app/static/workbench/THIRD_PARTY_LICENSES.txt', notices.join('\n\n'));
await writeFile('../app/templates/index.html', page);
console.info('已预渲染 React 工作台至 FastAPI 模板');
