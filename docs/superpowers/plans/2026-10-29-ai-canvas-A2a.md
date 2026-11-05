# AI 画布 A2a：持久文档与原子编辑

> OpenCode 是实现方，Codex 独立验收。用户已授权使用 opencode/big-pickle 发送必要设计与代码。只做本任务，不继续接口、画布、Agent 或账号集成；不提交、不推送、不读凭证或客户数据。

## 输入与文件边界

先读 `docs/superpowers/specs/2026-10-29-ai-canvas-design.md`、总实施计划、`app/designs/*` 和 A1 两份测试。

可新建 `app/designs/errors.py`、`app/designs/commands.py`、`app/designs/repository.py`、`tests/test_design_repository.py`、`tests/test_design_commands.py`。

可最小修改 `app/designs/models.py`、`app/designs/__init__.py`、`docs/ai-canvas-foundation.md`。不改 A1 已有测试、模板、渲染器、demo，不改其他旧文件。需要突破范围时先报告，不自己扩大。

仅用现有依赖与 Python 标准库，使用 `.venv/bin/python`、`.venv/bin/ruff`。测试和数据库全部使用 pytest tmp_path，不访问默认数据目录。

## 公共接口

- `DesignRepository(db_path: Path, asset_dir: Path)`：显式配置路径，不读取 `.env`，不在 import 时写文件。
- `repo.create(*, title: str, price: Decimal, product_bytes: bytes) -> DesignDocument`：验证商品并持久化文档，返回新唯一 document_id、revision=1，同样输入创建两次仍是两个项目。
- `repo.get(document_id: str, revision: int | None = None) -> DesignDocument`：读取最新或精确历史版本，缺失返回稳定异常，不回退。
- `repo.read_assets(document: DesignDocument) -> dict[str, bytes]`：只读取仓库索引中匹配 ID/完整 SHA-256 的素材，校验大小、哈希及实际格式，读到损坏或缺失素材明确失败。
- `repo.execute(document_id: str, command: CommandBatch) -> CommandResult`：原子执行、版本校验与命令幂等。
- `CommandBatch`：Pydantic 严格禁额外字段，包含 command_id（8–128 ASCII 字母数字下划线短横线）、expected_revision（严格正整数）、actor（user/agent/cli/external）、operations（1–20）。actor 是调用元信息，不作为授权证明，授权在后续适配器处理。
- `CommandResult`：document、command_id、replayed。重放返回当初已提交的 document revision，不把最新版本冒充重放结果。
- 领域错误包含 code 与稳定用户信息，至少 NOT_FOUND、INVALID_COMMAND、REVISION_CONFLICT、LOCKED_NODE、ASSET_INVALID、IDEMPOTENCY_CONFLICT、NOTHING_TO_UNDO、NOTHING_TO_REDO。不暴露 SQL 或服务器绝对路径。

## 首批操作契约

采用有 discriminator `op` 的 Pydantic union，未知字段/操作拒绝。

- `set_text(node_id, text)`：仅 title/subtitle/cta，纯文本且 1–200 字符，拒绝控制字符；保留字号与位置并生成显式行坐标。禁止 price/original_price 和非 text 节点。首版简单切行限制如实记录，A3 才做真实字体度量。
- `set_price(price, source_text)`：Decimal 字符串正价，最多两位小数，source_text 是 1–200 字符的用户提供来源记录，拒绝空白/控制字符。同步业务事实与唯一价格节点。普通 set_text 无法绕过。
- `set_style(node_id, color?, font_size?, opacity?)`：至少一个字段；text 可改 color/font_size；rect 可改 color 映射 fill；image 不接受 color/font_size；opacity 所有节点可用。沿用文档值域。
- `transform_node(node_id, x?, y?, width?, height?, rotation?)`：至少一个字段，有限且符合文档界限；移动文本必须按 dx/dy 同步所有 TextLine 绝对坐标。缩放文本框不会暗中改字号，不宣称完成重新排版。
- `set_node_lock(node_id, locked)`：唯一可操作已锁定节点的命令；显式解锁后才能编辑。locked 严格布尔。
- `reorder_node(node_id, index)`：严格整数，范围 0..len(nodes)-1；移动到索引，不重复图层；锁定节点拒绝。
- `undo`、`redo`：不带额外字段，必须单独占一整个 CommandBatch。撤销/重做创建新递增 revision，不删除历史。首次创建不能 undo。新普通编辑清空 redo。一次批次是一个撤销单位；重复 command_id 不重复撤销。

新增 AddNode/RemoveNode/ReplaceImage 留到 B 明确交互与资产导入契约后实施，本任务不声称已完成它们。

## 持久化与事务要求

- SQLite 是文档、head、命令幂等、撤销/重做栈和命令记录的事务权威。用连接上下文、busy_timeout 与明确事务边界，不共享裸 sqlite 连接给并发线程。
- 事务中核对 expected_revision，验证完整候选文档后一次提交。两线程同 revision 只允许一个成功；失败不得写入幂等成功记录、历史或部分修改。
- 幂等作用域 `(document_id, command_id)`。先查已有幂等结果再验证当前 revision，相同输入含 actor 返回原结果；相同 key 换输入必须冲突。规范化 JSON/Decimal 后求指纹，不用进程不稳定 hash()。
- 持久创建前所有资产引用必须包含 sha256、media_type、byte_size，禁止把 A1 允许的未绑定 AssetRef 保存为正式文档。素材原始字节按完整 SHA-256 寻址，asset_id 使用合法且唯一的 ID；索引绑定完整哈希，不用截断哈希作为安全校验。
- 商品字节检查必须复用受约束渲染验证或抽取等价入口，不接收外部路径、URL 或 SVG。对 EXIF/ICC/颜色管理本任务不新增虚假能力声明。
- 原子发布素材在数据库引用前完成，已有相同 SHA 文件复用但先验证。拒绝素材目录内符号链接与损坏文件，不能跟随引用读取目录外路径。失败允许留下未引用完整素材，不允许文档指向半文件。
- `repo.read_assets` 重读并校验不可变原始素材，再交给现有 render_svg。读写素材限额沿用 A1，任何错误失败关闭。
- 提交后能用新 repository 对象读取所有历史。SQL 查询参数化，文档 ID 先校验，目录不由调用方文档 ID 拼出任意路径。
- set_price 的 source_text 随命令记录持久保存，用于审计，不伪装成账号认证或人工审批。Agent 往后必须通过显式用户来源生成该操作。
- 本任务为应用库，不建立 HTTP 服务、MCP 或后台线程，也不改旧 worker_lock。单进程独占生命周期接入留 A2b；这里仍必须用事务保证并发版本检查。

## 测试驱动步骤

- [ ] 先写创建/重开/绑定素材/独立 ID/精确历史/缺失历史用例，运行确认行为缺失，再实现仓库最小接口。
- [ ] 写命令原子性、相同 command_id 重放与换输入冲突、过期 revision、并发同 revision、价格来源与 SetText 绕过拒绝、未知字段、非法数字/节点类型/锁定节点用例，确认失败后实现。
- [ ] 写文本移动后 SVG 坐标随动、图层排序、撤销/重做递增版本、批量撤销单位、编辑清 redo、重复 undo key、undo/redo 错误无写入、新实例重开后 undo/redo 仍有效用例，确认失败后实现。
- [ ] 写素材损坏、路径/ID 异常、符号链接拒绝和磁盘发布失败不会提交文档用例，确认失败后实现；不靠 monkeypatch 掩盖真实存储路径问题。
- [ ] 运行 A1+A2a 四个测试文件与 ruff check/format，跑一段临时目录创建→改价→undo→重开→SVG 渲染的真实脚本。
- [ ] 更新 foundation 文档：实际使用方式、已验证行为、A3 仍未验证的字体/PNG、尚未提供 API/画布；报告任务结果后停止。

## 完成判定

不根据测试数量判定正确；上述契约必须逐项有用例。禁止在测试中复制实现算法制造自证。保留 A1 两份测试原样并全绿。协调方会独立复核实现、追加边界探测并运行受影响回归。
