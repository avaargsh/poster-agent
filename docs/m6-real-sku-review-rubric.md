# M6 真实 SKU 质量验收量表（人工离线）

关联 [Issue #31](https://github.com/musuntana/poster-agent/issues/31)。本文件是**待执行的评测协议**，不代表真实 SKU、真实 OCR 或产品质量已通过验证。

## 1. 样本冻结

- 至少 20 个**不同 SKU**，每个 SKU 有合法使用的商品图片及可选 Logo；同 SKU 的多次生成不增加独立样本数。
- 覆盖：透明/复杂背景、浅色/深色包装、长短中文标题、整数/小数价格、带/不带原价、不同 CTA 长度。
- 在本机保存 cohort CSV、图片和批次账本；不得提交客户素材、SKU 标识、图片路径、原始标注或密钥到 Git。
- 在执行前冻结 SKU 列表、生成参数和评分口径。记录批次账本 SHA-256、版本 ID、输入图片 SHA-256、输出图片 SHA-256。
- 同一 SKU 的方形和原生 4:5 分开审核；**统计独立 SKU 数时不可将两个尺寸算作两个 SKU**。

### M5g 生成清单与 M6 cohort registry 必须分开

**1. M5g 生成清单**：使用 [M5g 清单模板](../evals/templates/real_sku_cohort_template.csv)，复制到本地 `data/real_trial/real_skus.csv`，将授权素材放在同一目录的 `images/` 下。它必须严格遵循 `scripts.real_sku_batch` 的既有 schema；不要添加 M6 元数据列，因为导入器会拒绝不支持的列。

| 字段 | 约束 |
| --- | --- |
| `sku_id` | 必填；2–64 位 ASCII 字母、数字、下划线或短横线；批次内唯一 |
| `product_image` | 必填；相对 CSV 所在目录的本地 PNG/JPG/WEBP 路径 |
| `title` / `price` | 必填；由现有 `PosterSpec` 规则校验 |
| `original_price` / `subtitle` / `cta` / `style` | 可选；留空时使用现有默认值或不显示原价 |
| `logo_image` | 可选；有授权的本地 Logo 相对路径 |
| `background_mode` | **禁止出现在 CSV 中**；M5g 强制使用本地免费背景 |

模板只有表头，不能直接执行 `--apply`。填入至少 20 个不同 SKU 后，先运行 M5g 默认预检，再在检查通过后显式执行 `--apply`。

**2. M6 cohort registry**：使用 [M6 cohort 注册表模板](../evals/templates/m6_cohort_registry_template.csv)，另存为本地 `data/real_trial/m6_cohort_registry.csv`。这是人工治理清单，**不传给 M5g 导入器**；必须恰好每个 SKU 一行，且 `sku_id` 集合与 M5g CSV、完成账本的 `expected_sku_ids` 完全一致。

| 字段 | 约束 |
| --- | --- |
| `cohort_id` | 同一批次使用稳定 ID |
| `sku_id` | 与 M5g 清单逐项匹配；不同版本不增加 SKU 数 |
| `product_category` / `product_family_id` | 记录商品类别与同系列/近似变体分组，防止样本集中于少数商品家族 |
| `variant_or_packaging_condition` | 记录图片/包装条件，例如透明背景、白底、包装图、复杂中文价签 |
| `source_asset_authorization_status` | 仅允许 `AUTHORIZED` 才能进入评测；`PENDING/NOT_AUTHORIZED` 必须停止 |
| `authorization_evidence_ref` | 指向本地授权记录的内部引用；不要把授权文件、客户合同或隐私信息提交 Git |
| `logo_authorization_status` | `AUTHORIZED` 或无 Logo 时的 `NOT_APPLICABLE`；其它状态阻塞批次 |
| `source_image_sha256` | 填入并核对 M5g 账本中对应 SKU 的 64 位十六进制源图摘要 |
| `distinct_sku_verified` | 人工核对后填写 `YES/NO`；不能仅凭不同 ID 就宣称独立 SKU |
| `duplicate_source_image_reviewed` | 人工检查重复/近似源图并填写 `YES/NO`；重复图可以保留，但须在汇总中报告 |
| `reviewer_alias` / `notes` | 审核员代号与简短说明；不填写敏感客户信息 |

`authorization_evidence_ref` 对已授权素材必须非空，但只写本地授权记录的路径/编号，不复制授权文件内容。cohort registry 是人工维护的治理记录；现有脚本不会自动验证其真实性，它不是签名来源证明。

### M6 每个输出的人工评审工作表

使用 [M6 输出评审模板](../evals/templates/m6_output_review_template.csv)，另存为本地 `data/real_trial/m6_output_reviews.csv`。每个 SKU 必须恰好有方形与原生 4:5 两条记录，分别使用 `variant_id=square` 与 `variant_id=feed_4x5_native`；`source_version` 指向原 poster 的版本号，原生 4:5 不虚构独立版本 ID。

- `output_image_sha256` 必须是人工实际检查的那张 PNG 的 SHA-256；`output_image_path` 只填本地路径。
- 组合键 `(cohort_id, sku_id, variant_id)` 必须唯一；每个 SKU 恰好一条 `square` 和一条 `feed_4x5_native` 记录。`reviewed_at_utc` 使用 UTC ISO 8601 时间。
- 如果预期尺寸无法生成/导出，仍保留该 SKU×variant 行；`output_image_path` 和 `output_image_sha256` 留空，`overall_accept=UNVERIFIED`，并在 `overall_reason` 写明 `OUTPUT_MISSING` 或 `EXPORT_FAILED`。该缺失输出仍计入应有输出分母，不可删行。
- `source_product_sha256` 与 `request_sha256` 从 M5g 账本抄录，`spec_sha256` 对应该版本的 `vN.json`。
- 方形行的 `source_square_image_sha256` 等于该方形输出 SHA；原生 4:5 行填写其源方图 SHA（应与原生导出响应的 `x-source-image-sha256` 一致）。
- 字段状态使用 `PASS/FAIL/UNVERIFIED`；只有规格中不存在的副标题/原价等可选字段才填 `N/A`，且不进入分母。`overall_accept` 必须由人工明确选择 PASS/FAIL/UNVERIFIED，不得仅由字段状态自动代替；FAIL/UNVERIFIED 必须填写 `overall_reason`。
- 此工作表是 M6 的每输出人工验收与整体放行记录，不是现有 M3c/M5c API schema，也不能直接喂给 M5j。M5c 原生 4:5 标签仍用于既有 OCR 评分；评分前须核对它与工作表中的同一字段/图片哈希一致。

所有模板都只有表头，不能直接作为有效批次执行。填充的 cohort registry、M5g CSV、输出评审表、素材、账本和人工/OCR 证据都应留在本机；仓库只保存无真实数据的 schema 模板。正式评分前冻结 registry 与评审表 CSV，并分别记录 SHA-256。

## 2. 标注字段和标准

M5c 现有原生 4:5 审核界面保存的原始值是 `correct`、`defective`、`unsure`，不是本协议的汇总状态。汇总时统一映射为 `correct → PASS`、`defective → FAIL`、`unsure → UNVERIFIED`；保留原始 JSON/CSV，不修改现有接口契约。

| M5c 字段 | PASS 的必要条件 | FAIL 示例 |
| --- | --- | --- |
| `sku_integrity` | 商品主体完整，无明显变形；核心包装与品牌识别信息保留 | 主体被裁切/变形、关键包装信息被遮挡 |
| `text_legibility` | 整体文字对比度足够，大小和层级可读 | 文字模糊、低对比、拥挤到无法辨认 |
| `composition` | 商品与文字无明显碰撞，主要信息层级清楚 | 文案遮挡商品、布局严重拥挤或关键区域被裁切 |
| `title` | 与冻结 Brief 一致，文字完整、无错字且可读 | 数字/字词错误、漏字、截断 |
| `subtitle` | 若规格包含副标题，则内容与 Brief 一致且完整可读 | 文案不符、漏字、截断或与其它元素重叠 |
| `price` | 活动价金额准确，且视觉位置/层级符合规格 | 金额错误、缺失、位置错位或容易误认成原价 |
| `original_price` | 若规格包含原价，则金额准确且与活动价层级清晰 | 原价错误、原价冒充活动价、位置/划线关系混乱 |
| `cta` | CTA 与规格一致、完整可读、不遮挡商品 | 文案错误、缺失、截断或重叠 |

`subtitle` 为空或没有原价时，M5c 不会要求标注对应字段；这属于不适用，不应伪造一条 PASS。当前 M5c 没有独立的 `logo_brand` 或 `overall` 字段：Logo/品牌问题记录在 `sku_integrity` 或 `composition` 的人工理由中。M6 输出评审表中的 `price_position` 与 `original_price_position` 是额外的人工位置诊断字段；它们不属于现有 M5c API schema，也不单独参与 M5j OCR F1 计算。M6 的 `price` / `original_price` 仍记录整个价签字段是否正确（包含金额、位置与层级），位置子字段用于定位失败原因。

M6 输出评审表中的 `overall_accept` 是**单独的人工作出的整体接收/拒绝判断**，不能用字段规则自动代替。可额外计算 `derived_overall_from_fields` 作为一致性诊断：任一适用关键字段 FAIL 则派生 FAIL；所有适用字段均 PASS 则派生 PASS；其余为 UNVERIFIED。它不能覆盖人工 `overall_accept`；两者不一致时必须计数、复核并记录理由。若只由一位审核员标注，应明确报告限制；多个审核员分别导出、分别计分，不把分歧合并成未经验证的“专家共识”。人工的 UNVERIFIED 与 OCR 未运行/失败是两类不同缺口，必须分开统计。

### M6 离线审核包（复用已有导出接口）

M5g `--apply` 完成后，可先以只读模式检查批次，再显式生成待审核文件。**不会重新调用付费模型，也不会自动标记人工通过。** 执行目录只能使用全新路径，防止覆盖已有人工评审和图片。

```bash
uv run --locked python -m scripts.prepare_m6_review \
  --journal data/eval_runs/m6_real_sku_batch_001/batch_import_results.json \
  --output-dir data/eval_runs/m6_review_package_001

uv run --locked python -m scripts.prepare_m6_review \
  --journal data/eval_runs/m6_real_sku_batch_001/batch_import_results.json \
  --output-dir data/eval_runs/m6_review_package_001 \
  --apply
```

脚本严格使用 M5g 账本记录的原始海报版本（当前 M5g 版本为 v1），不读取可能已被后续编辑推进到 v2/v3 的 latest 指针；若账本指定版本缺失或损坏，则保留缺失输出记录，不回退到新版本。导出前还会将原始产品图、背景图、v1 PNG/规格和可选 Logo 与 M5g 创建时的执行凭据逐一核对；即使文件仍是可解码图片，只要内容被替换也会被标记为缺失证据。注意：现有 M5j `--batch-journal` 流程按每个 SKU 的最新版本审计，M6 最终门禁也要求评审版本仍是最新版本。因此若 M5g 完成后已创建 v2，评审包仍会保留 v1，但 M5j/M6 评分门禁会阻止将过期版本计入分数；不要绕过该门禁。若要评估新生成的 v2，应以新的 manifest 路径和新批次账本启动独立 M5g 批次。历史版本的独立 M5j 评分仍是后续工作。脚本直接复制该版本的方形 PNG，并复用 `/exports/feed_4x5_native` 的同一导出处理函数生成原生版。**不会创建第二个 FastAPI 工作进程，也不会请求运行中的本地 HTTP 服务**，所以工作台保持打开时仍可离线准备评审包。不会修改原海报或生成新版本。生成结果包含 `images/`、`m6_cohort_registry.csv`、`m6_output_reviews.csv` 和 `m6_preparation.json`。

- `cohort_id` 根据完整 M5g batch journal 的 SHA-256 自动生成（`m6-` 加前 12 位），同一批次草稿可保持一致；若账本变化，ID 也随之变化。初始 `source_asset_authorization_status=PENDING`、`distinct_sku_verified=NO`，不能代表授权或商品独立性得到确认；需人工依据本地证据填写。
- 两种尺寸都预填 `overall_accept=UNVERIFIED`、`overall_reason=NOT_REVIEWED`，审核员与时间为空，绝不伪造人工结论。
- 某个 4:5 生成失败时仍保留对应输出行，图片路径与哈希为空，原因填 `EXPORT_FAILED:HTTP_422` 等；修复并生成新批次审核包前，不允许从分母删除该 SKU。
- 生成的 CSV 与图片放在**同一目录**下，评审表中图片路径相对 CSV；不要单独移动 CSV。生成的 CSV 仍是草稿，后续应在此基础上填写人工判断、运行 M5j 和本地 M6 证据门禁。
- 目录位于 `data/eval_runs/` 并被 Git 忽略。不要把图片、填充后的 CSV、源路径和授权证明提交到公开仓库。

**M6 报告不可覆盖发布：** 最终验证报告会先写入同目录私有临时文件，写入完成后再以文件系统排他创建语义发布。即使另一个本地进程在路径预检之后抢先写入同名报告，也不会被本次运行覆盖；临时文件在失败时清理。仍建议每次试验使用唯一的报告输出路径，不要把本地 JSON 的生成视为可信签名或业务质量证明。

**OCR 检测框与价格位置复核：** M6 的 PaddleOCR 3.x 采集现在保留每条 `rec_texts` 的置信度及对应像素矩形 `bbox=[x_min,y_min,x_max,y_max]`（方形 1024×1024，原生 4:5 为 1080×1350），识别输出同时支持官方 `Result.json` 的 `res` 包装。越界、反向、非数值或与识别文字数量不匹配的检测框会使采集失败，并且不发布不完整 OCR JSON。OCR 未提供 `rec_boxes` 时每行 `bbox=null`，不会根据文本臆造位置；仍可采集文本以供人工判断。

严格 M6 校验支持旧版无检测框的 JSON，但会校验新版每个 `bbox` 对应的画布尺寸，报告 `ocr_capture_positioned_line_count` 与 `ocr_capture_unlocated_line_count`。若存在未定位文字，则显示警告。**这些是坐标记录与范围的检查，不是价格正确或价格区域正确的评分。** 原有 M3/M4 价格区域判定有特定模板范围，不可将方形范围直接用于 4:5。

### 通过网页生成与下载 OCR 标注预览

在已经完成的网页批次中，先使用「准备 M6 离线审核包」生成审核 CSV 与两种版式图片，再在本机安装适用版本的 PaddleOCR/PaddlePaddle 并执行真正的 OCR 采集命令。回到批次页面（也可从「历史网页批次」进入），点击「准备 OCR 标注预览」后，可使用「下载 OCR 标注 ZIP」保存本地审核辅助文件。

此入口不接收用户指定路径、不运行 OCR、不调用付费模型；仅从本机既有的 `m6_review_package` 读取冻结证据。接口 `GET /api/batches/{batch_id}/m6-overlay` 只报告文件是否存在；`POST` 必须显式执行，且已有 ZIP 时绝不覆盖；`GET .../download` 在实际返回文件前重新核对当前的 CSV/OCR/image SHA-256 和 ZIP 内所有预览图的 SHA-256。批次未完成、缺失或篡改证据、图片变化、危险符号链接和失效 ZIP 会阻断操作。

请注意：OCR JSON 中 `real_ocr_inference_attempted` 是本机自述，单独看网页状态或标注 ZIP 不代表独立证明真实推理、源素材许可、价格/商品正确或 M6 验收通过。网页仅支持可信本机回环地址，不具备用户身份验证和公网服务能力。

### 为人工审核生成 OCR 检测框标注图

M6 保存 `m6_paddle_ocr_evidence.json` 以后，可在本地运行：

```bash
uv run --locked python -m scripts.render_m6_ocr_overlays \
  --package-dir data/eval_runs/web_batches/<BATCH_ID>/m6_review_package
```

该命令**不运行模型、不修改原始海报或人工标签**。它读取已冻结的 `m6_output_reviews.csv`、OCR 采集 JSON 和原 PNG，校验完整 SKU×方形/4:5 集合、OCR 声明格式、来源哈希与当前图片字节，然后生成 `m6_ocr_review_overlays.zip`。ZIP 中包含两种画布尺寸的带数字编号检测框 PNG 及 `m6_ocr_overlay_manifest.json`，含对应的原图 SHA-256、标注图 SHA-256、OCR 证据摘要。标注图的数字与 OCR JSON 中每张图 `recognized_lines` 的 **1-based 行号**对应。沿用现有可视化最多显示前 35 个有坐标的框；清单会标记未画出的数量。没有坐标的文本仍需人工定位，不会臆造矩形或声称价格位置正确。

此 ZIP 使用同目录私有临时文件生成并排他发布：已存在同名文件时拒绝覆盖，生成中失败不发布不完整压缩包。**它不是验收 PASS，也没有验证真实模型执行、素材授权或业务文案正确率**；人工审核仍须参考冻结规格、来源商品与两张原图。若修改了审核 CSV、OCR 证据或源图，原 ZIP 会保留，不会被静默重建；需建立新审核批次并重新执行证据采集。

### 将 M6 OCR 采集绑定至最终本地证据门禁

在完成原有 M5g 账本、M6 授权与人工审核 CSV、M5j native manifest 和 readiness 核验之后，可显式开启**严格 OCR 采集一致性模式**。完整 `validate_m6_cohort` 命令保留已有必填参数，并追加：

```bash
uv run --locked python -m scripts.validate_m6_cohort \
  --manifest data/real_trial/real_skus.csv \
  --registry data/real_trial/m6_cohort_registry.csv \
  --journal data/eval_runs/web_batches/<BATCH_ID>/batch_import_results.json \
  --reviews data/eval_runs/web_batches/<BATCH_ID>/m6_review_package/m6_output_reviews.csv \
  --native-manifest data/eval_runs/m6_native_trial_001/native_trial_manifest.csv \
  --readiness data/eval_runs/m6_native_trial_001/native_trial_readiness.json \
  --ocr-evidence data/eval_runs/web_batches/<BATCH_ID>/m6_review_package/m6_paddle_ocr_evidence.json \
  --require-ocr-evidence \
  --output data/eval_runs/m6_validation_ocr_001/report.json
```

指定 `--require-ocr-evidence` 却缺少文件会返回 `BLOCKED`（退出码 2）；OCR 文件超 8 MiB、符号链接、非官方 schema、模拟引擎标记、跨 cohort、旧评审 CSV SHA、缺少或重复的方形/4:5 行、错误 PNG 摘要/海报 ID、无效识别行/置信度或异常模型指纹声明都会阻断。校验**不调用 OCR**、不重新加载模型，也不修改任何输入；报告保留 `expected_output_count` 原始分母，并新增 `ocr_capture_consistency`、`ocr_capture_observation_count`、`input_sha256.ocr_capture`。

不传 `--ocr-evidence` 的旧调用仍只做历史元数据/文件校验，报告显式标记 `ocr_capture_consistency=NOT_ASSESSED`；此种 `PASS` **不能当作 OCR 通过**。指定匹配采集文件时可能标记 `MATCHED_DECLARED_REAL`，但 `real_ocr_inference_attempted` 是操作者可编辑 JSON 中的声明，模型文件哈希也是采集时的本地记录；两者都不是可信硬件签名或独立执行证明。因此不论本地文件是否匹配，`ocr_runtime_attested=false`、`real_ocr_quality_verified=false`，不计算文字正确率、价格坐标准确率或产品投放资格。应另行结合 M5j 原始推理报告、真实 SKU 素材授权和人工视觉审核检查。

**操作顺序：** 完成人工审核 CSV 的修改后，冻结该文件、再执行 OCR 采集。采集文件绑定 `reviews_csv_sha256`；如果后续任何人工标签改变，严格门禁会拒绝旧 OCR 证据，必须保留旧报告并重新采集一份新的完整证据，不得直接修改 JSON 以绕过冻结绑定。

### M6 已绑定图片与模型的真实 OCR 采集（CLI）

先准备上述 M6 本地审核包。安装适合自己 OS/架构的 PaddlePaddle，并安装 `pyproject.toml` 中可选的 `ocr` extra（PaddleOCR 3.3.0）。只有在两个**现有**模型目录已准备且适配当前 PaddleOCR / PaddlePaddle 版本时，才执行：

```bash
uv sync --locked --extra ocr
uv run --locked --extra ocr python -m scripts.capture_m6_paddle_ocr \
  --package-dir data/eval_runs/web_batches/<BATCH_ID>/m6_review_package \
  --det-model-dir /absolute/path/to/real/detection-model \
  --rec-model-dir /absolute/path/to/real/recognition-model \
  --device cpu
```

采集脚本在本机有界读取 `m6_output_reviews.csv` 与当前图片，检查完整 SKU×方形/4:5 行、同一 SKU 的来源哈希一致性，强制 PNG SHA-256 和 1024×1024 / 1080×1350 尺寸匹配，并记录 PaddleOCR 3.x 实际 `predict()` 的 `rec_texts/rec_scores`、OCR/运行时版本、模型目录 SHA-256 指纹、耗时与每个输出的 SHA。任何失配/推理失败会阻断整个批次，不发布部分 `m6_paddle_ocr_evidence.json`；已有文件不可覆盖。

**这仍是证据采集，不是评分。** `CAPTURED_REVIEW_REQUIRED` 明确要求人工和规格核对，不代表价格正确、文字位置正确或海报可投放。`m6_paddle_ocr_evidence.json` 现在可通过上述 `--ocr-evidence --require-ocr-evidence` 参数在 `validate_m6_cohort` 中进行一致性核验，但它仍不能单独证明真实模型执行、OCR 正确率或 M5j/M6 质量验收。文件包含识别出的敏感文案，仅在本机 Git 忽略目录保存，不要公开提交。使用显式模型路径并不构成绝对断网保证；严格离线运行须通过系统或容器网络策略阻断出站连接。Mac M1 的 PaddlePaddle 可用性须与官方安装矩阵核对，必要时用 Linux CPU 环境执行；单元测试使用注入的假引擎，并不代表真实模型已运行。

### 从网页下载离线审核包 ZIP

完成网页 M6 审核包准备后，点击「下载审核包 ZIP」即可获取本机生成的 CSV、准备摘要与当前审核表中列出的两种版式 PNG，不必手动查找 `POSTER_BATCH_DIR`。接口 `GET /api/batches/{batch_id}/m6-package/download` 只读，仅对完整且已有审核包的网页批次有效。它先验证原始批次的产物凭据、所需审核行和图片摘要，再生成安全 ZIP：每张 PNG ≤20 MiB，CSV/JSON ≤2 MB，全部文件解压后总计不超过 256 MiB。**额外的未登记文件不会打包**，避免把本机私有文件意外带出；路径穿越、符号链接、文件损坏或超限会阻止下载。

ZIP 是便于本机导出、离线审核与人工交接的文件集合，**不是完整的科学/业务证据门禁**。两份人工 CSV 会按本机已填写的内容复制，允许仍标为 `UNVERIFIED`。请注意网页服务默认只支持本地回环访问；下载并转交 ZIP 前由操作员自行确认真实 SKU 的使用授权和数据脱敏。此接口既不修改现有审核包也不执行真实 OCR、付费模型或自动批准。

### 完成网页批次后，一键准备离线审核包

如果通过本机工作台「本地批量商品导入与进度」创建 M5g 批次，则完成后可直接点击「准备 M6 离线审核包」，无需再手工复制 `batch_import_results.json` 路径运行 CLI。工作台会先复用已有 `/api/batches/{batch_id}/review-scope` 的完整账本和原始创建凭据验证，再调用 `scripts.prepare_m6_review.prepare_package`，**只将离线草稿生成到** `POSTER_BATCH_DIR/<batch_id>/m6_review_package/`。默认 `POSTER_BATCH_DIR` 为 `data/eval_runs/web_batches`，如自定义环境变量，应以配置的根目录为准。

操作必须显式点击；读取 `/api/batches/{batch_id}/m6-package` 只做预检，不创建目录或执行模型。写操作 `POST /api/batches/{batch_id}/m6-package` 仅对状态 `COMPLETED`、账本完整且产物有效的批次生效；若目录已存在（包括未完成的部分输出），会拒绝覆盖。内部共用 CLI 的离线方形/原生 4:5 图片导出及 `UNVERIFIED` 人工表格，**无真实 OCR、无收费模型调用、无自动批准**。导出后请在本机打开新目录填充授权与审核记录，再使用下面的 M6 进度预览和最终证据门禁。生成包不是审查结果，也不证明 20 个真实 SKU 的质量。

### M6 网页人工进度预览（CSV-only）

在本地运行 Poster Agent 工作台（`uv run --locked python -m app.run_local --port 8000`），打开 `http://127.0.0.1:8000` 的「M6 · 本地评测进度预览」。选择 `m6_cohort_registry.csv` 和 `m6_output_reviews.csv`，点击「查看本地评测进度」。

工作台使用 `POST /api/m6/review-progress`，将所选文件交给**本机服务**在内存中解析；每个 CSV 最多 2 MB，registry 最多 100 行，reviews 最多 200 行。只接受官方模板列名及同一 cohort 的 SKU×版式唯一记录，拒绝重复、额外 SKU、跨 cohort 与格式错误。缺少应有的评审行也会继续计入预期分母。输出按 SKU 显示待补授权/独立性声明、人工字段、时间戳及图片路径/摘要的引用缺口。

**这里的“人工表格齐备”只是 CSV 格式与填写情况检查：** 授权声明是未经核实的人工自述；不打开图片文件、不核对实际 SHA-256、尺寸或 Spec、不读取 M5j、**不执行 OCR、不保存 CSV 或触发付费模型**。CSV 的 `N/A` 是否确实适用，必须留给最终 `validate_m6_cohort` 检查。即使预览没有待补项，也不能代替 M6 最终证据门禁和人工视觉质量验收。CSV 含本地运营数据，不要通过公网服务上传；Poster Agent 只支持本机访问。

## 3. 执行步骤

1. 用 M5g 进行 dry-run 预检；核对 CSV、路径及唯一 SKU ID 后显式 `--apply`。先使用本地免费背景，**不调用付费模型**。
2. 校验账本已完成、预期 SKU 集合与实际生成集合完全一致；不完整批次不得评分。
3. 为每个 SKU 保留方形 PNG 与原生 4:5 PNG；两者都记录 `poster_id` 和 `source_version`，原生图另标 `variant_id=feed_4x5_native`，不虚构独立的 native version ID。方图沿用 M3c 标注，原生 4:5 沿用 M5h/M5i 队列与 M5c 原生审核；两者都在 M6 输出评审表记录实际输出 SHA-256 和人工整体接收状态。
4. 在本机安装和诊断 PaddleOCR 后，真实运行 OCR；记录运行命令、系统架构、Python、PaddleOCR/PaddlePaddle 版本、推理耗时及失败信息。OCR 不可用时明确标记未验证。
5. 使用 M5e/M5j 对**同一冻结批次**进行盘点；仅在审核/报告齐备时显式评分，归档 CSV、报告摘要及 SHA-256。
6. 按下表生成匿名化汇总，不上传客户数据或图像。每一失败类型至少保存本地可复现的样本 ID、输入版本和复现步骤。

### 证据文件清单（本地保存）

| 证据 | 最低要求 | 边界 |
| --- | --- | --- |
| 冻结 M5g CSV | 完整 SKU 行、参数、CSV SHA-256 | 含本地路径，留在本机 |
| M6 cohort registry | 每个 SKU 的类别/家族、授权状态、独立性复核、源图 SHA-256；冻结 registry CSV SHA-256 | 只存本机；不得提交真实行；状态为人工自述 |
| M6 输出评审表 | 每 SKU × 方形/原生 4:5 的图片路径、源版本、输出 SHA、字段状态、人工 overall 与理由；冻结 CSV SHA-256 | 两种尺寸各一行；不作为 M5j OCR 输入 |
| M5g `batch_preflight.json` | 预检成功、SKU 数、不同源图哈希数 | 只证明预检，不证明生成完成 |
| M5g `batch_import_results.json` | `completed=true`、预期 SKU 清单/数量、每个 SKU 的 poster ID、请求与源图摘要 | 原始账本视为私有本地数据 |
| 方形 PNG 与规格版本 | SKU→poster ID/version、PNG SHA-256、规格 SHA-256 | 每次改价/改文案后都视为新版本 |
| 原生 4:5 PNG | 实际导出的 1080×1350 PNG、SKU/version、PNG SHA-256 | 导出需独立保存；不继承方图验收结果 |
| 人工审核证据 | M5c 原生审核记录、审核员 CSV、每个字段的结论与理由 | 审核员代号不证明身份；保留 `unsure` 的原始状态 |
| OCR 证据 | 原生 OCR JSON、运行状态、报告/图片哈希、环境与耗时 | 未运行/失败/弃判不得记为 PASS |
| M5e/M5j 盘点与评分 | `native_trial_readiness.json`、`native_trial_manifest.csv`、`native_real_summary.json`、`native_real_cases.csv`、`native_trial_labels.csv` | 只对指定完整批次评分；保留确切分母 |
| 环境记录 | OS/架构、Python/uv、PaddleOCR/PaddlePaddle 版本、运行命令、总耗时 | 版本信息是诊断记录，不是模型来源的密码学证明 |
| 匿名化摘要与摘要清单 | 最终统计、缺陷清单、对外文件 SHA-256 | 只分享经人工审查后的汇总，不分享客户素材、原始账本或原始标签 |

### 批次完成判定

在开始评分前，必须同时满足：

1. M5g 账本的 `schema_version=m5g-1`、`mode=apply`、`completed=true`；
2. `expected_sku_count` 等于 `expected_sku_ids` 去重后的数量，且 `set(expected_sku_ids) = set(rows.keys())`；
3. M5g 清单 SHA-256 与被冻结的 M5g CSV 一致；M6 cohort registry 的 SHA-256 已记录且 SKU 集合完全匹配，所有素材授权状态有效、`distinct_sku_verified=YES`、`duplicate_source_image_reviewed=YES`，且每个 SKU 都有唯一 poster ID 和有效输入摘要；
4. M6 输出评审表中每个 SKU 恰好有方形与原生 4:5 两条记录，输出 SHA 与实际审核 PNG 一致，且两条记录都明确填写人工 `overall_accept`；`overall_reason` 在 FAIL/UNVERIFIED 时非空；
5. M5j 的 `native_trial_readiness.json` 显示指定批次的所有 SKU 均 READY、`can_score=true`，且批次 SKU 数不少于 20；
6. 每个被评分的**当前最新版本**都具有对应的原生人工审核与成功 PaddleOCR 报告。任何修改版本都必须重新审核并重新运行 OCR。

任一条件不满足时停止评分，先修复或重新运行原流程；不得手动编辑账本来伪造 `completed`，也不得从分母中移除缺失 SKU。

### 本地证据门禁脚本

在 M5g 导入完成、两种尺寸的人工评审表已填写、且 M5j 已输出就绪报告后，运行下面的离线校验。它只读取本地文件，不执行 OCR、不调用模型、不上传数据；退出码 `0` 表示元数据与证据文件通过校验，`2` 表示阻塞项。`PASS` 不是视觉质量或生产就绪结论。

```bash
uv run --locked python -m scripts.validate_m6_cohort \
  --manifest data/real_trial/real_skus.csv \
  --registry data/real_trial/m6_cohort_registry.csv \
  --journal data/eval_runs/m6_real_sku_batch_001/batch_import_results.json \
  --reviews data/real_trial/m6_output_reviews.csv \
  --native-manifest data/eval_runs/m6_native_trial_001/native_trial_manifest.csv \
  --readiness data/eval_runs/m6_native_trial_001/native_trial_readiness.json \
  --output data/eval_runs/m6_validation_001/m6_validation_report.json
```

脚本会记录 manifest、journal、registry、review CSV、M5j native manifest 与 readiness 的 SHA-256，检查至少 20 个 SKU、素材授权状态、SKU 集合一致性、每 SKU 两种输出、最新版本、规格/图片哈希与尺寸，并要求原生 4:5 人工评审 PNG 的哈希与 M5j `native_image_sha256` 一致。缺失输出必须保留为 `UNVERIFIED`，仍计入应有输出分母，并阻塞通过。

**评审 PNG 文件边界：** M6 校验最多读取每张 20 MiB 的本地 PNG，空文件或超限文件必须 `BLOCKED`；拒绝直接以符号链接指定的图片文件，避免审核路径被悄然重定向。校验先有界读取一次图片字节，然后对同一份字节计算 SHA-256 并由 Pillow 解码，避免哈希与解码期间分别重开路径导致结果不一致。只有 PNG 格式且尺寸与目标版式相符时才做完整像素解码。文件哈希和解码合法仍不等于商品视觉保真或真实 OCR 验收。

M6 最终校验还会对每个 SKU 重新检查 M5g 创建时保存的不可变 v1 凭据，包括原始商品图、可选 Logo、背景、v1 方图和 v1 规格。校验复用 `app.batch_artifact_integrity.verify_batch_artifact`。即使评审表中的当前图片哈希与 M5j 报告一致，只要原始凭据或源素材已改动，最终状态仍为 `BLOCKED`。后续修订版本继续按原有规则核对当前最新版本，不会替代原始创建凭据。此检查验证本地一致性，不证明素材授权、OCR 真实性或人工评分质量。

## 4. 汇总报告模板

### A. 每个输出尺寸的总体结果

| 指标 | 方形 | 原生 4:5 |
| --- | --- | --- |
| 冻结 cohort 中的独立 SKU 数 | 待测 | 待测 |
| 不同原始商品图 SHA-256 数（单列，不代表商品类别独立性） | 待测 | 待测 |
| 商品类别数 / product_family_id 数 / 图片与包装条件分布 | 同一 cohort，待测 | 同一 cohort，待测 |
| 应有输出数 / 实际生成输出数 | 待测 | 待测 |
| 完成全部适用字段人工审核数 / 应审核数 | 待测 | 待测 |
| 人工 overall_accept PASS / FAIL / UNVERIFIED 数 | 待测 | 待测 |
| 人工 overall 通过率（PASS 数 / 应审核输出数） | 待测 | 待测 |
| 人工 overall 与字段派生状态不一致数 | 待测 | 待测 |
| OCR 成功 / 失败 / 未运行 / 弃判数 | 待测 | 待测 |
| OCR 覆盖率（成功执行数 / 应执行 OCR 输出数） | 待测 | 待测 |
| 总耗时、单 SKU 中位耗时 | 待测 | 待测 |
| 收费模型调用次数 / 实际付费成本 | 0 / 0（基线） | 0 / 0（基线） |

### B. OCR 字段级评测

对每个可由 OCR 预测的字段分别填写结果。价格和原价必须把**文字识别与专属坐标检查同时纳入**；任一项 FAIL 即预测 FAIL，只有两项都 PASS 才预测 PASS，其余为 UNVERIFIED/弃判。

| 字段 | 确定人工标签数 | TP | FP | FN | TN | 弃判数 | Precision | Recall | F1 | Coverage |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| title | 待测 | 待测 | 待测 | 待测 | 待测 | 待测 | 待测 | 待测 | 待测 | 待测 |
| subtitle（适用时） | 待测 | 待测 | 待测 | 待测 | 待测 | 待测 | 待测 | 待测 | 待测 | 待测 |
| price（含坐标） | 待测 | 待测 | 待测 | 待测 | 待测 | 待测 | 待测 | 待测 | 待测 | 待测 |
| original_price（含坐标，适用时） | 待测 | 待测 | 待测 | 待测 | 待测 | 待测 | 待测 | 待测 | 待测 | 待测 |
| cta | 待测 | 待测 | 待测 | 待测 | 待测 | 待测 | 待测 | 待测 | 待测 | 待测 |

### C. 只能人工判断的视觉字段

| 字段 | PASS/correct | FAIL/defective | UNVERIFIED/unsure | 主要缺陷类型 |
| --- | --- | --- | --- | --- |
| sku_integrity | 待测 | 待测 | 待测 | 待测 |
| text_legibility | 待测 | 待测 | 待测 | 待测 |
| composition | 待测 | 待测 | 待测 | 待测 |

### D. 人工价区位置诊断

| 字段 | PASS | FAIL | UNVERIFIED | 主要缺陷类型 |
| --- | --- | --- | --- | --- |
| price_position | 待测 | 待测 | 待测 | 待测 |
| original_price_position（适用时） | 待测 | 待测 | 待测 | 待测 |

所有比率必须展示分子与分母；人工 overall 通过率的分母是全部应审核输出，包括缺失输出，不是已审核或 OCR 成功的子集。OCR 字段指标只在确定人工标签上计算，同时报告弃判和覆盖率；视觉字段没有自动预测，不计算 OCR Precision/Recall/F1。合成测试和 Linux 单图 OCR 不可并入真实 SKU 质量分数。基线默认使用本地免费背景，不调用收费模型；如之后比较付费生图，必须单独申请、估算费用并另列结果。

## 5. 停止条件与缺陷优先级

- **立即停止**：发现未授权素材/客户隐私可能被提交、实际收费调用未获授权、批次 ID/哈希无法对应、导入账本不完整却被接受。
- **阻塞 M6 验收**：真实 SKU 少于 20、任一尺寸缺少可追溯版本、人工标注不完整、OCR 执行与未执行混淆、报告分母与冻结批次不一致。
- P0：商品内容被篡改、价格错误、关键文案错误；P1：文字重叠/裁切、构图不可用；P2：非关键视觉风格问题。

本协议只准备评测，不承诺质量阈值已达到。质量门槛应在正式打分前由产品负责人预先确定并记录，不可看完结果再调整。
