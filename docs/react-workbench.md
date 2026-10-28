# React 商品海报工作台

前端使用 React 19、TypeScript、Vite 7、Tailwind CSS 4 与 Lucide 图标，依赖锁定在 `frontend/package-lock.json`。后端继续使用 FastAPI，同一个本机地址提供页面、静态文件和业务接口。

## 查看页面

现成构建文件随仓库保存，运行 Python 服务即可打开，不要求使用者安装 Node.js：

```sh
uv run --locked python -m app.run_local --port 8000
```

打开 http://127.0.0.1:8000 。已生成的图片、草稿和审核记录仍按原有规则保存。

## 修改和构建前端

开发需要 Node.js 22.12 或更高版本。

```sh
cd frontend
npm ci
npm run build
```

构建先执行 TypeScript 检查，再构建浏览器资源，最后用 React 服务端渲染生成 `app/templates/index.html`。提交源码时也需包含生成模板与 `app/static/workbench/`，不要手工修改生成文件。生成页面包含初始表单结构，浏览器加载 React 后同步完成 hydration，再启动已有业务控制器。

```sh
npm run format:check
npm run typecheck
```

需要开发预览时，先在另一个终端启动端口 8000 的 Python 服务，再执行 `npm run dev`，使用终端给出的本机地址。源文件变更后会完整刷新页面，让业务事件只初始化一次。Vite 将 `/api` 和两个草稿模块转发给本机 Python 服务。不同端口使用各自的浏览器创作草稿存储。

## 目录和迁移边界

| 文件 | 用途 |
| --- | --- |
| `frontend/src/App.tsx` | 侧栏、应用外框及四个工作区 |
| `frontend/src/components/*Workspace.tsx` | 创作、批量处理、审核与执行记录的 React 结构 |
| `frontend/src/components/CreationForm.tsx` | 商品、文案与版式填写 |
| `frontend/src/components/PosterEditor.tsx` | 编辑、导出与检查标签及对应面板 |
| `frontend/src/components/PreviewToolbar.tsx` | React 状态驱动的预览缩放与适应画布 |
| `frontend/src/workbench.css` | 共享结构样式 |
| `frontend/src/styles.css` | Tailwind 入口、视觉样式与响应式布局 |
| `frontend/src/workbench.js` | 原有业务控制器，继续使用 JavaScript |
| `frontend/scripts/prerender.tsx` | 将 React 结构和业务控制器生成可直接启动的页面 |

这是兼容现有业务的组件迁移。页面结构和新预览控件已使用 React；原有请求、草稿、版本、批量与审核仍由 DOM 控制器驱动，尚未全部改写为 React hooks 或 TypeScript。表单使用 `defaultValue` 保持非受控，避免预览缩放时覆盖尚未保存的输入。控制器必须在 React 初始化后只执行一次，不要在 React StrictMode 的 effect 中重复注册。

桌面使用侧栏和双栏创作区，左侧表单独立滚动，生成按钮保持可访问。窄屏改为横向导航和上下布局。缩放只改变浏览器显示，不修改导出图尺寸、已保存图片或验收结果。未能加载浏览器程序时保留恢复提示，初始表单保持 inert，避免误操作。

## 验证

```sh
uv run --locked python -m pytest -q
uv run --locked ruff check app tests scripts evals
```

真实浏览器回归使用可选的 Playwright 和系统 Chrome。为避免写入已有资料，先用临时目录指定 `POSTER_DATA_DIR`、`POSTER_EXECUTION_DB` 和 `POSTER_CHECKPOINT_DB` 启动服务，然后执行：

```sh
python tests/browser_react_workbench.py http://127.0.0.1:8000 /tmp/poster-react-screenshots
```

此脚本使用程序绘制的商品图和免费背景，检查 hydration、草稿刷新恢复、实际生成、预览缩放、导航、4:5 模板对比、四个手机工作区与启动失败提示。它不证明真实商品素材质量、模型质量或人工审核通过。
