# Poster Agent · 电商营销海报生成与验收

> **开源许可：** 本项目采用 [Apache License 2.0（Apache-2.0）](https://www.apache.org/licenses/LICENSE-2.0)，完整许可文本见根目录 [`LICENSE`](LICENSE)。正式发布前仍需核实代码、第三方依赖、字体、素材、示例数据及历史提交的权属和授权；Apache-2.0 不会自动授权你无权再许可的内容。详见 [发布准备清单](docs/open-source-release.md)、[贡献指南](CONTRIBUTING.md)、[行为准则](CODE_OF_CONDUCT.md)、[安全披露](SECURITY.md)。

本项目面向电商运营，将 **Brief → 人工确认 → 商品图海报生成 → 多版本修改 → OCR/规则验收 → 人工标注和可复现评测** 串成一个单机可运行的工作流。

> **适用范围：本地研究与产品 Demo。** 尚无登录、权限隔离、可靠任务队列、真实商品保真度验证和生产级事务；使用 `app.run_local` 启动，它会拒绝非回环绑定，应用层也会拒绝非 loopback 的直接请求、非本地 Host 和跨源请求（403）。这些是纵深防护，不是身份认证。不要直接公网部署，也不要用原始 Uvicorn 命令绕过启动检查。真实 Qwen 图片 API 可能产生费用，本地渐变背景不是 AI 生图。

## 快速启动

要求 **Python 3.12**（由 uv 根据 `.python-version` 管理）与中文字体；Mac 使用 PingFang，Linux 使用 Noto Sans CJK。先按 [uv 官方说明](https://docs.astral.sh/uv/getting-started/installation/) 安装 uv，不需要手动创建/激活 venv。

```bash
git clone git@github.com:musuntana/poster-agent.git
cd poster-agent
uv python install 3.12
uv sync --locked
cp .env.example .env
uv run --locked python -m scripts.generate_demo
uv run --locked python -m pytest -q
uv run --locked ruff check app scripts evals tests
uv run --locked python -m app.run_local --reload --port 8000
```

项目依赖以 `pyproject.toml` 为唯一声明、`uv.lock` 为可复现版本锁，开发依赖置于 `dev` 组。请用 `uv add` 更新依赖并同时提交锁文件；完整说明见 [uv 使用手册](docs/uv-workflow.md)。

打开 <http://127.0.0.1:8000>；API 文档在 <http://127.0.0.1:8000/docs>。首次示例会在 `data/examples/` 生成测试素材及预览图。若中文缺字，在 `.env` 中设置本机存在的 `POSTER_FONT_PATH`。

## 已实现能力（以当前 main 为准）

| 模块 | 当前能力 | 可信边界 |
| --- | --- | --- |
| M1 渲染与修订 | SKU/Logo 上传、1024×1024 预览、改价产生新版本、PNG 导出；本地背景或 Qwen-Image API | 10 MiB/1600 万像素限制、ICC→sRGB；原子文件替换与分阶段发布；仅本地 Demo |
| M2 Brief 与人工审核 | 规则/Qwen 结构化需求、LangGraph SQLite Checkpoint、规格人工确认 | 本地单进程工作流，没有登录和生产并发隔离 |
| M2/M7 执行可靠性 | AI 生图强制幂等键、执行历史、取消请求转 `UNKNOWN`、完整本地产物凭据核验与恢复、默认每小时 20 次和 1 路并发上限；启动时对执行库、Workflow 检查点库与海报目录取得独占锁 | macOS/Linux 本地文件系统；不能恢复缺失或损坏的产物；不保证跨系统 Exactly-Once |
| M3 Acceptance Gate | 文案/OCR、品牌词表、价格区域位置与冲突检查、报告 SHA-256、可视化诊断 PNG | OCR 缺失与不确定必须标 `UNVERIFIED`，不可冒充 PASS |
| M3 评测 | 合成 OCR 样本、20 张程序渲染 PNG、FP/FN/F1、逐版人工标注及 CSV | 合成数据不是真实业务精度；人工标签尚无独立审计 |
| M4a/M4b 多尺寸派生 | 三种安全缩放尺寸；4:5 原生提供经典分栏和商品主体优先两种版式 | 两种版式均有独立 OCR/规则报告；M5c 人工标注支持两种模板且按版式隔离，其他比例仍为安全缩放 |
| 真实 OCR 证据 | 独立 GitHub Linux CPU PaddleOCR 3.3.0 成功识别一张程序海报（7 行/7 坐标框） | [Actions 证据](https://github.com/musuntana/poster-agent/actions/runs/39586650764)；Mac M1 和真实客户图片尚未实测 |

**M4a 多尺寸安全派生已实现：** 从已保存的 1024×1024 PNG 离线导出 1080×1350（4:5）、1080×1920（9:16）、1200×675（16:9）。原海报整体等比例缩放、居中，空白部分模糊延展；不会重调 AI 或裁切中央内容。这**不是自适应重排**，新尺寸必须人工重新验收。详见 [M4a 说明](docs/m4a-channel-exports.md)。

**M4b 原生 4:5 布局：** 1080×1350，基于已保存的商品素材、Logo、背景和选定版本规格离线重排，不再次调用收费模型。现提供经典分栏（左侧促销文案、右侧商品）和商品主体优先（商品上方、促销下方）两种模板。M4c 已为两种模板提供独立 OCR/规则报告及各自的价格坐标区；M5c 已支持两种模板的独立人工标注与 CSV 导出，标签和报告均不得跨版式继承。旧三种比例安全缩放依然可选。详见 [M4b 开发说明](docs/m4b-native-feed.md)。

**M4c 独立 4:5 验收：** 经典分栏和商品主体优先版式均可对实际 1080×1350 PNG 执行规则检查或真实 PaddleOCR，使用不同价格坐标区域，并将报告绑定到当前 SKU、背景、Spec 和对应版式 PNG 的 SHA-256。默认不运行 OCR 时明确标记未验证，不继承方形版或另一版式的报告；人工标签同样按版式独立存储与导出。详见 [M4c 说明](docs/m4c-native-acceptance.md)。

**M5c 原生 4:5 人工质量标注：** 经典分栏和商品主体优先两种版式均可独立审核，在独立的 1080×1350 导出上审查商品完整度、可读性、构图及价格、标题、CTA。审核与原方图标签隔离，并绑定具体 PNG、源素材和规格 SHA-256；可导出专用 CSV，无法判断的标签不会被算作正确。两种版式的标签和 READY 状态均不得跨版式复用。详见 [M5c 说明](docs/m5c-native-human-review.md)。

**M5d 已新增 4:5 原生报告评测：** 对已有原生人工 CSV 与对应成功 PaddleOCR 报告做逐字段 TP/FP/FN/TN、弃判和覆盖率统计；活动价/原价必须同时核对文字及专属坐标，SKU 完整度、构图等仅做人工统计，不伪装为自动模型精度。没有真实报告则拒绝评分。参见 [M5d 评测说明](docs/m5d-native-evaluation.md)。

**M5h 本地验收队列：** 网页可选择经典分栏或商品主体优先两种 4:5 版式，分别查看最新版本的独立审核与 OCR 状态，并点击「打开审核」进入对应模板。M5d/M5e 可按指定版式独立评分，不能交叉复用人工标签和 OCR 结果；评测输出目录会绑定版式、审核员与 M5g 批次身份，阻止跨实验误覆盖旧证据。READY 不表示广告可投放。刷新不运行 OCR，也不自动放行。详见 [M5h 审核队列说明](docs/m5h-local-review-queue.md)。

**M5g 本地商品批量导入（含历史批次浏览）：** CLI 与网页均支持 1–100 个 SKU 的全批预检、显式应用、逐 SKU 进度、幂等续跑与 `batch_import_results.json` 证据账本。网页通过本机 API 上传 CSV 与图片，强制本地免费背景；工作台也能只读查看默认 CLI 账本。该功能不执行 OCR，也不虚构真实商品评测成绩。参见 [M5g 本地批量导入](docs/m5g-local-batch-import.md)。

**M5f 已加固试验证据：** 原生 OCR 报告记录运行适配器、PaddleOCR/PaddlePaddle 包版本及架构（**仅诊断，不可当作真实模型执行证明**）；每次盘点或评分会把旧指标归档到 `history/`，通过 `latest_score_status` 避免旧 F1 被当成最新结果。参见 [M5f 试验防混淆说明](docs/m5f-evidence-run-safety.md)。

**M5e 已提供本地评测盘点：** 使用 `uv run --locked python -m scripts.native_trial --reviewer local-operator` 自动找出未审核、缺真实 OCR 或报告过期的原生 4:5 版本；显式加入 `--score` 才会复用 M5d 生成指标和完整 CSV 标签快照。会提示不同商品素材数量不足，避免多个改价版本被误当成独立商品。参见 [M5e 试验手册](docs/m5e-native-trial.md)。

**M5k 加固未完成批次检测：** M5g 导入账本写入预期 SKU 清单与最终完成状态，M5j 拒绝将中断后仅完成的部分 SKU 当作完整评测批次。旧 M5g 账本需幂等续跑补写完成状态，不增加 OCR/AI 调用。

**M5j 已支持按导入批次独立评分：** 使用 `uv run --locked python -m scripts.native_trial --reviewer local-operator --batch-journal data/eval_runs/m5g_import/batch_import_results.json` 仅盘点本批次最新 SKU 版本；全批 READY 后显式 `--score`，结果附带批次账本 SHA 和 SKU 清单。缺一个样本也不会自动缩小分母。详见 [M5j 批次独立评测](docs/m5j-batch-evaluation.md)。

**M6 OCR 人工复核标注包：** 保存真实 OCR 证据后，可运行 `uv run --locked python -m scripts.render_m6_ocr_overlays --package-dir /path/to/m6_review_package`。脚本复用冻结 CSV、OCR JSON 和图片 SHA-256 校验，生成**不覆盖原图**的 `m6_ocr_review_overlays.zip`（编号检测框 PNG + 来源清单），供人工查看文字位置；缺坐标保留未知，不自动评分或批准。详细见 [M6 评测协议](docs/m6-real-sku-review-rubric.md)。

**M6 OCR 空间证据：** PaddleOCR 采集现在连同置信度保存识别行对应的像素检测框（`bbox`），严格拒绝不合法及越界坐标；如果模型没有返回检测框，证据仍会将位置标为未知。最终报告统计已定位/未定位 OCR 行数，但不自动宣称价格位置或文案质量通过。详见 [M6 评测协议](docs/m6-real-sku-review-rubric.md)。

**M6 严格 OCR 采集一致性门禁：** `scripts.validate_m6_cohort` 新增可选 `--ocr-evidence <m6_paddle_ocr_evidence.json>` 和 `--require-ocr-evidence`。严格模式下，缺文件、过期/模拟的捕获结果、SKU×版式缺口、评审 CSV 哈希或图片身份失配都会 `BLOCKED`；通过也仅表示本地 JSON 与冻结评审记录一致（`MATCHED_DECLARED_REAL`），**不证明真实模型执行和识别准确率**。默认兼容原有元数据门禁，但明确标记 `ocr_capture_consistency=NOT_ASSESSED`。详见 [M6 评测协议](docs/m6-real-sku-review-rubric.md)。

**M6 真实 OCR 批次证据采集：** 新增 `scripts.capture_m6_paddle_ocr`，在本机对 M6 审核包的方形/4:5 PNG 用已安装的 PaddleOCR 3.x 和明确指定的检测、识别模型目录做真实识别；记录两种版式每张图的 SHA-256、OCR 文本/置信度、模型文件指纹和环境耗时。它拒绝不一致或篡改过的 CSV/图片，仅写入 `CAPTURED_REVIEW_REQUIRED` 的本地私有证据，**不会自动将任何结果判为 PASS、不会主动做模型下载，也不代替 20 个真实 SKU 的最终门禁**。详见 [M6 评测协议](docs/m6-real-sku-review-rubric.md)。

**M6 真实 SKU 评测准备：** 已提供 M5g 生成清单、授权治理表、人工审核工作表和本地证据门禁；`scripts.prepare_m6_review` 可在本地工作台运行期间直接复用导出逻辑，从完整 M5g 批次记录的精确海报版本离线生成方图及原生 4:5 图（即使之后编辑出了 v2，也不会悄悄改评 v2），不会再次启动 ASGI 服务，并生成全部为 `UNVERIFIED` 的本地审核草稿，`scripts.validate_m6_cohort` 则检查版本、创建凭据与本地图片/规格哈希以及 M5j 证据范围。两者都不运行真实 OCR、不调用收费模型、不替代人工验收。详见 [M6 评测协议](docs/m6-real-sku-review-rubric.md)。

**M6 审核包 ZIP 下载：** 已准备好的网页批次现在可以直接点击「下载审核包 ZIP」，包含官方 M6 CSV、准备摘要及与评审行匹配的方形 / 原生 4:5 PNG。服务器先在本机校验图片 SHA-256，限定文件清单和压缩包大小，拒绝符号链接和目录外文件；不会把同目录其他私有文件打包。下载是只读操作，不运行 OCR 或付费模型，不代表人工评测通过。详见 [M6 评测协议](docs/m6-real-sku-review-rubric.md)。

**M6 网页一键准备审核包：** 完成网页 M5g 批次后，可显式点击「准备 M6 离线审核包」，在该批次的 `POSTER_BATCH_DIR/<batch_id>/m6_review_package/` 生成不可覆盖的方形/4:5 图片与人工审核 CSV 草稿；复用现有本地离线导出器，严格保留原始凭据核验，仍不调用 OCR 或付费模型，不会自动验收。详见 [M6 评测协议](docs/m6-real-sku-review-rubric.md)。

**M6 网页进度预览：** 本地工作台支持选取人工填写的 M6 registry 与 output reviews 两份 CSV，实时统计各 SKU 的授权/独立性声明、方形与 4:5 审核进度及缺行、未验证的输出引用；服务仅在本机解析，不保存 CSV、不读取图片或调用模型。它不运行真实 OCR，不核实图片 SHA-256 与授权真实性，不能替代 `scripts.validate_m6_cohort` 最终证据门禁。详见 [M6 评测协议](docs/m6-real-sku-review-rubric.md)。

**公开目录 SKU Smoke Test（不是业务质量评测）：** 可从 Amazon Berkeley Objects（CC BY 4.0）数据集选取 20 张目录商品图，在本地跑通批量导入与原生 4:5 海报导出。价格为确定性合成值；该流程不抓取淘宝、拼多多或京东，不执行真实 OCR 或人工评分，也不能证明真实价格准确性。操作步骤与限制见 [公开 SKU Smoke Test](docs/public-abo-sku-smoke-test.md)。

**M5i 已支持按导入批次筛选审核队列：** 在现有 M5h 页面选择 M5g 的 `batch_import_results.json`，浏览器仅在内存中筛选对应 SKU，不上传该文件，也不改变全量 M5e 评分样本范围。详见 [M5i 本地批次筛选](docs/m5i-batch-review-scope.md)。

所有自动化质量检查仍需人工最终审核；尚未实现 VLM 修复、SKU 视觉保真验证或所有渠道原生响应式排版。

## 常用操作

服务仅支持本机访问：`uv run --locked python -m app.run_local --port 8000`。开发时可加 `--reload`。不要用 `uvicorn app.main:app --host 0.0.0.0` 或反向代理/容器端口映射暴露服务；该启动保护不提供身份认证，原始 Uvicorn 命令也能绕过启动器。详见 [本地部署边界](docs/local-only-deployment.md)。

**无需云端 Key：** 网页选择本地背景，生成、修改并导出；`uv run --locked python -m scripts.generate_demo` 可检查本地字体与渲染。

**可选 Qwen 真实背景：** 在 `.env` 设置 `DASHSCOPE_API_KEY`、对应区域/Workspace 的 `QWEN_IMAGE_ENDPOINT`，以及 `QWEN_IMAGE_MODEL`。确保 Key、服务地域和模型互相匹配；勿将 `.env` 提交。详细变量模板见 [`.env.example`](.env.example)。Qwen 结构化 Brief 可按模板配置 `QWEN_CHAT_ENDPOINT`。

**M8 兼容图片响应：** Qwen 图片 API 在 `data[0]` 返回 HTTPS URL 或 `b64_json` 时均可解析 PNG/JPEG/WEBP；Base64 图片大小、像素、格式与 URL 下载接受相同的安全校验。仍不支持任意自定义 Provider、付费调用自动重试或生产多租户部署。适配来源、MIT 许可、离线测试和下一轮集成候选见 [上游模块集成记录](docs/upstream-integration.md) 与 [第三方源码声明](THIRD_PARTY_NOTICES.md)。

**OCR 环境诊断及真实单图推理（可选）：** 先按 [uv + OCR 安装说明](docs/uv-workflow.md#ocr-重依赖按需安装) 执行 `uv sync --locked --extra ocr`，再安装对应平台的 PaddlePaddle 推理引擎。

```bash
uv run --no-sync python -m scripts.ocr_probe
uv run --locked python -m scripts.generate_demo
uv run --no-sync python -m scripts.ocr_probe --infer --image data/examples/sample_preview.png
```

`--infer` 需额外安装真实 PaddlePaddle / PaddleOCR，使用 `uv run --no-sync` 保留平台专属推理包；安装见 [uv 工作流](docs/uv-workflow.md) 和 [本地 OCR 说明](docs/m3d-ocr-runtime.md)。Linux CI 的成功记录**不能当成 Mac 本机成功记录**。

**质量评测：** `uv run --locked python -m scripts.evaluate_acceptance` 重放合成文本测试；`uv run --locked python -m scripts.run_image_benchmark --ocr none` 生成 20 张 PNG 并标注 OCR 未运行；`--ocr paddle` 才调用真实本地 OCR。对实际海报请在网页中先人工审核并导出 CSV，再运行 `uv run --no-sync python -m scripts.evaluate_real_reports --annotations data/my-real-labels.csv --ocr-mode paddle`（要求已有与图片哈希一致的真实 OCR 报告）。

## GitHub Actions 额度策略

**不因每次 push 自动触发 CI。** Draft PR → Ready for review 时运行一次快速测试；修改后需要再次验证，则手动运行 CI quick 或给 PR 添加 `ci:run` 标签。完整离线评测选择手动 `full`；昂贵的 `Real PaddleOCR CPU` 仅手动执行。详细流程见 [docs/ci-budget.md](docs/ci-budget.md)。分支维护采用合并 PR 且 HEAD 精确匹配的保护策略，避免误删未完成工作。

## M7：当前安全与可靠性边界

- **已实现（本地单 Worker）**：非回环地址/请求保护、AI 强制幂等键、SQLite 付费并发与小时预算、超时或取消后的 `UNKNOWN` 处理、完整本地产物恢复。应用的 ASGI lifespan 会对 `POSTER_EXECUTION_DB`、`POSTER_CHECKPOINT_DB` 和 `POSTER_DATA_DIR` 申请独占 POSIX 文件锁，阻止其他进程在共享数据上同时启动或错误恢复未完成任务。
- **未实现（禁止公网或共享部署）**：用户认证、资源级权限、租户隔离、正式任务队列、分布式锁、每用户额度、经过真实计费/重启故障注入验证的生产恢复策略。
- **验证层级**：pytest/CI 仅证明模拟边界；真实 OCR 单图记录不能代表完整 M6。至少 20 个经授权 SKU 的真实 OCR、人工逐图复核和冻结证据仍未验收。

在 macOS/Linux 的本地文件系统运行 `uv run --locked python -m app.run_local --port 8000`。如果启动出现 `WorkerLockUnavailable`，先停止正在使用同一执行库、Checkpoint 库或海报目录的进程；不要删除磁盘上的锁文件。Windows 与网络文件系统不属于该锁的支持范围。见 [本地部署边界](docs/local-only-deployment.md) 与 [付费调用恢复手册](docs/paid-call-idempotency.md)。

## 代码入口与下一阶段

- `app/main.py` / `app/templates/index.html`：FastAPI 与前端；
- `app/compositor.py`、`app/storage.py`：确定性布局、版本素材；
- `app/review_service.py`、`app/workflows/review_graph.py`：规格确认与恢复；
- `app/execution_store.py`：执行账本与幂等；
- `app/acceptance.py`、`app/ocr_provider.py`、`app/ocr_price_layout.py`、`app/ocr_overlay.py`：质量检查、OCR、可视化；
- `app/human_annotations.py`、`evals/`：人工标签与评测。

**下一优先级：** 收集不少于 20 张真实商品海报、人工复核原生 4:5 与方形版本的文字/商品保真效果，并完成真实 OCR；随后再决定是否为 9:16 和 16:9 实现独立排版。VLM 自动修复、鉴权、多租户和异步队列仍属后续里程碑。

更多细节见 [当前文档索引](docs/README.md)，早期记录见 [历史归档](docs/archive/README.md)。


## License

The project license is Apache License 2.0 (`Apache-2.0`). See [`LICENSE`](LICENSE). Third-party dependencies, fonts, images, datasets, and other assets may have separate terms; review their provenance and permissions before redistribution.
