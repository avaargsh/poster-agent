# Codex 账号驱动画布验收

本项目通过本地 MCP 接受 Codex 的结构化修改建议。模型账号由 Codex CLI 管理。验收脚本创建一份使用程序素材的设计，再通过实际模型完成读取、提案与状态查询，最后独立检查服务端保存结果。

## 运行

需要已登录的 Codex CLI，以及包含 `mcp` extra 的项目环境。先启动本机 HTTP 服务，再在同一 Python 环境运行脚本。建议使用独立的演示数据目录。

```bash
uv sync --locked --extra mcp
codex login
uv run --no-sync python -m app.run_local --port 8000
```

另开终端执行以下命令。`--run-model` 会使用当前已登录账号发起真实模型任务，可能消耗账号额度。输出目录必须不存在，每次验收使用新目录。

```bash
uv run --no-sync python -m scripts.verify_codex_design \
  --run-model --base-url http://127.0.0.1:8000 \
  --output-dir data/eval_runs/codex-account/run-001
```

脚本以空的宿主工作目录和临时配置启动 Codex，使用只读模式，关闭 Shell、插件、连接器、浏览器与图像文件工具。MCP 限定为 `read_design`、`propose_design_edit`、`read_edit_proposal`。账号凭据由 CLI 内部使用，脚本仅记录登录类型。模型任务默认最多等待 120 秒，超时后终止本次进程组。

本次验收指定标题为“轻松开启亮白微笑”、活动价字号为 100。脚本检查实际工具调用、存储中的两项操作、候选内容、价格金额、商品位置与其余字段，并确认保存版本仍为 1。成功时 `report.json` 的状态为 `PASS_PENDING_CONFIRMATION`。`events.jsonl` 保留 CLI 事件，`stderr.log` 保留诊断信息，均写入本地忽略目录。

打开报告的 `confirmation_url`，点击“预览建议”查看差异，再由用户决定是否应用。应用会生成新版本。验收成功表示真实账号调用与结构化提案完成，视觉质量和人工认可需单独评价。HTTP 服务需要持续运行才能访问确认链接。

普通 CI 仅运行验收器的确定性回归，真实账号任务由上述显式命令启动。当前脚本已验证 Codex CLI 0.169.0 的参数与事件格式，其他版本需按实际运行结果确认兼容性。配置字段依据官方文档。[Codex 配置参考](https://learn.chatgpt.com/docs/config-file/config-reference)

## 2026-11-15 实测

使用 ChatGPT 登录的 Codex CLI 0.169.0 完成了一次真实任务。事件中观察到三项 MCP 工具成功返回，候选标题及价格字号符合要求，保存设计仍为版本 1。真实 Chrome 已打开该模型生成的建议，验证只读预览及确认按钮状态，建议保留待用户确认。

该次 CLI 记录的 usage 为 input_tokens 75038、cached_input_tokens 66176、output_tokens 453。这些数字是此单次任务的原始 CLI 计数，后续运行以各自报告为准。本次素材由程序绘制，未进行客户图片质量评测。
