# AI 画布 OpenCode 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. 本次用户明确指定 OpenCode 实现，Codex 负责协调与独立验收，不替换实现方。不允许自行分派更多执行者。

**Goal:** 完成面向 AI 应用 / Agent 求职展示的可编辑海报与对话修改闭环。

**Architecture:** 文档和统一设计命令是状态中心；画布、Agent、CLI/MCP 共用应用服务。旧海报链路保持兼容，新模块逐步交付。

**Tech Stack:** Python 3.12、Pydantic 2、FastAPI、React/TypeScript、SQLite；共享 SVG 渲染方向先验证，再固定导出运行时。

**Spec:** `docs/superpowers/specs/2026-10-29-ai-canvas-design.md`

## Global Constraints

- 用户已授权按照该设计安排 OpenCode 实现，不再重复询问是否执行。
- 不删除或覆盖已有工作，不更改旧 `/api/posters` 协议和数据。
- 不提交、不推送、不部署；每个任务完成后由协调方审核差异再继续。
- 不读取 `.env`、账号凭证、客户素材，不在日志或报告输出敏感数据。
- OpenCode 单任务独占指定文件；协调方在其运行期间不修改这些文件。
- 模型与真实浏览器结果分别验证，不能用模拟测试冒充真实模型验收。
- 不主动下载权重、不自动付费生图、不把缺少能力当成 PASS。
- 首版单用户、本地部署、单页、text/image/rect、固定字体；不扩展协作或通用设计平台。
- 接口修改先定义契约；复用 Pydantic/FastAPI 契约，不为本次任务增加 Postman 云工作区。

## Review Focus

- 恶意文案与素材引用：不能在 SVG 中执行脚本、加载外部资源或读取任意路径；A1 覆盖转义与非法引用。
- 业务价格与普通文字：SetText 不得绕过 SetPrice；A2 覆盖业务字段来源与校验。
- 用户和 Agent 同时编辑：过期候选不能覆盖最新文档；A2/C 覆盖原子冲突拒绝。
- 字体或 Chromium 缺失：明确失败，不输出声称保真的替代图片；A3 覆盖依赖失败。
- 付费外部请求中断：未知结果不得自动重试；C/D 覆盖取消、重启与 UNKNOWN。

## 执行状态

- [x] A1 文档模型、受约束 SVG 与无模型 CLI，OpenCode 实现，Codex 独立重跑 61 项测试和 ruff 通过；离线样例生成通过。字体度量和 PNG 保真尚未验证。
- [ ] A2 设计仓库、命令原子性、幂等、撤销重做与 API。
- [ ] A3 真实画布、固定字体与 PNG 导出验证。
- [ ] B 手动编辑与浏览器完整闭环。
- [ ] C 真实模型的受约束工具循环与局部修改。
- [ ] D Codex 账号执行引擎和外部 MCP/CLI。
- [ ] E 本地模型、断网验证、固定任务评测与求职材料。

### 协调验收记录（2026-10-29）

- A1：独立运行两份新增测试，61 passed；ruff check 通过；离线 demo 实际生成 document.json/design.svg/manifest.json/product.png。
- 当前回归基线：指定全部旧存储路径到 `/private/tmp/poster-baseline-0915` 后，695 passed in 157.24s，记录在本机 `/private/tmp/poster-opencode/baseline-0915.txt`。这是含 A1 的基线，不是后续任务的最终验收。
- 初次未隔离检查点路径时测试触发正在运行的工作台独占锁；未停止工作台或改动真实业务数据。改用独立路径后通过，不修改产品锁语义来迁就测试。
- A1 追加探测发现 U+FFFE 进入标题后生成无效 SVG；已在 A2b 任务中要求先加回归再修复，尚未宣称该问题已修复。
- A2a 已通过 OpenCode 独立会话派发，日志本机 `/private/tmp/poster-opencode/task-A2a.jsonl`。A2b 等待 A2a 验收后串行派发。
- A2a 最终独立验收：159 项设计测试通过、ruff check 与 format 通过；已检查仓库事务、资产完整哈希、命令重放和历史栈实现。A2a 完成，A2b 开始派发；尚未提供画布或 Agent。
- A2b 已写入本地 API、延迟初始化和存储生命周期及 Unicode 修复，独立运行 196 项设计测试、完整 830 项回归均通过，ruff 与格式检查通过，10 项真实 HTTP 检查通过。
- A2b 尚未验收完成：协调方独立发现同物理数据库的符号链接与硬链接别名可绕过独占保护。OpenCode 会话在交付前因 big-pickle 免费额度耗尽退出。修复交接见 `2026-10-29-ai-canvas-A2b-review.md`，等待用户选择后续实现方式；未切换其他服务、未提交或推送。

阶段 A–E 的完整退出条件以设计文档第 11 节为准。A1 通过不代表画布、Agent 或离线模型已经完成。

## Task A1：文档、SVG 和离线基线

**Files:** 新建 `app/designs/__init__.py`、`app/designs/models.py`、`app/designs/templates.py`、`app/designs/rendering.py`、`scripts/design_demo.py`、`tests/test_design_documents.py`、`tests/test_design_rendering.py`、`docs/ai-canvas-foundation.md`。不改已有文件、依赖或锁文件；需要额外文件时先报告理由。

**Interfaces:** `DesignDocument` 使用 schema_version=1、document_id、revision、单 page、nodes 和 asset_refs。图层采用 Pydantic discriminated union：TextNode/ImageNode/RectNode，有稳定 ID 和语义角色。`create_template(*, title: str, price: Decimal, product_asset_id: str) -> DesignDocument` 创建一份 1024×1024 模板。`render_svg(document: DesignDocument, *, assets: dict[str, bytes]) -> str` 生成只含白名单内容的 SVG。图片由资产 ID 解析、验证并内联；不得接受路径或网络地址。`python -m scripts.design_demo --output-dir <new-directory>` 用程序商品图输出文档 JSON、SVG 和 manifest，不调用模型。

- [ ] 编写模型测试：JSON 往返保存语义、重复 node_id、非有限坐标、异常尺寸、未知字段/类型、非法 asset_id、价格精确字符串；验证负例确实拒绝。
- [ ] 执行 `uv run --locked python -m pytest tests/test_design_documents.py -q`，记录行为缺失导致的失败。
- [ ] 实现有界文档与确定性模板。缺少原图资产必须失败，不绘制伪商品。不支持的富文本、任意 SVG、HTML、脚本不进入模型。
- [ ] 编写渲染测试：中文和 `& < >` 正确转义；标题、价格、商品分别对应独立图层；恶意 asset 内容、缺资产、外链、图像格式/大小异常被拒绝；同一文档渲染稳定。
- [ ] 执行渲染测试确认缺口，随后实现 SVG 与 CLI。文字换行使用固定字体方向的显式行坐标；本任务不宣称已完成 PNG 保真或浏览器一致性。
- [ ] 执行两个新测试文件、CLI 临时目录演示和 `uv run --locked ruff check app/designs scripts/design_demo.py tests/test_design_documents.py tests/test_design_rendering.py`。输出目录存在时拒绝覆盖。
- [ ] OpenCode 报告差异、命令与状态、已知限制，停止编辑。Codex 独立运行新测试和现有回归，核对生成 SVG/JSON 与范围；通过后再标记完成。

## Task A2：仓库与命令

待 A1 接口通过后派发。新增 repository/commands/service/routes 和契约测试；只在注册路由处最小修改 `app/main.py`。`apply_command(document_id, command_id, expected_revision, operations)` 在一个 SQLite 事务中提交快照与幂等记录。定义稳定错误 code、相同命令重放语义、Undo/Redo 和独占写锁。测试半批失败不写入、同 key 换输入、过期 revision、锁定图层、SetText 改价被拒绝、重启重读与历史不丢失。此任务不接模型。

A2 分两次串行派发：先按 `2026-10-29-ai-canvas-A2a.md` 实现仓库和命令，再安排应用接口和独占写锁集成。A2a 通过不代表完整 A2 已通过。

## Task A3/B：真实画布与导出

待文档/命令稳定后派发独立计划，读取 frontend 规范。新建隔离的 React 画布入口，不启动旧 DOM 控制器。完成选择、拖动、文本/价格/样式修改、锁定、撤销重做、保存重开；SVG 展示与服务端导出共用语义。验证中文固定字体与 Chromium 缺失反馈后再引入必要依赖。真实浏览器验证人工操作、刷新、冲突、键盘及导出，并与指定 revision 的产物比对。

## Task C：Agent 修改闭环

派发前细化引擎/工具接口。复用现有 Qwen 配置入口但不读取秘密值，先实现 ModelAgentEngine 的受约束工具循环。候选与 base_revision 绑定、预览后显式应用；最多 12 次工具、2 次修正、120 秒。工具协议测试覆盖缺字段、非法参数、锁定元素、冲突、超时、取消与持久恢复。至少一个真实模型完成端到端任务才通过；凭证不可用则明确阻塞，不能用假模型替代实测。

## Task D：Codex 与外部调用

实施时复核官方协议；登录、会话、权限与事件各自验证。Codex 仅访问海报工具和授权范围，不能以提示词代替访问限制。外部 MCP/CLI 复用同一命令服务和写入授权，拒绝第二个进程并行写库。需要用户登录时留给用户完成认证，不代读账号秘密。账号登录及一次真实编辑任务分别记录结果。

## Task E：离线与求职交付

先做纯脚本断网验证，再部署用户机器能运行且许可允许的本地模型；不得默认付费或下载大模型。固定不少于 20 个任务并至少 5 个人工视觉评价，报告结构正确与视觉质量、恢复、耗时及真实用量。最后更新安装、演示、架构、失败案例及准确简历表述；不编造性能数字。

## 交接约定

OpenCode 每次只实现一个已派发任务，发现设计冲突时报告，不自行减弱要求。日志与提示词保存在本机忽略目录，不进入前端或 Git。任务完成后由 Codex 核对实际文件和鲜活验证结果。后续任务派发前补齐准确文件、签名和验收测试，不把阶段说明冒充可直接执行的详细任务。
