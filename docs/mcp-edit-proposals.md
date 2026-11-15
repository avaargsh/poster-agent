# 通过 MCP 提出画布修改建议

Poster Agent 提供本地 stdio MCP 服务，将外部 Agent 的结构化修改建议送入已有画布。MCP 宿主负责模型账号和模型选择，HTTP 服务负责设计版本、建议保存与应用。

## 安装与启动

在项目根目录安装可选依赖，然后启动 HTTP 服务。

```bash
uv sync --locked --extra mcp
uv run --locked --extra mcp python -m app.run_local --port 8000
```

打开 `http://127.0.0.1:8000/canvas` 创建一份设计。侧栏设计库列出服务端保存的设计，可分页打开，清空浏览器最近记录后仍可找回。MCP 服务连接运行中的 HTTP 服务，使用同一设计存储。

## Codex

在项目根目录执行以下命令，注册本机 MCP。`uv --directory` 固定模块的项目位置，`--no-sync` 使用已经安装的依赖。

```bash
codex mcp add poster_agent -- uv --directory "$PWD" run --locked --no-sync python -m app.mcp_server --base-url http://127.0.0.1:8000
codex mcp list
```

GUI 宿主若找不到 `uv`，将命令中的 `uv` 替换为 `command -v uv` 返回的绝对路径。删除此项注册可执行 `codex mcp remove poster_agent`。

## OpenCode V2

在项目根目录注册，或按官方 V2 配置放入 `mcp.servers`。

```bash
opencode mcp add poster_agent -- uv --directory "$PWD" run --locked --no-sync python -m app.mcp_server --base-url http://127.0.0.1:8000
opencode mcp list
```

配置示例中的项目路径需替换为实际绝对路径。

```json
{
  "mcp": {
    "servers": {
      "poster_agent": {
        "type": "local",
        "command": ["uv", "--directory", "/absolute/path/poster-agent", "run", "--locked", "--no-sync", "python", "-m", "app.mcp_server", "--base-url", "http://127.0.0.1:8000"],
        "protocol": "legacy"
      }
    }
  }
}
```

配置依据为 OpenCode V2 官方 MCP 文档。本项目通过官方 Python SDK 的现代协商和旧版 initialize 握手做了真实 stdio 测试。[OpenCode MCP 配置](https://opencode.ai/docs/mcp-servers/)

## 工具与确认流程

| 工具 | 用途 |
| --- | --- |
| `list_designs` | 分页查找已有设计，返回当前标题、版本和画布链接 |
| `read_design` | 读取指定设计的当前版本、图层与素材元数据 |
| `propose_design_edit` | 提交绑定基础版本的修改建议，返回画布确认 URL |
| `read_edit_proposal` | 读取指定建议的状态及操作 |

可向已连接 MCP 的宿主提出需求，例如“列出已有设计，找到夏日活动海报，读取最新版本，把活动价字号改为100，保留商品位置，给我确认链接”。多个同名设计时由调用方确认目标。工具输入需使用新的 `request_id`，网络重试复用同一标识和完整请求。

`list_designs` 接受 `limit`（默认 20，范围 1–50）及可选 `cursor`。返回 `designs` 和 `next_cursor`，后者为空表示已到末页，继续翻页时原样传入。列表按不可变创建时间与设计 ID 倒序排列，编辑不会改变分页顺序。标题和版本反映本次读取的当前状态，正式提出建议前调用 `read_design` 获取最新图层。HTTP 入口为 `GET /api/designs`，无效或未知游标返回 422。

`propose_design_edit` 的参数示例使用 `document_id` 和 `proposal`。

```json
{
  "document_id": "doc_replace_with_your_design_id",
  "proposal": {
    "request_id": "example-edit-001",
    "expected_revision": 1,
    "summary": "放大活动价",
    "operations": [{"op": "set_style", "node_id": "price", "font_size": 100}]
  }
}
```

打开返回的 `confirmation_url`，查看建议说明和实际差异，点击“预览建议”后可显式应用。建议预览只读，应用创建一个新的设计版本。当前未保存的属性草稿存在时，应用按钮说明会放弃这些草稿。修改建议与正式设计审核分别管理，导出沿用既有审批接口。

建议状态包括 `PENDING`、`STALE` 和 `APPLIED`。期间有人保存设计时，建议会过期，需要基于新版本重新提交。`APPLIED` 指向已提交的编辑版本。保存响应丢失后，可以重试原建议，服务端复用固定命令凭据。撤销已应用的修改仍通过画布现有撤销操作完成。

允许的操作复用文本、价格、外观、几何和图层顺序命令。商业价格使用独立 `set_price` 与调用方提供的 `source_text`，用户需在预览中核对。MCP 返回素材元数据和链接，图片在本机画布中渲染。

## HTTP 与验证

建议接口位于 `/api/designs/{document_id}/proposals`，包括 POST 创建、GET 有界列表、GET 指定建议、GET `/{proposal_id}/preview.svg` 和 POST `/{proposal_id}/apply`。绑定精确基线与候选摘要，复用现有原子编辑和版本校验。摘要用于一致性检查，权限与正式审核由各自既有入口处理。

```bash
uv run --locked --extra mcp python -m pytest -q
uv run --locked --extra mcp --with 'playwright>=1.55,<2' python tests/browser_edit_proposals.py
```

标准客户端测试使用真实子进程和独立 HTTP 服务。浏览器测试覆盖 MCP 提议、只读预览、显式应用、重复调用、刷新恢复、过期建议、属性草稿处理、响应丢失重试及移动端布局。测试使用程序素材，MCP 宿主的实际模型效果由相应宿主与模型任务单独评价。
