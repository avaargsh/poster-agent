# CI 额度与分支治理（2026-11）

## 改动目标

GitHub Actions 不再因**每次 push / 每次 PR synchronize** 就自动执行测试或下载 OCR 模型。工作流拆分为轻量校验、完整离线评测和昂贵的真实 OCR：

| 类型 | 触发时机 | 执行内容 | 自动触发？ |
|---|---|---|---|
| 快速 CI | 有代码或前端变更的 PR 从 Draft 切换成 Ready for review | merge ref 与源提交分别运行 Python 检查、pytest、前端类型检查、构建及产物一致性检查 | 显式切换时触发 |
| 快速 CI（补测） | 给已有 PR 添加 `ci:run` 标签 | 与 Ready 相同的双版本验证 | 明确标记时才触发 |
| 快速 CI（手动） | Actions → CI → Run workflow，选择 `quick` | 对选择的引用运行 Python 与前端检查 | 否 |
| 浏览器 smoke | 给 PR 添加 `ci:browser` 标签 | 精确源提交上的 Chromium 操作回归 | 明确标记时才触发 |
| LangGraph 兼容性 | 给 main 目标 PR 添加 `ci:compat` 标签 | 精确源提交上的旧检查点生成与恢复验证 | 明确标记时才触发 |
| 完整 CI（手动） | Actions → CI → Run workflow，选择 `full` | 快速 CI + 合成 OCR 评测 + 20 张 PNG + 产物上传 + demo | 否 |
| 真实 PaddleOCR | Actions → Real PaddleOCR CPU → Run workflow | 下载 CPU 模型、真实识别、证据验证；可选 20 图 | **只允许手动** |

不配置 `push`，不配置 `schedule`。合并 PR 到 `main` 不会再触发一遍普通 CI。昂贵的 OCR 工作流没有 `pull_request` 或 `push` 事件。

文档、README 或 Markdown 文件单独变化时，普通 CI 的 PR 路径过滤器默认不会启动 Runner。更改应用、前端、测试、依赖锁或所列工作流文件时，在 PR 切换到 Ready for review 的那一刻运行快速检查。stacked PR 也按其实际目标分支生成合并引用，并独立验证源提交。

### 推荐日常流程

1. 多次提交代码到 `feature/...`：**都不会自动运行 CI**。
2. 创建 **Draft PR**，继续修改、测试代码。
3. 开发和本机测试稳定后，点击 **Ready for review**：仅执行一次快速 CI。
4. 如审查后又修改代码，**不会自动重新跑**。需要验证新 HEAD 时，移除旧的 `ci:run` 标签再重新添加，或者选择 Actions 手动运行。
5. 仅对重要里程碑或可能影响图片生成/评测的数据契约手动选择 `full`。
6. 仅在 OCR 引擎、模型或推理代码调整后，有明确需要时手动运行 `Real PaddleOCR CPU`。

**注意：** PR 如果创建时就处于 Ready 状态，不会触发 `ready_for_review` 事件。此时请添加 `ci:run` 标签或手动运行。移除并重新添加标签会发出新的显式验证请求。`ci:run`、`ci:browser` 和 `ci:compat` 各自对应不同任务。

项目依赖已统一到 `pyproject.toml` + `uv.lock`；见 [uv 工作流](uv-workflow.md)。

### Actions 页面手动启动

- [CI 工作流](https://github.com/avaargsh/poster-agent/actions/workflows/ci.yml) → **Run workflow** → 选分支 → 选 `quick` 或 `full`。
- [真实 OCR 工作流](https://github.com/avaargsh/poster-agent/actions/workflows/real-ocr.yml) → **Run workflow** → 选择 `main` → `full_benchmark` 默认 false。

如使用已认证的 GitHub CLI：

```bash
# 指定需要检查的代码分支；新改动后要手动再跑
gh workflow run ci.yml --ref feature/my-change -f level=quick

# 重要功能验收时执行完整离线评测
gh workflow run ci.yml --ref feature/my-change -f level=full

# 只有真实 OCR 变更才执行，通常使用已合并的 main
gh workflow run real-ocr.yml --ref main -f full_benchmark=false
```

**不要因没有 CI 自动运行就认为代码已经通过测试。** 合并前，至少用本机 `uv run --locked python -m pytest -q`，或在 PR 的最终 commit 上显式触发一次快速 CI。CI 的状态对应特定 commit；如果最后一次 CI 后又改了代码，原来的通过结果不保证适用于新提交。

### 费用优化与重要限制

- 移除每次 push 触发后，高频 commit 本身不会消耗 CI Runner 分钟。
- `full` 才会运行额外回放、OCR 环境诊断和上传构建产物；默认 `quick` 运行 Python 与前端检查。
- 完整离线产物保留时间从 14 天缩短为 **3 天**，减少存储占用。
- 同一任务在同一 PR/分支上的重复请求会取消前一轮。并发控制放在 job 层，Python、前端、浏览器和兼容性任务使用不同分组。被跳过的标签任务不会取消另一类正在运行的验证。
- **GitHub Branch protection / Rulesets**：如果仓库把 `CI / python-tests` 设为 *Required status check*，基于路径过滤、仅准备审查时触发及手动派发可能让检查长时间 Pending，阻止合并。此时应在仓库 Settings → Rules → Rulesets/Branch protection 审查要求，选择始终对需要合并的 PR 触发适用的检查，或者调整 Required checks 规则。**本次没有修改仓库分支保护设置。**
- GitHub 官方特别指出 workflow path filtering 可能导致必选检查处于 Pending；别把这个当成代码出错。
- 若 PR 在 Ready for review 后又更新，请显式补测；不会为节省额度而把旧 HEAD 的结果假装为新 HEAD 验证。
- 真实 OCR 的 Docker/PaddlePaddle 下载仍耗时且消耗 Runner 配额，因此只应按需运行。所有结果仅能证明合成样本上的模型链路，不能替代真实商品海报准确率评估。

## 已合并分支清理

清理前记录本地、远端和工作区的精确提交，并核对开放 PR 的 head/base 依赖。普通合并可通过主分支祖先关系确认。squash 合并需核对 PR 的精确源提交与可达的合并提交，单独记录共享同一源提交的分支别名。

保存 Git bundle、清单和 SHA-256 校验，验证备份并演练恢复后执行删除预演。远端批量删除使用原子推送和逐分支的精确 SHA lease，引用变化时整个批次失败。本地已合并分支使用 `git branch -d`。具体分支及恢复路径记录在当次治理报告中。

参考：
- https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows
- https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax
- https://docs.github.com/en/pull-requests/how-tos/merge-and-close-pull-requests/troubleshooting-required-status-checks
