# 可编辑图层画布实施计划

**Goal:** 将已有 DesignDocument 命令接入可操作的 React 画布，完成创建、局部编辑、保存、撤销重做和刷新恢复。

**Architecture:** 独立 `/canvas` 页面复用现有 `/api/designs` 及固定版本 SVG。页面选择、缩放和拖动预览由 React 管理，已提交设计由服务端版本仓库管理。

**Tech Stack:** 现有 React 19、TypeScript、Vite、Lucide、FastAPI；使用现有依赖。

**Spec:** `docs/superpowers/specs/2026-10-29-ai-canvas-design.md` 的手动编辑闭环。本次实施基线为 `9938492dc0fa7f6e2e9fb237768c9a0c59270a7e`。

## 执行与范围

用户已确认画布方向、指定 OpenCode 实现并授权 opencode/big-pickle 处理必要代码和设计。本轮继续该方式，由 Codex 独立验收。OpenCode 完成下述任务后停止，由协调方处理提交、PR 与合并。

执行记录更新于2026-11-14。实际启动 OpenCode 后，服务立即返回 provider.free-limit / HTTP 429，本轮由当前 Codex 会话接续实现。该调整已向用户说明。

允许新增 `frontend/src/canvas/` 下的类型/API、状态、画布、属性面板与 CSS，入口 `frontend/src/canvas-main.tsx`，`frontend/vite.canvas.config.ts`，`frontend/scripts/prerender-canvas.tsx`，`app/templates/canvas.html`，构建产物 `app/static/canvas/`，`tests/test_canvas_page.py`，`tests/browser_canvas_workspace.py`，`docs/canvas-workspace.md`。

允许最小修改 `frontend/package.json` 的 build/check/format 脚本、`frontend/tsconfig.json` 的 include、`frontend/src/App.tsx` 的画布导航链接、`app/main.py` 的 `/canvas` HTML 路由、`.github/workflows/ci.yml` 的浏览器任务步骤、README 的画布入口说明。

不修改现有 Design 领域模型、命令、仓库、审核与导出接口，不改旧 JS 控制器，不安装依赖，不读取 `.env`/凭证/客户素材，不调用项目模型、生图或 OCR，不提交推送，不派发其他代理。

## 接口与视图

1. `/canvas` 返回独立构建页面，在旧工作台提供“图层画布”入口。新页面的脚本独立启动 React，避免初始化旧 DOM 控制器。构建保留旧工作台的预渲染流程。
2. 左侧创建/打开设计与图层列表，中间真实海报画布，右侧选中图层属性。顶栏显示保存状态、当前版本、撤销/重做与返回工作台。沿用项目的浅色编辑器、中文系统字体和靛蓝操作色。
3. 创建使用 `POST /api/designs` 的 multipart `product,title,price,cta?`。标题上限35字符，CTA上限12字符，商品上传8 MiB。可选字段空白时省略。创建成功保存 document_id 到 URL 的 `document` 查询参数，刷新通过 `GET /api/designs/{id}` 读取最新版本。
4. 用户也能显式输入设计 ID 打开历史项目。localStorage 最多存最近10个项目的 ID/标题，读取和写入失败提供实际反馈，URL仍可恢复设计。图片内容与账号数据不写本地存储。
5. 已知接口返回 `{document,preview_url}`。前端从 unknown 校验页面实际使用的文档字段，使用 text/image/rect discriminated union，拒绝非法数值、ID和未来 schema_version。不使用 any 或未校验的类型断言。可以定义有限的只读前端投影，避免复制服务端业务规则。
6. 固定版本 SVG 通过同源固定路径读取，预览与 document.revision 配对。服务端 SVG 是渲染依据。对非 SVG/错误响应明确失败。若使用内联 SVG，验证解析根节点与允许的元素/引用，不将 HTTP 错误内容当 HTML 插入。

## 编辑语义

- 用户可在图层列表或画布选择元素，列表提供有意义的中文名称。可见选框与属性面板对应同一 node_id。图层顺序与后端 nodes 的绘制顺序一致。
- 鼠标/触摸拖动支持缩放坐标转换，拖动时可见目标移动。pointerup 仅提交一次 transform_node，pointercancel/Escape 取消预览且不提交。拖动旋转节点保留原旋转。
- 属性面板支持 x/y、宽/高、旋转、透明度、文字颜色/字号，以及矩形颜色。输入清空或不合法时保留编辑值并提示，不能默默保存为0或NaN。
- 标题/CTA 文本通过 set_text 保存。价格通过 set_price 保存 Decimal 字符串，source_text 记录用户本次明确输入。界面不以 set_text 修改价格，也不提供原价的无支持编辑入口。
- “保存修改”将一次属性编辑作为一条 CommandBatch 提交，单次失败不得显示部分保存。成功后采用返回的权威快照并更新固定版本预览。
- “锁定/解锁”使用 set_node_lock。已锁节点仍可选择和查看，几何、文案、样式和拖动禁用。解锁操作显式执行。
- “上移/下移一层”使用 reorder_node，处理数组从底到顶的语义。撤销/重做使用单独的 undo/redo 命令，并显示服务器返回的新递增版本。
- 每条命令生成一次 command_id，携带 expected_revision 和 actor=user。一旦提交超时，不自动生成新 key 重发修改。保留同 key 重试操作或提供读取最新版本来核对结果。
- 单页面最多一条写操作在途。提交期间禁止切换设计或再发修改。读取使用取消或请求代号，使旧设计/旧预览迟到响应不能覆盖当前项目。
- 版本冲突显示“设计已在其他页面更新，当前修改尚未保存”，保留当前输入，提供显式“载入最新版本”。不得静默覆写、自动重放到新版本，或显示保存成功。
- 编辑失败显示清楚的恢复方式，保持当前设计和输入。撤销空栈、锁定、资源不可用等错误分别映射用户可理解的反馈。
- 支持画布缩放与适应窗口，缩放仅影响显示。可通过图层列表与数值属性完成键盘编辑，画布焦点内方向键支持1像素/Shift 10像素移动，输入框方向键保留文本/数值输入行为。

本轮交付手动编辑入口。审批、PNG 导出、真实 OCR 和 Agent 会话仍通过各自现有契约演进，画布不自动批准设计，也不展示模拟 AI 执行状态。若提供“打开 SVG 预览”，明确它对应当前预览版本。

## 构建与测试

- [ ] 新建独立 Vite 配置，构建到 `app/static/canvas` 并生成 `app/templates/canvas.html`。构建时从 manifest 取哈希资源名，包含第三方许可。
- [ ] `npm run build` 同时生成旧工作台与画布，`npm run check` 核对两套已提交产物。所有新增 TS/Vite 配置纳入类型检查。
- [ ] 实现 `/canvas` 和导航，并通过测试确认静态资源可读取、页面使用独立脚本、经过本地请求边界；测试使用全部5个隔离存储配置。
- [ ] 实现前端状态/API边界和完整编辑组件。使用已有测试模式验证关键输入/命令构造；避免仅断言源码字符串的伪功能测试。
- [ ] 新增 opt-in Python Playwright 流程，参数为真实测试服务 origin 和截图目录，使用生成商品像素。覆盖创建、选中拖动、文字/改价、锁定、图层顺序、撤销重做、刷新恢复、外部修改冲突、离线错误恢复、移动端与控制台异常。
- [ ] 在现有 ci:browser 任务追加该浏览器流程，遵循已有单独服务/临时数据方式；如采用 origin 参数，任务内显式启动隔离服务并可靠清理。
- [ ] OpenCode 运行当前锁定临时环境 `/private/tmp/poster-governance-20261112-venv/bin/python` 和同目录 ruff 进行相关测试；运行 npm 类型检查、构建。新增文件按现有格式整理。
- [ ] 协调方独立检查 diff，使用真实 Chrome 和临时数据验证端到端及截图，补查迟到响应、并发冲突和失败恢复。
- [ ] 文档提供真实启动入口和当前可用能力。经过验证的代码再提交并走 PR 双版本 CI 与浏览器检查。

## 实现约定

先阅读相关代码与本计划，再直接实现。服务端已有能力足够本轮使用。遇到契约缺口先报告具体问题，不在领域层扩展新状态或审批捷径。完成本计划后汇报实际测试及未验证项，停止编辑，交给协调方验收。
