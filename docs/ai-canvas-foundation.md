# AI 画布基础：文档、SVG、持久仓库、原子命令与本地 API（Task A1 + A2a + A2b）

状态：A1、A2a 与 A2b 实现范围。已完成文档模型、受约束 SVG、离线演示、持久设计仓库、原子命令、幂等与撤销/重做，以及本地设计 API、独占存储生命周期和 Unicode 文本回归。
前端画布、Agent、MCP、PNG/Chromium 导出、账号登录与外部授权尚未实现。

本文描述的是当前代码的边界，不是整个 AI 画布已经完成的能力清单。设计依据见
`docs/superpowers/specs/2026-10-29-ai-canvas-design.md`，任务拆解见派发计划。

## 1. 本阶段交付

A1 新增：

- `app/designs/models.py`：有界 `DesignDocument`、页面、文本/图片/矩形图层、业务事实、素材与字体引用。
- `app/designs/templates.py`：确定性 1024×1024 模板 `create_template(...)`。
- `app/designs/rendering.py`：白名单 SVG 渲染 `render_svg(...)`。
- `app/designs/__init__.py`：模块公开接口。
- `scripts/design_demo.py`：无模型、无网络的离线样例。
- `tests/test_design_documents.py`、`tests/test_design_rendering.py`：契约、安全与演示测试。

A2a 新增：

- `app/designs/errors.py`：带稳定 `code` 与用户信息的领域异常。
- `app/designs/commands.py`：严格 `CommandBatch`、首批操作、`CommandResult`。
- `app/designs/repository.py`：SQLite 文档/版本/幂等/撤销栈仓库与内容寻址素材。
- `tests/test_design_repository.py`、`tests/test_design_commands.py`：持久化、原子性、冲突与历史测试。
- 本文。

A2b 新增：

- `app/designs/service.py`：懒初始化、进程内单例初始化、数据库与素材目录 POSIX 独占锁、可重复关闭。
- `app/designs/routes.py`：`/api/designs` 创建、读取、命令、精确 revision 和 SVG 预览。
- `app/main.py`：仅增加 router 注册与 lifespan 中的设计管理器接线。
- `app/designs/models.py`、`templates.py`、`commands.py`：共享纯文本/XML 字符校验。
- `tests/test_design_api.py`、`tests/test_design_lifecycle.py`：HTTP 契约、错误映射、跨进程锁和生命周期测试。

A1 测试保持原样。未修改旧海报模块、依赖或锁文件；未实现 MCP、前端画布、Agent 循环、固定字体安装、PNG 导出或浏览器保真验证。设计存储与旧 `worker_lock` 是独立作用域。

## 2. 文档模型

`DesignDocument` 的顶层字段：

- `schema_version`：当前固定为 `1`。
- `document_id`：`doc_` 加 32 位十六进制。
- `revision`：从 1 开始的正整数。
- `page`：宽、高、背景色；模板固定为 1024×1024。A1 允许 64–4096 的页面尺寸，其他尺寸不自动重排。
- `nodes`：1–200 个图层，列表顺序即绘制顺序。
- `asset_refs`：素材 ID 及可选内容哈希、MIME、字节数。
- `font_refs`：声明使用的固定字体族与字重。
- `facts`：业务事实；当前只包含价格和 CNY 币种。
- `source`：文档来源；A1 只支持模板来源与模板 ID。

图层是 Pydantic discriminated union：

- `TextNode`：语义角色为 `title`、`subtitle`、`price`、`original_price`、`cta`；文本拆成显式 `TextLine`，每行带自己的 `x`、`y`，渲染器不做浏览器自动换行。
- `ImageNode`：语义角色为 `product`、`logo`、`background`；只引用不可变 `asset_id`，并有 `contain` / `cover` 适配方式。
- `RectNode`：语义角色为 `background`、`decoration`；有填充、描边和圆角。

所有图层共享稳定 `node_id`、语义 `role`、位置、尺寸、旋转、透明度、可见性和锁定状态。A1 验证/保存这些字段；A2a 的命令强制锁定与编辑规则。

## 3. 有界校验

模型在所有入口强制拒绝以下内容：

- 未知字段、未知 `node_type`、未知 `schema_version`。
- 重复 `node_id`、重复素材 ID、引用未声明素材的图片图层。
- 非有限坐标、非正尺寸、超出范围和异常页面尺寸。
- 未声明字体的文本图层。
- 超过一次的关键语义角色（标题、价格、商品、Logo、背景等）。
- 非法的素材 ID：不允许路径、`..`、URL、大写或空格，只允许小写 ID 字符。
- 含控制字符、换行或空白的文本。
- XML 1.0 无法表示的字符：U+FFFE、U+FFFF、代理码点以及 C0/C1 控制字符；正常中文、emoji 与 `& < >` 保留，由渲染器统一转义。
- 价格超出范围或超过两位小数。

价格使用 `Decimal` 保存；JSON 中保持字符串（例如 `"199.90"`），模板和模型校验都会把价格规范到两位小数。文档必须恰好有一个 `price` 文本图层，且其文本必须等于业务事实格式化结果（例如 `¥199.90`），避免普通文字层绕过价格事实。专门的 `SetPrice` 命令在 A2a 实现；来源记录和授权适配器仍分开处理。

A1 不支持富文本、任意路径、嵌入 HTML、脚本、远程 SVG 或任意 SVG 源码进入文档模型。

## 4. 确定性模板

```python
create_template(*, title: str, price: Decimal, product_asset_id: str) -> DesignDocument
```

模板固定生成 1024×1024 页面，包含：

- `accent`：装饰矩形。
- `product`：商品图片图层，引用调用方提供的 `product_asset_id`。
- `title`：标题图层；按固定字符数切成显式行坐标。
- `price`：价格图层，文本由 `facts.price` 生成。

模板不会绘制占位商品。缺少被引用素材时，渲染明确失败。

A1 的 `document_id` 由模板 ID、标题、规范价格和商品素材 ID 确定性派生，便于离线复现。A2a 仓库为真实创建分配持久、唯一的文档 ID，不把该确定性 ID 当作数据库唯一键。

## 5. SVG 安全边界

`render_svg(document, *, assets: dict[str, bytes]) -> str` 只输出白名单结构：`svg`、`rect`、`image`、`text`、`tspan`、`g`，属性全部来自模型字段或确定性生成。

关键行为：

- 中文和 `& < >` 等字符经 XML 转义；文本不能闭合标签或插入元素。
- 图片只通过 `asset_id` 从调用方字典解析；不接受路径、文件 URL 或网络 URL。
- 图片先做有界检查再内联：
  - 只允许 PNG、JPEG、WebP；拒绝 GIF、SVG、HTML 和损坏数据。
  - 单素材最多 8 MiB、边长最多 4096、像素数最多 8,000,000。
  - 拒绝动画图、空字节、无法解码的内容和 decompression bomb。
  - 若 `AssetRef` 带哈希、MIME 或字节数，渲染器校验；不匹配即失败。
  - 解码后重新编码为 PNG data URI，去除原文件元数据，恶意文本块不会进入 SVG。
- 输出只包含 `data:image/png;base64,` 内联图片，不产生外部 `href`、`xlink:href`、`url(...)` 或脚本。
- 同一文档和素材重复渲染得到相同 SVG 字符串。
- 可见图片缺少素材时抛出 `MissingAssetError`；素材非法时抛出 `AssetValidationError`。

## 6. 离线 CLI 演示

```bash
.venv/bin/python -m scripts.design_demo --output-dir <new-directory>
```

行为：

- 只在给定目录不存在时创建；目录已存在（包括空目录）时退出码为 2，不覆盖任何文件。
- 用 Pillow 程序化生成一张可公开的示例商品图，不读取客户素材。
- 不调用模型、网络、付费生图或外部服务。
- 输出：
  - `product.png`：程序商品图。
  - `document.json`：可重新校验的设计文档快照。
  - `design.svg`：自包含 SVG。
  - `manifest.json`：文档 ID、revision、渲染器版本、文件哈希、素材哈希、字体声明和已知限制。

演示在写文档前把商品内容哈希绑定到 `asset_refs`，因此样例文档与 manifest 都能追溯同一份素材。

## 7. 持久设计仓库（A2a）

```python
repo = DesignRepository(db_path: Path, asset_dir: Path)
document = repo.create(title="夏日特惠", price=Decimal("199.90"), product_bytes=png_bytes())
latest = repo.get(document.document_id)
exact = repo.get(document.document_id, revision=1)
assets = repo.read_assets(latest)
```

路径由调用方显式提供，不读取 `.env`，模块 import 时不写文件。构造仓库时创建数据库结构、父目录和素材目录；测试全部使用 `tmp_path`，不使用默认数据目录。

- `create` 用现有模板生成文档，把商品字节交给现有受约束渲染验证，再绑定完整 SHA-256、实际媒体类型和字节数。`document_id` 由仓库生成唯一值，`revision` 固定为 1。相同输入调用两次会得到两个独立项目，不把 A1 的确定性模板 ID 当作唯一键。
- `get` 默认读取 head 指向的最新 revision；传入 revision 时只精确读取该历史快照。文档或 revision 缺失都返回 `NOT_FOUND`，不回退到最新版本。
- 正式文档的每个 `AssetRef` 必须同时带 `sha256`、`media_type`、`byte_size`；未绑定的 A1 素材引用不能通过仓库提交。
- 素材原始字节按完整 SHA-256 命名为 `asset_dir/<sha256>.bin`，数据库 `assets` 表按 `(document_id, asset_id)` 绑定完整哈希、媒体类型和字节数。素材先原子发布，再提交文档引用；同哈希已有文件会先按完整哈希与大小验证后复用。失败可能留下未引用的完整素材，但不会留下被文档引用的半文件。
- `read_assets(document)` 只读取索引中 `asset_id` 匹配且完整哈希一致的文件，重新校验大小、完整 SHA-256，并通过 `render_svg` 校验实际格式和 A1 限额。文件缺失、损坏、索引不匹配、符号链接或指向素材目录外的引用都失败关闭。素材目录本身或引用文件是符号链接时拒绝读取。
- 素材发布失败（包括目录不可写）不会提交文档。数据库路径、SQL 文本或服务器绝对路径不会出现在领域错误消息中。

SQLite 是文档、head、历史、命令幂等、撤销/重做栈和素材索引的事务权威。每次写入使用独立连接、`busy_timeout` 和显式 `BEGIN IMMEDIATE`；读取使用短生命周期连接，不把裸连接共享给线程。事务内核对 `expected_revision`、应用全部操作、重新验证完整候选文档，然后一次提交。并发写同一 revision 时只有一个成功，另一个得到 `REVISION_CONFLICT`。

## 8. 统一命令与撤销/重做

```python
batch = CommandBatch(
    command_id="cmd-8f3a2c1d",
    expected_revision=1,
    actor="user",
    operations=[SetPrice(price=Decimal("128.50"), source_text="用户确认 128.50")],
)
result = repo.execute(document.document_id, batch)
```

`CommandBatch` 严格禁止额外字段：

- `command_id`：8–128 位 ASCII 字母、数字、下划线或短横线。
- `expected_revision`：严格正整数。
- `actor`：`user`、`agent`、`cli`、`external`，仅作为调用元信息记录，不是授权证明。
- `operations`：1–20 个操作；未知字段或未知 `op` 被拒绝。

首批操作：

- `SetText(node_id, text)`：仅 `title`、`subtitle`、`cta` 文本节点，1–200 个纯文本字符，拒绝空白、控制字符和换行。保留节点位置、尺寸和字号，按固定字符宽度生成显式 `TextLine`；首版不是字体度量，长文本可能溢出文本框。不能修改 `price` / `original_price` 或图片、矩形。
- `SetPrice(price, source_text)`：正 `Decimal`，最多两位小数；同步业务事实与唯一价格节点。`source_text` 是 1–200 字符的用户来源记录，随命令记录持久保存用于审计，不代表账号认证或人工审批。普通 `SetText` 无法绕过价格校验。
- `SetStyle(node_id, color?, font_size?, opacity?)`：至少一个字段。文本可改 `color`、`font_size`；矩形 `color` 映射到 `fill`；图片拒绝 `color` / `font_size`；`opacity` 适用于所有节点。值域沿用文档模型。
- `TransformNode(node_id, x?, y?, width?, height?, rotation?)`：至少一个有限字段并符合文档界限。移动文本时按 `dx` / `dy` 同步所有 `TextLine` 的绝对坐标；缩放文本框不改变字号，也不声称完成重新排版。
- `SetNodeLock(node_id, locked)`：`locked` 为严格布尔值。锁定后其他节点命令返回 `LOCKED_NODE`；这是唯一能操作已锁定节点的命令，显式解锁后才能继续编辑。
- `ReorderNode(node_id, index)`：严格整数，索引范围 `0..len(nodes)-1`，移动图层但不复制图层；锁定节点拒绝。
- `Undo()` / `Redo()`：不带额外字段，必须单独占整个 batch。每次撤销/重做创建新的递增 revision，不删除历史。首次创建不能撤销；新的普通编辑清空 redo 分支；一个 batch 是一个撤销单位；重复 `command_id` 不会重复撤销。

命令幂等作用域为 `(document_id, command_id)`。先查已有成功记录，再核对当前 revision：相同输入（含 actor、expected_revision、操作）返回当初提交的 revision 快照并标记 `replayed=True`；同一 key 换输入返回 `IDEMPOTENCY_CONFLICT`。指纹由排序后的规范 JSON 和规范化 Decimal 计算，不使用进程内 `hash()`。任何失败都会回滚，不写入幂等成功记录、历史或部分修改。

新增 AddNode、RemoveNode、ReplaceImage 尚未实现，留待 B 阶段明确交互和资产导入契约。A2a 不提供 HTTP、MCP 或画布接口。

## 9. 稳定领域错误

`app/designs/errors.py` 中的异常都带稳定 `code`、固定的用户消息和 `as_dict()`：

| code | 含义 |
| --- | --- |
| `NOT_FOUND` | 文档或精确 revision 不存在 |
| `INVALID_COMMAND` | 文档语义不允许该操作或候选文档校验失败 |
| `REVISION_CONFLICT` | `expected_revision` 不是当前 head |
| `LOCKED_NODE` | 目标节点已锁定，需先显式解锁 |
| `ASSET_INVALID` | 素材缺失、损坏、索引不匹配、格式非法或发布失败 |
| `IDEMPOTENCY_CONFLICT` | `command_id` 已用于不同输入 |
| `NOTHING_TO_UNDO` | 没有可撤销的编辑 |
| `NOTHING_TO_REDO` | 没有可重做的编辑 |
| `STORAGE_ERROR` | SQLite 读写失败，消息不包含 SQL 或绝对路径 |

## 10. 本地设计 API（A2b）

新增配置只在进程启动时读取，后续请求不能改变存储位置：

| 环境变量 | 默认值 | 含义 |
| --- | --- | --- |
| `POSTER_DESIGN_DB` | `data/designs.sqlite3` | 设计文档、历史、命令与素材索引 SQLite |
| `POSTER_DESIGN_ASSET_DIR` | `data/design-assets` | 内容寻址素材目录（`<sha256>.bin`） |

API 全部位于 `/api/designs`，继续经过应用已有的 localhost、Host 与 Origin 边界：

| 方法与路径 | 行为 |
| --- | --- |
| `POST /api/designs` | multipart `product`、`title`、`price`；最多读 `MAX_ASSET_BYTES + 1`，超限 413；成功按 A2a `create` 保存并返回 201 `{document, preview_url}`。 |
| `GET /api/designs/{document_id}` | 返回当前 head `{document, preview_url}`。 |
| `GET /api/designs/{document_id}/revisions/{revision}` | 精确读取该 revision；缺失返回 404，不回退到最新。`revision` 必须是正整数。 |
| `POST /api/designs/{document_id}/commands` | `CommandBatch` JSON；仅接受 `actor=user`，返回 `{document, command_id, replayed, preview_url}`。 |
| `GET /api/designs/{document_id}/revisions/{revision}/preview.svg` | 按精确 revision 读取并校验素材，再调用共享 `render_svg`。 |

`preview_url` 始终固定到响应中的具体 revision。SVG 响应为 UTF-8 `image/svg+xml`，带
`Content-Security-Policy: default-src 'none'; img-src data:; sandbox`、
`X-Content-Type-Options: nosniff` 和 `Cache-Control: no-store`。素材缺失、损坏或哈希/索引不匹配时返回结构化错误，不输出半份 SVG。

`POST /api/designs` 的图片只按内容校验，不信任上传文件名或声明 MIME；文件名不会参与路径拼接。空文件、无法解码或不受支持的图片返回 422 `ASSET_INVALID`，超限返回 413。

领域错误统一为 `{error: {code, message}}`：

| code | HTTP |
| --- | --- |
| `NOT_FOUND` | 404 |
| `REVISION_CONFLICT`、`LOCKED_NODE`、`IDEMPOTENCY_CONFLICT`、`NOTHING_TO_UNDO`、`NOTHING_TO_REDO` | 409 |
| 持久素材完整性损坏 `ASSET_INVALID` | 409 |
| 字段/命令非法 `INVALID_COMMAND` | 422 |
| 上传图片非法 `ASSET_INVALID` | 422 |
| 独占锁被其他进程持有 `DESIGN_STORAGE_BUSY` | 503 |
| 存储不可用 `DESIGN_STORAGE_UNAVAILABLE` | 503 |

框架自身拒绝缺失 multipart 字段或非法 revision 时仍使用标准 422，不回显上传内容或服务器路径。存储与渲染在同步路由或线程池中执行，上传文件读取后关闭。手动 HTTP 编辑入口只允许 `actor=user`；仓库里的 `actor` 仍只是审计元信息，外部 Agent 授权尚未实现，不能靠伪造 actor 获得审批。

## 11. 存储生命周期与独占锁（A2b）

`create_design_manager()` 只捕获进程配置。应用 lifespan 构造一个 `DesignManager` 并放入 `app.state`；第一次设计请求才：

1. 同时申请数据库与素材目录的 POSIX `flock` 独占锁。
2. 创建父目录、SQLite 结构和素材目录。
3. 构造并缓存 `DesignRepository`。

同一数据库或同一素材目录不能被第二个服务同时持有。锁文件位于目标路径旁，命名为 `.<name>.poster-agent-design.lock`，释放后保留，避免不同进程锁到不同 inode。初始化失败会释放已取得的锁；并发首次请求由进程内互斥锁合并为一次初始化；`close()` 可重复调用。新 lifespan 不会复用旧 repository 或旧锁。

缺少 lifespan 时，设计路由从 `request.app.state` 找不到管理器，直接返回 503，不会偷偷打开默认目录。旧 `/api/posters` 请求和旧 `worker_lock.single_worker()` 不读取、不创建、也不锁定设计路径；两套锁作用域独立。

## 12. 验证命令

```bash
.venv/bin/python -m pytest \
  tests/test_design_documents.py \
  tests/test_design_rendering.py \
  tests/test_design_repository.py \
  tests/test_design_commands.py \
  tests/test_design_api.py \
  tests/test_design_lifecycle.py -q
.venv/bin/python -m pytest \
  tests/test_api.py tests/test_security_boundary.py \
  tests/test_worker_lock.py tests/test_storage_recovery.py -q
.venv/bin/ruff check app/designs app/main.py \
  tests/test_design_documents.py tests/test_design_rendering.py \
  tests/test_design_repository.py tests/test_design_commands.py \
  tests/test_design_api.py tests/test_design_lifecycle.py
.venv/bin/ruff format --check app/designs app/main.py \
  tests/test_design_documents.py tests/test_design_rendering.py \
  tests/test_design_repository.py tests/test_design_commands.py \
  tests/test_design_api.py tests/test_design_lifecycle.py
.venv/bin/python -m scripts.design_demo --output-dir <new-directory>
```

A1 测试继续覆盖 JSON 往返、价格字符串、确定性模板、重复 ID/异常尺寸/未知字段、中文与注入转义、恶意素材、外链与超限、元数据剥离、渲染稳定性和离线 demo。

A2a 测试覆盖：

- 创建、重开、独立 ID、完整历史、缺失历史、非法文档 ID 与 revision。
- 商品字节非法时不建文档、素材复用、素材损坏/缺失/符号链接/索引不匹配。
- 素材发布失败不提交文档；`get` 与 `read_assets` 失败不暴露路径。
- 命令原子性、一批一 revision、过期 revision、并发同 revision。
- 相同 `command_id` 重放原 revision、换输入或 actor 冲突、规范化 Decimal 重放。
- `SetText` 与 `SetPrice` 的价格边界；锁定节点所有编辑被拒绝；显式解锁后恢复。
- 文本移动同步 SVG 坐标、缩放不改字号、图层排序、严格索引与布尔值。
- 撤销/重做递增 revision、批量撤销单位、编辑清 redo、重复 undo key、空栈错误无写入、新实例重开后可继续撤销/重做。
- 未知字段、未知操作、`undo`/`redo` 与其他操作混批被拒绝。
- 真实临时目录脚本：创建 → 改价 → undo → redo → 新仓库重开 → 读取素材 → SVG 渲染。

A2b 测试覆盖：

- 创建、读取最新、精确历史、非法 ID、非法 revision 与 404 不回退。
- 无 UI 的完整 HTTP 流程：创建 → 改价 → undo → redo → 应用重启 → 读取固定 revision SVG。
- 空文件、伪图片内容、超限、非法价格、缺字段、错误 actor、过期 revision、重放 command key、同 key 换输入、锁定节点与非法操作。
- 上传文件名不参与路径；持久素材损坏返回 409 且不输出 SVG。
- SVG Content-Type、CSP、nosniff、no-store；跨站与非本机请求仍被拒绝。
- 旧 API 请求不创建设计目录或锁；启动后修改环境变量不能移动已捕获的路径。
- 缺少 lifespan 返回 503 且不打开目录；同 DB/同素材目录互斥；真实子进程持锁时拒绝；释放后可重开；初始化失败释放锁并可重试；并发首次请求只构造一次仓库。
- 模板和 `set_text` 都拒绝 U+FFFE/U+FFFF、代理码点与控制字符，同时接受中文、emoji 和 `& < >`。

## 13. 已知限制与待验证项

- 未验证 PNG 导出、Chromium 渲染、透明背景、旋转图片或浏览器与 SVG 的视觉一致性；这些属于 A3。
- 字体只声明 `Noto Sans CJK SC`，未检查本机是否存在，也未打包字体。字体缺失的明确失败和固定字体证据属于 A3。
- 换行使用固定字符数切分并输出显式行坐标，不是字体度量或 Unicode 字形宽度计算；A2a 的 `SetText` 同样不做真实排版，长文本可能溢出文本框。中文排版和浏览器结果仍需 A3 实测。
- A2a 已实现正式文档的素材哈希绑定、SQLite 事务、幂等、冲突拒绝和撤销/重做；仍未实现 AddNode、RemoveNode、ReplaceImage、几何检查、OCR、选择框或拖动。
- A2b 只提供本机 HTTP API；没有前端画布、Agent 工具循环、MCP、账号登录、外部 Agent 授权、PNG/Chromium 导出或真实字体安装。
- 设计存储锁是单机 POSIX advisory lock，不是跨主机租约或账号授权；只支持 macOS/Linux。锁文件保留在磁盘上，不代表进程仍在运行。
- 手动 HTTP 命令入口只允许 `actor=user`。`actor` 本身不是授权证明，未来内部引擎应直接使用应用服务，外部 Agent 必须另建显式授权路径。
- A2a/A2b 不新增 EXIF/ICC/颜色管理能力；素材验证沿用 A1，不能宣称已做色彩管理。
- `document_id` 的确定性派生只用于 A1 可复现模板；A2a 仓库创建使用新的唯一 ID。
