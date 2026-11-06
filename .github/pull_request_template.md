## 变更目的与范围 / Summary

- 用户或工程问题：
- 本次变更与明确非目标：
- 已复用的现有模块、成熟开源项目或官方集成：

## 实现与验证 / Validation

- [ ] 新增或更新了有意义的自动化测试。
- [ ] `uv run --locked ruff check app tests scripts evals` 已通过；如未运行，已说明原因。
- [ ] `uv run --locked python -m pytest -q` 已通过；如未运行，已说明原因。
- [ ] `uv run --locked python -m scripts.check_repository_readiness` 已运行并检查输出。
- [ ] 已记录精确命令、结果，以及验证对应的 commit SHA。
- [ ] 如有依赖变化，已通过 `uv add` 更新并提交 `pyproject.toml` 与 `uv.lock`。

## 证据与可信边界 / Evidence

- [ ] 明确区分合成/Mock、真实模型 OCR、真实商品人工评审与生产环境验证。
- [ ] 未将 CI 通过或合成样本结果表述为真实商品质量、客户准确率或生产就绪证明。
- 证据链接、命令与结果：

## 数据安全、隐私与费用

- [ ] 未提交 API Key、`.env`、真实客户图片、私有批次账本、数据库或本地评测产物。
- [ ] 未默认触发收费模型调用或昂贵的 OCR 模型下载。
- [ ] 如适用，已说明 API 鉴权、上传安全、任务并发、状态持久化与错误恢复边界。
- 数据隐私、运行成本、兼容性与迁移风险：

## CI、基线与合并门禁 / CI and merge gate

- PR Head SHA（完整 40 位）：
- Base branch / Base SHA：
- 依赖 PR（如 stacked）及父 PR 当前 head：
- [ ] 不依赖每次 push 自动 CI；通过 Ready 或重新添加 `ci:run` 标签显式触发 quick CI。
- [ ] 此次 **exact-head** Python 3.12 locked quick CI 已成功，并附 Actions run 链接；否则保持 Draft。
- [ ] 若父分支更新或需要合并到 main，已核查新基线/合并树，重新验证受影响契约与 CI。
- [ ] 独立代码评审已处理阻断意见；COMMENTED 不自动视为 APPROVED。
- [ ] 本次涉及 SQLite/Brief/Design 权威、重启与 ACK-loss 时，已说明仅仓库内验证或真实环境验证的证据等级。
- [ ] 如涉及真实 OCR、20 SKU、人工审核或发布，已单独提供对应实证；未以普通 CI 代替。
- 最终 exact-head / merge-tree CI 链接、评审意见、未解决门禁：

## 已知限制与后续工作

- 未完成或未实测的内容：
- 需要的人工验收、授权或本地证据：
