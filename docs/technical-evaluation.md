# 图片目录技术评测

本地生成与预览不要求授权登记表或审核人。当前单张生成 API、上传页面和 M5g 批量生成已经采用这一行为，没有 `license_id` 或 `reviewer` 的必填生成参数。

新增的目录入口会扫描 JPG、PNG、WebP，校验图片并保存原文件副本及 SHA-256，自动创建 CSV 清单与最小素材登记表。原文件保持原样，授权、商品独立性和人工视觉审核均保持 `UNVERIFIED`。相同图片不会因文件名不同而被声明为独立商品。

## 从 20 张图片开始

先进行只读检查：

```bash
uv run --locked python -m scripts.technical_evaluation \
  --images data/my_products \
  --price 199 \
  --output-dir data/eval_runs/technical_001
```

`--price` 是操作者指定的统一测试价格，标题使用“技术测试商品”及序号；它们不是模型识别出的真实商品信息，不应用于对外发布。需要真实商品名称和价格时，仍使用现有 M5g CSV 清单填写它们。本工具不会根据图片猜测价格。

确认后加 `--apply`，自动执行本地生成、M6 包准备及两种尺寸检查：

```bash
uv run --locked python -m scripts.technical_evaluation \
  --images data/my_products --price 199 \
  --output-dir data/eval_runs/technical_001 --apply
```

默认至少 20 张、最多 100 张图片。工程单元测试允许较小样本，但不会将其声明为 20 个真实独立 SKU。输出目录必须是新目录且与原图目录分开；批次使用独立的海报目录与执行数据库，不混入工作台已有记录。失败会保留 `INCOMPLETE` 报告与已产生的证据，不将部分成功当作完成；不要覆盖该目录重跑，M14/M5g 的细粒度恢复仍使用各自原有入口。

## 可选的真实模型检查

配置本地 u2netp 和 rembg 后，加 `--cutout`。生成的候选沿用 M14 的模型 SHA 固定、原图保存和逐行完成记录，然后交给 M5g。

PaddleOCR 与 PaddlePaddle 需要安装在当前应用的 Python 环境中，参照 [OCR 环境说明](m3d-ocr-runtime.md)。模型预下载后，加 `--ocr` 并提供本地目录：

```bash
uv run --no-sync python -m scripts.technical_evaluation \
  --images data/my_products --price 199 \
  --output-dir data/eval_runs/technical_models_001 --apply --cutout --ocr \
  --det-model-dir /absolute/path/to/detection-model \
  --rec-model-dir /absolute/path/to/recognition-model
```

未加 `--ocr` 时明确记录 `NOT_RUN`；执行成功后记录采集数量与 `CAPTURED_REVIEW_REQUIRED`，保留文字、置信度、坐标和模型摘要，不据此生成视觉审核 PASS 或 OCR 准确率。

## 报告与独立审核

- `inputs/manifest.csv`：符合现有 M5g schema 的生成清单。
- `inputs/sku_registry.csv`：最小素材登记。来源摘要已记录，授权、独立 SKU 与审核人未被自动确认。
- `inputs/source_snapshot.json`：本地原文件名与 SHA-256 的对应关系。
- `cutouts/`：启用抠图时的 M14 记录、候选及下游清单。
- `batch/batch_import_results.json`：真实生成的 M5g 账本。
- `m6_review_package/`：方形与 4:5 PNG、独立人工审核表、可选 OCR 观察。
- `technical_report.json`：生成完成数、预期输出分母、尺寸和摘要检查、OCR 执行状态。授权与视觉审核各自保持 `UNVERIFIED`，`quality_accepted=false`。

本地 M14 CLI 的 `--registry` 现在可省略，默认 `--purpose technical_evaluation`，仍需有效清单、至少 20 张图片和预装模型。若需要正式质量验收，显式指定 `--purpose quality_acceptance` 和授权登记表；历史严格模式的 Python 调用默认保持兼容。

技术运行结束后可以复制其登记表，独立补齐授权证据、商品独立性和真实审核人，再用严格的 `verify_cohort` 核验新的登记表与原图身份；无需重新运行图像模型。原始输入登记摘要保留在 M14 账本中，新核验报告记录 `verified_registry_sha256`。未填写或不匹配的记录会阻塞正式验收。

仓库当前没有公开发布接口。本工具不增加发布能力；现有 M6 最终门禁仍要求至少 20 个独立 SKU、授权、人工视觉标签与真实 OCR 证据。技术检查完成不代替这些条件。
