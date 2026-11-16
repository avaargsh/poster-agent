# 画布中的 AI 修改任务

在画布点击“AI 修改”，输入对当前保存版本的修改需求，使用本机已登录的 Codex 账号生成建议。需求、设计文本、图层及素材元数据会发送到 Codex 使用的模型服务。图片像素在本机预览。

## 启用服务

需要 Codex CLI 和项目的 `mcp` 可选依赖。此功能默认关闭，启动时显式启用，并指定本次 HTTP 服务的 loopback 地址。

```bash
uv sync --locked --extra mcp
codex login
POSTER_DESIGN_AGENT_ENABLED=1 \
POSTER_DESIGN_AGENT_BASE_URL=http://127.0.0.1:8000 \
uv run --locked --extra mcp python -m app.run_local --port 8000
```

修改服务端口时，相应更新 `POSTER_DESIGN_AGENT_BASE_URL`。普通编辑和导出继续使用原有入口，PNG 的依赖配置见 [画布导出](canvas-export.md)。

本轮选择 Codex 作为首个内置执行宿主，已验证 CLI 0.169.0，使用其默认模型与现有登录状态，生成会消耗账号的模型额度。服务使用临时配置和只读宿主目录。模型通过受限 MCP 读取当前设计、提出绑定版本的建议、查询本次建议状态。服务端约束设计 ID、基础版本和请求 ID，限制12次工具调用与120秒执行时间。

## 操作与恢复

示例需求为“把主标题改成轻松开启亮白微笑，活动价字号设为100，保留价格金额和商品位置”。点击生成后可继续手动编辑。当前版本变化时，模型的旧版本建议会被拒绝或标记过期。

关闭面板后任务继续，可重新打开或刷新页面查看最近20条任务。输入草稿在本页面的面板切换之间保留。点击“停止 AI 任务”会结束对应模型进程组，已进入存储的建议保留供单独查看。

建议生成后，点击“查看并预览建议”进入已有差异面板。用户预览后显式应用，才会生成新的设计版本。审批和导出按实际保存版本继续处理。

提交响应丢失时，“重试原 AI 请求”复用原请求 ID 和参数。已完成、失败或停止的任务在重复请求下返回已有状态。服务同时执行一个任务，新的模型调用由用户再次提交。服务异常中断后，未完成任务恢复为 `UNKNOWN`，用户可以核对已有建议并决定后续操作。

## HTTP 契约

- `GET /api/design-agent/capabilities` 返回功能启用及依赖可用状态。
- `POST /api/designs/{id}/agent-runs` 接收 `request_id`、`expected_revision`、`prompt`（1–2000字符），返回202和持久任务记录。
- `GET /api/designs/{id}/agent-runs` 返回最近20条记录。
- `GET /api/designs/{id}/agent-runs/{request_id}` 读取对应任务。
- `POST /api/designs/{id}/agent-runs/{request_id}/cancel` 请求停止执行。

状态包括 `RUNNING`、`SUCCEEDED`、`FAILED`、`CANCELLED` 和 `UNKNOWN`。相同请求 ID 的参数变化返回409，旧版本提交返回409，服务未启用或依赖缺失返回503。HTTP 客户端需保留原请求以恢复不确定的提交结果。

## 验证范围

自动化使用确定性 CLI 替身和真实本地 HTTP/MCP/浏览器，验证幂等、停止、超时进程终止、重启、范围拒绝及建议预览确认。替身用于确定性协议回归。

真实账号验证使用程序素材，通过实际 Codex 任务生成标题和活动价字号修改，保存版本仍为1，并在 Chrome 中成功预览候选。模型输出字段与实际存储独立核对。视觉质量由用户继续评价，运行产生的 usage 按 CLI 报告原样记录。
