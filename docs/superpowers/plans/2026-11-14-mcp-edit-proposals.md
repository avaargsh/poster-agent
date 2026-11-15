# MCP 修改建议闭环

目标为外部 MCP 宿主读取设计、提出结构化编辑，用户在画布预览并显式应用，生成可撤销的新版本。实施沿用现有设计文档与命令，基线为 b0301bfd58e3c75003ccd85bd46134523fcbeb5b。

## 契约

- ProposalRequest 由 request_id、expected_revision、summary 和1–20条操作组成。操作复用 SetText、SetPrice、SetStyle、TransformNode、ReorderNode。锁定与历史管理保留在人工编辑入口。
- 同一设计的 request_id 固定绑定规范化请求。相同请求返回原建议，不同请求冲突。建议存储在设计仓库的同一个 SQLite 中，绑定精确基础快照、候选快照摘要和渲染版本。
- 建议基于历史版本生成独立候选，预览保持基线 revision。创建和预览不会推进设计版本。应用使用建议对应的固定 command_id，复用现有 execute 幂等、事务与 expected_revision 检查。应用状态从该命令的已提交凭据推导。
- HTTP 提供创建/列表/读取建议、固定候选 SVG 与应用接口。列表最多50项。读取按 document_id 隔离，错误沿用领域错误 envelope。
- MCP 使用官方 Python SDK 和本机 stdio，连接固定的受信任 loopback HTTP 后端。暴露 read_design、propose_design_edit、read_edit_proposal 三项工具，返回结构化数据及画布确认 URL。模型工具侧不暴露应用或文件读取功能。
- MCP 运行使用可选 extra。普通网页启动保持基础依赖。Python Quick CI 显式安装 MCP extra 并运行真实 stdio 客户端测试。
- 画布支持通过 proposal 查询参数打开建议、切换当前/建议预览、查看摘要和价格变化，以及显式应用。待保存属性存在时，应用按钮说明会放弃这些草稿。过期建议显示冲突并要求重新提出，保存结果未知可重试同一建议。

## 实施与验证

1. 为建议的无副作用、幂等、非法操作、锁定、陈旧基线、应用重放、撤销及重启编写回归，观察缺失行为，再实现仓库方法。
2. 编辑投影和 execute 复用同一个内部操作入口。保留既有模型校验、价格来源、资产索引与审批导出规则。
3. 验证 HTTP 常规入口、Host/Origin 边界、固定候选预览与损坏/过期建议失败路径。
4. MCP 通过真实子进程 handshake、tools/list 和 tools/call 连接临时 HTTP 服务。验证结构化输出、错误处理、只允许固定 loopback 后端、创建建议后原设计版本不变。
5. 在真实 Chrome 验证建议预览、确认应用、重放、版本冲突与属性草稿处理，沿用独立临时数据。
6. 编写开源使用说明及 Codex/OpenCode 配置示例。运行锁定依赖全量测试、前端检查、独立审查和最终提交的云端双版本/浏览器 CI，再合并。

框架决策由实施方选用官方 MCP SDK，stdio 适用于本地单机项目。宿主负责模型账号和模型选择，新增适配器复用现有本机 HTTP 服务，避免多个进程争用设计数据库。
