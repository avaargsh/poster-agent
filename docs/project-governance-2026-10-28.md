# 本地项目治理记录 · 2026-10-28

本轮仅核对本地分支和已有远端缓存。GitHub 不可用，未联网获取新的远端状态，也未推送。

## 分支整合

治理起点为 `0919201`。14 个非主分支引用包含 12 个不同提交头；其中两个本地分支早已是主分支祖先。远端缓存中的功能已由此前的压缩提交进入主分支。

逐一计算三方合并结果，检查全部冲突和无冲突区域。冲突仅涉及后续扩展和修复，例如 Starlette 上传兼容、模型固定摘要、环境变量隔离、技术评测用途、图层碰撞检查和审核凭据。保留主分支内容后，每个合并结果均与治理起点的文件树一致。通过 10 次普通合并补齐历史关系，逐次确认文件树未改变。没有重写已发布分支历史。

| 分支引用 | 审计提交 |
| --- | --- |
| `codex/technical-evaluation-without-authorization` | `5510d8a4393a21067dcce8fd72ef86d2917bb8c2` |
| `feat/create-draft-126` | `4fa56e8c2b3b03e26ffe635f7f7b14c3c4cc7534` |
| `origin/feat/batch-versioned-layout-reuse` | `d364200b472d0081501b052d7b636143fd3af75f` |
| `origin/feat/local-cutout-preview-and-source-preservation` | `c81c40dd0a151542566cd53722fc8352557f8b31` |
| `origin/feat/local-u2netp-cohort-runner` | `f3c50ac92282b5b342233d4872f68c7b24fed0da` |
| `origin/feat/m14-cross-stage-lineage-check` | `c9fb7d57b13b369e77a919a6da4977c633c1f67a` |
| `origin/feat/m6-cutout-human-review-workbench` | `67c683045f38d334a663c421146d14cec19a2c4c` |
| `origin/feat/m6-cutout-quality-evidence` | `8d6388528adfc08884ad20bced0ae7c75c4361ba` |
| `origin/feat/m6-cutout-review-verified` | `67c683045f38d334a663c421146d14cec19a2c4c` |
| `origin/feat/product-cutout-final` | `be056f0a012f68f3cf1f728836a31237287ff687` |
| `origin/feat/product-cutout-verified` | `c81c40dd0a151542566cd53722fc8352557f8b31` |
| `origin/feat/square-layer-collision-guard` | `8c77485687ffa7f5e033e59af92b365183736be0` |
| `origin/feat/versioned-square-layer-offsets` | `0675639cb3515c966193ac8d1f488443ba07aa65` |
| `origin/fix/m6-cutout-review-provenance-final` | `5f217168d34eace8b0f8a6a0a903d3486ebe4691` |

## 修复

- 四组浏览器回归共用系统 Chrome 检测，支持 macOS 应用路径和 Linux 命令；解析可执行文件符号链接，避免 Chrome 在应用包外寻找框架。
- 营销预设和 OCR 操作面板浏览器测试改为临时海报目录、执行库和检查点库，避免启动测试时接触用户工作台数据。另两组测试原本已有隔离。
- README 与 M6 协议中的字面换行和过度转义改成可阅读段落与可复制命令。正式验收示例显式指定 `--purpose quality_acceptance`，与技术评测默认用途区分。
- 贡献指南同步现有 Apache-2.0 许可证及本地模型证据边界。

## 备份和未提交工作

`data/eval_runs/project_governance_20261028/before.bundle` 保留整合前全部 Git 引用；同目录 `branch-audit.json` 和 `merges.json` 保存具体提交和逐次核验结果。备份与测试产物均被 Git 忽略。

另一个技术评测工作目录的未提交修改此前已移植到主分支，原目录保持原样。没有重置、清理或重写该工作目录。

## 验证与限制

634 项 pytest 全部通过，耗时 139.77 秒；四组真实 Chrome 回归全部通过。Ruff、Python 编译、uv 离线锁文件校验及 Git 跟踪文件检查通过。README 与 M6 shell 示例通过语法解析，正式 cohort 命令的用途参数另经校验。真实浏览器测试的 OCR 和抠图模型边界使用合成响应，海报生成、版本存储和页面交互使用实际本地服务。它们不能证明真实模型效果、客户素材质量或正式 M6 验收。

20 个合成 OCR 用例重放的案例级 F1 为 0.695652，存在 5 个误报和 2 个漏报；这是既有规则的观测结果，未为提高分数修改数据或降低门禁。另生成 20 张程序图片，未执行 OCR 时全部保持 UNVERIFIED。
