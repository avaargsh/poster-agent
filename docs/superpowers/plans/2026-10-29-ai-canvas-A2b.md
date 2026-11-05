# AI 画布 A2b：本地 API 与存储生命周期

状态：A2a 已完成并停止编辑；159 项相关测试待本次最终独立核对后派发。

## 授权和约束

用户已指定 OpenCode 实现，并明确允许 opencode/big-pickle 外发必要设计与代码。延续总计划和设计约束，不提交推送、不读凭证/客户数据、不安装依赖，不调用项目模型或生图服务。

保持旧 API、现有 DOM 控制器及数据兼容；此任务不做前端、Agent、PNG 或账号登录。

## 允许文件

新建 `app/designs/service.py`、`app/designs/routes.py`、`tests/test_design_api.py`、`tests/test_design_lifecycle.py`。

最小修改 `app/main.py` 的 router 注册和 lifespan 清理、`app/designs/models.py` 的纯文本字符校验、`app/designs/templates.py` 的输入校验、`app/designs/commands.py` 的共享字符校验、`tests/test_design_documents.py` 和 `tests/test_design_commands.py` 的 Unicode 回归，以及 `docs/ai-canvas-foundation.md`、`.env.example` 的新配置说明。不改 repository、rendering 和 A1 demo。若需要根据 A2a 实际代码调整其他文件，先由协调方更新此范围。

## 服务和所有权

- 新配置 `POSTER_DESIGN_DB` 默认 `data/designs.sqlite3`；`POSTER_DESIGN_ASSET_DIR` 默认 `data/design-assets`。启动时只构造生命周期管理器，第一次设计请求才申请锁和创建设计库。
- 新设计服务在应用 lifespan 内持有数据库与素材目录的 POSIX 独占文件锁；锁文件不删除。相同 DB 或相同素材目录都不能被另一服务同时持有。
- 旧 API 不初始化设计仓库，不因默认设计路径被其他工作台持有而失败。新路由测试必须显式临时配置全部旧路径和设计路径。
- 新 helper 负责初始化与关闭设计管理器。main 只做生命周期接线，不复制业务逻辑。应用重开后不能复用上次 lifespan 的 repository 或锁。
- 并发首次请求仅初始化一次；初始化失败释放已取得的锁，不留下半初始化实例；关闭可重复调用。
- 原有 `worker_lock.single_worker()` 继续保护旧路径。新服务锁是独立作用域，不修改旧锁的路径集合。
- 新请求只从 `request.app.state` 取本应用管理器，不依赖跨 TestClient 的全局 repository。缺少 lifespan 时返回明确 503，不偷偷打开数据目录。
- API 服务保护路径在本机进程配置，不能通过请求改变。

## HTTP 契约

1. `POST /api/designs`：multipart 的 product、title、price。最多读取 MAX_ASSET_BYTES+1，超限 413；格式或字段非法 422；按 A2a create 保存且返回 201。响应是 `{document, preview_url}`；preview_url 固定新 revision。
2. `GET /api/designs/{document_id}`：200 `{document, preview_url}`，返回当前最新版本。
3. `GET /api/designs/{document_id}/revisions/{revision}`：精确读取历史，404 不回退；revision 必须正整数。
4. `POST /api/designs/{document_id}/commands`：CommandBatch JSON，200 `{document, command_id, replayed, preview_url}`。HTTP 手动编辑入口只接受 actor=user；未来内部引擎直接使用应用服务，外部 Agent 的授权另行实现，不能通过此字段伪造审批。
5. `GET /api/designs/{document_id}/revisions/{revision}/preview.svg`：按精确版本 read_assets → render_svg，`image/svg+xml`，UTF-8。响应设置 CSP 禁脚本/外联且仅允许内联 data 图片，X-Content-Type-Options nosniff，Cache-Control no-store。缺失或损坏素材明确错误，不输出半份 SVG。

错误响应 `{error: {code, message}}`。领域 NOT_FOUND→404；REVISION_CONFLICT/LOCKED_NODE/IDEMPOTENCY_CONFLICT/NOTHING_TO_UNDO/NOTHING_TO_REDO→409；字段/命令非法→422；素材完整性损坏→409 ASSET_INVALID；独占锁被占→503 DESIGN_STORAGE_BUSY；不可预期存储错误→503 DESIGN_STORAGE_UNAVAILABLE 且不泄漏本机路径/SQL。上传非法图像为 422 ASSET_INVALID，勿与持久素材损坏混淆。

Pydantic 请求校验仍可使用框架标准 422，但不得回显大块素材或敏感路径；领域错误必须稳定。文档 ID 非法返回 404。HTTP 层不得把上传文件名当路径。

所有路由经过已有 localhost/Host/Origin 边界。不放松既有网络限制。上传读完后关闭文件，阻塞存储/渲染用同步路由或线程池，不能长时间阻塞事件循环。

## 已复现的 A1 Unicode 缺口

协调方已实测 `create_template(title='bad\ufffe', price=Decimal('99'), product_asset_id='product')` 被接受，但 render_svg 的结果 `xml.etree.ElementTree.fromstring` 抛 ParseError。模型应拒绝 XML 1.0 不允许的字符，包括 U+FFFE/U+FFFF、代理字符与非法控制字符，保留正常中文、emoji 和换义后的 &<>。

抽取共享纯文本校验供模板、TextLine 和命令入口使用，避免后续 set_text 绕过。必要时允许最小改 `app/designs/commands.py` 和相应命令测试，但不要重构其他逻辑。先添加回归观察失败，再修复；渲染边界不通过替换字符悄悄改变用户文案。

## 测试与验收

- [ ] 创建→修改→撤销→重做→重启→读取固定历史 SVG；检查每次递增 revision 与价格/图层一致。
- [ ] 上传空文件、伪 MIME、文件名路径、超限、非法价格、缺字段、非法 ID/操作、错误 actor、过期版本、重放 key 的 HTTP 状态与无部分修改。
- [ ] XML 非法字符负例与中文/emoji 正例，模板和 set_text 都拒绝不合法输入。
- [ ] preview 精确绑定历史版本，素材损坏失败；SVG 响应头；跨站/非本地请求依旧拒绝。
- [ ] 生命周期测试用真实跨进程锁：同 DB 不同素材目录、不同 DB 同素材目录都拒绝；释放后可重开；初始化失败可重试；并发首次初始化无双实例。用临时目录，不能干扰用户工作台。
- [ ] 确认旧 API 请求不创建设计库，新请求不能修改运行路径。使用 TestClient lifespan，不模拟成功掩盖锁行为。
- [ ] 运行 A1/A2a/A2b 相关测试、旧 API/安全边界/worker_lock 回归与 ruff，文档写清实际可用 API 和仍无 UI/Agent 的限制，报告后停止。

协调方随后使用临时数据启动真实本机服务，对 health、创建、修改、冲突与精确 SVG URL 做独立 HTTP 验证。测试数据不进入真实工作台。
