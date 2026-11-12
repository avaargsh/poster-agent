# 2026-11-12 项目治理与分支清理

审计起点为 `123ac0ddfe1903914093175a2516f9a8237bb429`。开始时 GitHub 无开放 PR，本地有一个工作区且工作区干净。历史审计的 #18、#20 已合并，以下清单对应本次重新读取的 GitHub 状态。

## 已完成的历史分支清理

删除了 20 个历史远端分支及 3 个历史本地分支。远端候选均经过精确源提交、PR 合并记录和主分支包含关系核对。squash 合并通过可达的合并提交确认。共享 #5 源提交的 integration 别名单独记录。

远端操作采用逐引用 SHA lease、删除预演及原子推送。执行前再次确认无开放 PR，并逐个读取远端 SHA。清理后 GitHub 的分支清单为 main。本次治理主题分支用于后续 PR 验证和合并。

| 历史远端分支 | 清理时完整 SHA | 对应已合并 PR |
| --- | --- | --- |
| `chore/stacked-pr-ci-governance` | `00e68617ebd32297bd8ad5294adf3fd876be6e1f` | [#21](https://github.com/avaargsh/poster-agent/pull/21) |
| `feat/approved-raster-source` | `6cd827153c05396e748d2253816abfac48f48680` | [#15](https://github.com/avaargsh/poster-agent/pull/15) |
| `feat/brief-design-link-contract` | `e7d82f2b3a5b0c14c2f4026dd29665fb507d278e` | [#8](https://github.com/avaargsh/poster-agent/pull/8) |
| `feat/campaign-brief-preflight-v1` | `58afcda94f661ca44337b1f1921bd2c5035353e8` | [#20](https://github.com/avaargsh/poster-agent/pull/20) |
| `feat/campaign-plan-v1-draft-contract` | `fe2f0a7cef6e69cba6f995db3fe994dee1a2c10e` | [#18](https://github.com/avaargsh/poster-agent/pull/18) |
| `feat/design-approval-audit-history` | `6049ef76c14a616cac684a626e3d276c52b3b924` | [#7](https://github.com/avaargsh/poster-agent/pull/7) |
| `feat/design-explicit-cta` | `18a7810e7d23d6252fe6ea7f28642d314fe6de16` | [#12](https://github.com/avaargsh/poster-agent/pull/12) |
| `feat/design-ocr-operation-journal` | `23708b5d63af7568b70ca937a6f2b33ab7198ab7` | [#17](https://github.com/avaargsh/poster-agent/pull/17) |
| `feat/design-ocr-pinned-local-models` | `080aef57eaa67eeb9aeec08036b7dc6fe24c4c89` | [#19](https://github.com/avaargsh/poster-agent/pull/19) |
| `feat/design-ocr-preview-fingerprint` | `0cae567ee635b96338dd3107d815860ecacaacc2` | [#13](https://github.com/avaargsh/poster-agent/pull/13) |
| `feat/design-ocr-target` | `b8a3e0c7d0c32079ddf1251b3ae7acbb2223e2e8` | [#10](https://github.com/avaargsh/poster-agent/pull/10) |
| `feat/design-ocr-unattested-checks` | `ab54c6e5208365fdf322bef64faa44d46b57ee25` | [#11](https://github.com/avaargsh/poster-agent/pull/11) |
| `feat/design-revision-approval-binding` | `63361d06b557e8577af517f766f563c5a0a8a67d` | [#5](https://github.com/avaargsh/poster-agent/pull/5) |
| `feat/local-reviewer-attribution` | `1399684ca12f78ea1e415082b73d5c8aa9e41202` | [#9](https://github.com/avaargsh/poster-agent/pull/9) |
| `fix/brief-qwen-idempotent-admission` | `4a0d6e4452c18c64e63e1271cce837ce657ea349` | [#2](https://github.com/avaargsh/poster-agent/pull/2) |
| `fix/review-per-run-locking` | `44360bb7c5a4a79b22b02617eb39380495e4dd1e` | [#4](https://github.com/avaargsh/poster-agent/pull/4) |
| `fix/sqlite-checkpointer-setup-race` | `38548fdb3942056b0c641a4787c4b992ceff96ce` | [#6](https://github.com/avaargsh/poster-agent/pull/6) |
| `integration/design-approval-rebased` | `63361d06b557e8577af517f766f563c5a0a8a67d` | [#5](https://github.com/avaargsh/poster-agent/pull/5) |
| `test/langgraph-cross-version-checkpoints` | `87ca4ee44b3a7f288a3961975bcd3bd8838f67db` | [#3](https://github.com/avaargsh/poster-agent/pull/3) |
| `test/m6-unattested-replay-boundary` | `a1710e07cae45a91e58453e001b0fccb91ad7cf5` | [#16](https://github.com/avaargsh/poster-agent/pull/16) |

本地清理的引用为 `codex/review-design-storage`、`codex/technical-evaluation-without-authorization`、`feat/create-draft-126`，均为 main 的祖先，使用普通 `git branch -d` 删除。

## 恢复记录

本机备份位于 `data/eval_runs/governance_20261112/`，包含完整 Git bundle、精确引用清单、GitHub 元数据、预演与删除输出、校验值及恢复说明。bundle 校验通过，并已在临时裸仓库恢复代表性 squash 源分支，恢复提交与原提交一致。

bundle SHA-256 为 `14bdce3a221c22d0e8e8b205baacd7ddb737547c927107ffda4fe48091700cc0`。

```bash
git bundle verify data/eval_runs/governance_20261112/refs.bundle
git fetch data/eval_runs/governance_20261112/refs.bundle refs/heads/feat/create-draft-126:refs/heads/feat/create-draft-126
```

远端恢复步骤按备份目录中的 `RECOVERY.md` 执行，逐项核对 `cleanup-plan.json` 的分支名称和 SHA。

## CI 治理修复

- 将并发取消控制移到各 job，各验证任务使用独立分组，避免新增标签产生的跳过工作流取消其他正在运行的检查。
- browser 和 LangGraph 兼容性任务显式检出 PR 源 SHA，使实际检出与任务声明一致。
- 前端路径纳入现有显式触发策略。新的前端任务分别对 PR merge ref 和精确源提交执行类型检查、正式构建、预渲染及已提交产物一致性检查。
- 更新 CI 手册中的任务、引用语义和分支恢复步骤。

## 验证

本次使用临时环境按最新 uv.lock 安装依赖，包含 LangGraph 1.2.19、checkpoint 4.3.1、SQLite checkpointer 3.1.3。测试的所有运行数据使用临时目录。

- 治理配置回归在修复前 3 项失败，修复后 3 项通过。
- 完整 pytest 为 967 passed in 160.46s。
- Ruff 检查及格式检查通过，197 个 Python 文件格式一致。
- actionlint v1.7.7 验证两个工作流通过。
- 前端 `npm run check` 的类型、构建和产物一致性检查通过。
- 仓库跟踪文件检查无 blocker。
- 独立只读审查未发现实质阻断问题。

云端执行结果随本次治理 PR 记录，合并前核对最终源提交及关联运行。阶段记录和分支备份可用于复核具体操作。
