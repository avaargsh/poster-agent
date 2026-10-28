export function BatchWorkspace() {
    return (
        <section
            className="workspace-view"
            id="view-batch"
            role="tabpanel"
            aria-labelledby="nav-batch"
            tabIndex={0}
            hidden
        >
            <div className="workspace-heading">
                <div>
                    <h2>批量处理</h2>
                    <p className="tip">从一份商品清单开始，统一生成，再逐张审核。</p>
                </div>
                <span className="quiet-label">免费本地背景 · 支持中断续跑</span>
            </div>
            <div className="batch-grid">
                <section className="panel">
                    <section id="batchImportPanel">
                        <h3>导入商品素材</h3>
                        <p className="tip">
                            每批 1–100 个商品。先检查清单，再开始生成；不调用 Qwen 生图或 OCR。
                        </p>
                        <ol className="workflow-steps" id="batchSteps" aria-label="批量处理步骤">
                            <li data-batch-step="0" aria-current="step">
                                <span>1</span>准备素材
                            </li>
                            <li data-batch-step="1">
                                <span>2</span>检查清单
                            </li>
                            <li data-batch-step="2">
                                <span>3</span>生成海报
                            </li>
                            <li data-batch-step="3">
                                <span>4</span>审核与导出
                            </li>
                        </ol>
                        <div className="actions template-actions">
                            <a
                                id="downloadBatchTemplate"
                                download="poster-products-template.csv"
                                href="data:text/csv;charset=utf-8,sku_id%2Cproduct_image%2Ctitle%2Cprice%2Coriginal_price%2Csubtitle%2Ccta%2Cstyle%2Clogo_image%0A"
                            >
                                下载 CSV 模板
                            </a>
                            <span className="tip">填写商品编号、图片文件名、标题和价格。</span>
                        </div>
                        <div className="row">
                            <label htmlFor="batchManifest">SKU 清单 CSV（UTF-8）</label>
                            <input id="batchManifest" type="file" accept=".csv,text/csv" />
                        </div>
                        <div className="row">
                            <label htmlFor="campaignPreset">活动文案预设（批量补全空白字段）</label>
                            <select id="campaignPreset" disabled>
                                <option value="">正在读取本地预设…</option>
                            </select>
                        </div>
                        <div className="actions">
                            <button
                                id="applyCampaignPreset"
                                type="button"
                                className="secondary"
                                disabled
                            >
                                下载补全后的 CSV
                            </button>
                        </div>
                        <p
                            id="campaignPresetStatus"
                            className="tip"
                            role="status"
                            aria-live="polite"
                        >
                            预设只填补空白的副标题、CTA
                            和风格，不会覆盖价格、主标题或已有文案。下载后请重新选择新 CSV
                            并进行全批预检；不会自动生成海报。
                        </p>
                        <div className="row">
                            <label htmlFor="batchUseSavedLayout">
                                复用当前已保存海报的方形版式
                            </label>
                            <input id="batchUseSavedLayout" type="checkbox" />
                        </div>
                        <div className="row">
                            <label htmlFor="batchTemplateVersion">模板来源的历史版本</label>
                            <select id="batchTemplateVersion" disabled>
                                <option value="">先生成或打开一张已保存海报</option>
                            </select>
                        </div>
                        <p id="batchTemplateStatus" className="tip" role="status">
                            启用后，预检会复制所选历史版本的版式、商品位置、图层偏移到全部
                            SKU；不会复制商品图、标题、价格、背景、审核结论。预检完成后会冻结成新的批次清单。
                        </p>
                        <div className="row">
                            <label htmlFor="batchImages">
                                CSV 引用的所有图片（PNG / JPEG / WEBP；每张不超过 10 MiB）
                            </label>
                            <input
                                id="batchImages"
                                type="file"
                                accept="image/png,image/jpeg,image/webp"
                                multiple
                            />
                        </div>
                        <details className="disclosure">
                            <summary>清单怎么填写？</summary>
                            <p className="tip">
                                必填列：sku_id、product_image、title、price。可选：original_price、subtitle、cta、style、logo_image、layout_id、product_align、product_scale、layer_layout（受控
                                JSON）。网页批量导入要求 product_image / logo_image
                                使用文件名，不带目录；每批 1–100 个
                                SKU。勾选复用模板时会用当前选中海报版本的版式字段替换 CSV
                                中的对应字段，标题、价格与图片路径不变。图片只保存在本机。
                            </p>
                        </details>
                        <p id="batchFileSummary" className="tip">
                            尚未选择文件。
                        </p>
                        <div className="actions batch-start-actions">
                            <button id="preflightBatch" type="button">
                                上传并预检
                            </button>
                            <button id="applyBatch" type="button" disabled>
                                生成海报（本地背景）
                            </button>
                        </div>
                        <p id="batchNextAction" className="next-action" role="status">
                            先选择 CSV 和图片，再点击「上传并预检」。
                        </p>
                        <section id="batchResults" className="batch-results" hidden>
                            <h3>生成完成，接下来检查海报</h3>
                            <p className="tip">
                                可逐张审核，也可下载含方形和 4:5
                                图片的审核包。生成完成不代表审核通过。
                            </p>
                            <div className="actions">
                                <button
                                    id="reviewCurrentBatch"
                                    type="button"
                                    className="secondary"
                                    disabled
                                >
                                    审核当前批次
                                </button>
                                <button
                                    id="prepareM6Package"
                                    type="button"
                                    className="secondary"
                                    disabled
                                >
                                    准备离线审核包
                                </button>
                                <button
                                    id="downloadM6Package"
                                    type="button"
                                    className="secondary"
                                    disabled
                                >
                                    下载审核包 ZIP
                                </button>
                            </div>
                            <p id="m6PackageStatus" className="tip" aria-live="polite">
                                准备后可下载图片与待填审核表。
                            </p>
                            <div className="actions">
                                <button
                                    id="openM6CutoutReview"
                                    type="button"
                                    className="secondary"
                                    disabled
                                >
                                    逐 SKU 检查商品抠图
                                </button>
                            </div>
                            <section
                                id="m6CutoutReviewPanel"
                                className="mt"
                                hidden
                                aria-label="M6 透明抠图人工质量审核"
                            >
                                <h4>商品抠图人工复核</h4>
                                <p className="tip">
                                    左侧原图、右侧透明候选。在本地逐项检查主体轮廓、边缘与品牌标识。操作员评价会保留历史
                                    CSV；来源哈希和原图保持冻结。
                                </p>
                                <div className="row">
                                    <label htmlFor="m6CutoutSku">选择 SKU</label>
                                    <select id="m6CutoutSku"></select>
                                </div>
                                <p
                                    id="m6CutoutStatus"
                                    className="tip"
                                    role="status"
                                    aria-live="polite"
                                ></p>
                                <img
                                    id="m6CutoutComparison"
                                    style={{
                                        width: '100%',
                                        maxWidth: '820px',
                                        borderRadius: '10px',
                                    }}
                                    alt="左侧商品原图、右侧棋盘背景透明抠图，供人工审核"
                                />
                                <p id="m6CutoutMachineRisks" className="tip"></p>
                                <div className="row">
                                    <label htmlFor="m6CutoutSubject">商品主体完整性</label>
                                    <select id="m6CutoutSubject">
                                        <option value="">待核对</option>
                                        <option value="PASS">PASS</option>
                                        <option value="FAIL">FAIL</option>
                                    </select>
                                </div>
                                <div className="row">
                                    <label htmlFor="m6CutoutEdge">抠图边缘与残影</label>
                                    <select id="m6CutoutEdge">
                                        <option value="">待核对</option>
                                        <option value="PASS">PASS</option>
                                        <option value="FAIL">FAIL</option>
                                    </select>
                                </div>
                                <div className="row">
                                    <label htmlFor="m6CutoutBrand">包装与品牌标识保真</label>
                                    <select id="m6CutoutBrand">
                                        <option value="">待核对</option>
                                        <option value="PASS">PASS</option>
                                        <option value="FAIL">FAIL</option>
                                    </select>
                                </div>
                                <div className="row">
                                    <label htmlFor="m6CutoutDecision">操作员总评</label>
                                    <select id="m6CutoutDecision">
                                        <option value="">待核对</option>
                                        <option value="PASS">PASS</option>
                                        <option value="FAIL">FAIL</option>
                                    </select>
                                </div>
                                <div className="row">
                                    <label htmlFor="m6CutoutReviewer">审核员标识</label>
                                    <input
                                        id="m6CutoutReviewer"
                                        maxLength={80}
                                        placeholder="至少 2 个字符"
                                    />
                                </div>
                                <div className="row">
                                    <label htmlFor="m6CutoutNotes">审核意见（FAIL 必填）</label>
                                    <textarea
                                        id="m6CutoutNotes"
                                        maxLength={500}
                                        rows={2}
                                        defaultValue=""
                                    ></textarea>
                                </div>
                                <div className="actions">
                                    <button id="m6CutoutSave" type="button" disabled>
                                        保存这条人工复核
                                    </button>
                                    <button id="m6CutoutReload" type="button" className="secondary">
                                        重新加载最新审核表
                                    </button>
                                </div>
                                <p className="tip">
                                    保存使用审核 CSV SHA-256 并发校验。操作员 PASS
                                    是人工声明；模型真实性、授权和正式商品质量验收需要各自独立验证。
                                </p>
                            </section>
                            <details className="disclosure">
                                <summary>OCR 证据与标注工具</summary>
                                <p id="m6OCRCaptureStatus" className="tip" aria-live="polite">
                                    OCR 尚未启动。先在本机配置 POSTER_M6_OCR_DET_MODEL_DIR 和
                                    POSTER_M6_OCR_REC_MODEL_DIR，并预先安装 PaddleOCR
                                    3.x、PaddlePaddle 与对应离线模型。
                                </p>
                                <div className="actions">
                                    <button
                                        id="captureM6OCR"
                                        type="button"
                                        className="secondary"
                                        disabled
                                    >
                                        显式运行本机 PaddleOCR（耗费 CPU/GPU）
                                    </button>
                                </div>
                                <p className="tip">
                                    模型目录由本机环境变量指定；网页不会接受 Shell
                                    命令或任意文件路径。采集证据不可覆盖；执行和标签均不构成真实质量验收。
                                </p>
                                <p id="m6OverlayStatus" className="tip" aria-live="polite">
                                    先准备审核包，再在本机采集 OCR 证据。
                                </p>
                                <div className="actions">
                                    <button
                                        id="prepareM6Overlay"
                                        type="button"
                                        className="secondary"
                                        disabled
                                    >
                                        准备 OCR 标注预览
                                    </button>
                                    <button
                                        id="downloadM6Overlay"
                                        type="button"
                                        className="secondary"
                                        disabled
                                    >
                                        下载 OCR 标注 ZIP
                                    </button>
                                    <button
                                        id="inspectM6OCR"
                                        type="button"
                                        className="secondary"
                                        disabled
                                    >
                                        查看逐 SKU OCR 核对
                                    </button>
                                </div>
                            </details>
                        </section>
                        <section
                            id="m6OcrReviewPanel"
                            className="mt"
                            hidden
                            aria-label="逐 SKU OCR 与人工标签核对"
                        >
                            <h3>OCR 文字与人工标签逐项核对</h3>
                            <p className="tip">
                                内容来自本地冻结的审核 CSV 和 OCR
                                证据；仅供人工检查，不能据此自动批准海报或证明真实模型执行。
                            </p>
                            <div className="row">
                                <label htmlFor="m6OcrSkuPicker">选择 SKU</label>
                                <select id="m6OcrSkuPicker" disabled></select>
                            </div>
                            <p id="m6OcrReviewStatus" className="tip" aria-live="polite"></p>
                            <div id="m6OcrReviewRows" aria-live="polite"></div>
                            <div className="actions">
                                <button
                                    id="m6ReviewSaveSku"
                                    type="button"
                                    className="secondary"
                                    disabled
                                >
                                    暂存当前 SKU 的人工审核
                                </button>
                                <button
                                    id="m6ReviewExportSidecar"
                                    type="button"
                                    className="secondary"
                                    disabled
                                >
                                    下载审核侧车 JSON
                                </button>
                                <button
                                    id="m6ReviewDownloadWorksheet"
                                    type="button"
                                    className="secondary"
                                    disabled
                                >
                                    下载人工评审 CSV 草稿
                                </button>
                                <label htmlFor="m6ReviewImportFile">
                                    恢复审核草稿（本地 JSON）
                                </label>
                                <input
                                    id="m6ReviewImportFile"
                                    type="file"
                                    accept=".json,application/json"
                                />
                                <button
                                    id="m6ReviewImportSidecar"
                                    type="button"
                                    className="secondary"
                                    disabled
                                >
                                    校验并恢复审核草稿
                                </button>
                            </div>
                            <p id="m6ReviewDraftProgress" className="tip" aria-live="polite">
                                尚无已保存的人工审核草稿。
                            </p>
                            <p className="tip">
                                CSV 只是已验证来源身份的操作员草稿，不覆盖冻结 OCR
                                原表；审核时间为空，需人工补充、复核授权，并重新建立匹配的 OCR
                                证据后才能进入独立 M6 验收。
                            </p>
                            <section className="mt" aria-label="M6 SKU 待办清单">
                                <h4>SKU 审核待办</h4>
                                <p className="tip">
                                    根据本页面人工审核草稿和已核对摘要的 OCR
                                    记录生成，仅用于查找漏审与操作员标记的 FAIL；不证明 OCR
                                    真正执行，也不代表质量验收。
                                </p>
                                <div className="row">
                                    <label htmlFor="m6ReviewQueueFilter">筛选 SKU</label>
                                    <select id="m6ReviewQueueFilter">
                                        <option value="actionable">待处理（漏审或 FAIL）</option>
                                        <option value="missing">缺审核版式</option>
                                        <option value="failed">人工标记 FAIL</option>
                                        <option value="all">全部 SKU</option>
                                    </select>
                                </div>
                                <p id="m6ReviewQueueSummary" className="tip" aria-live="polite">
                                    加载 OCR 证据后可查看 SKU 待办。
                                </p>
                                <div
                                    id="m6ReviewQueue"
                                    className="execution-history"
                                    style={{ maxHeight: '380px', overflow: 'auto' }}
                                    role="list"
                                    aria-label="SKU 审核待办项"
                                ></div>
                            </section>
                            <div className="actions">
                                <button
                                    id="m6ReviewNextMissing"
                                    type="button"
                                    className="secondary"
                                    disabled
                                >
                                    下一待审 SKU
                                </button>
                                <button
                                    id="m6ReviewAssess"
                                    type="button"
                                    className="secondary"
                                    disabled
                                >
                                    核对草稿与冻结证据
                                </button>
                                <button
                                    id="m6ReviewDownloadAssessment"
                                    type="button"
                                    className="secondary"
                                    disabled
                                >
                                    下载核对报告 JSON
                                </button>
                            </div>
                            <p id="m6ReviewAssessmentStatus" className="tip" aria-live="polite">
                                核对结果只反映操作员声明及本地证据一致性，不是商品质量或发布批准。
                            </p>
                            <p id="m6ReviewSidecarStatus" className="tip" aria-live="polite">
                                暂存只在当前浏览器页面内有效。请显式下载后保存；不会覆盖冻结
                                CSV，不构成 M6 最终验收。
                            </p>
                        </section>
                        <p id="batchStatus" className="tip" aria-live="polite">
                            尚未选择批次。预检不会创建海报；只有点击生成后才开始处理。
                        </p>
                        <progress
                            id="batchProgress"
                            max="100"
                            value="0"
                            aria-label="批量生成进度"
                        ></progress>
                        <div id="batchPlanSummary" className="tip"></div>
                        <div className="row batch-search">
                            <label htmlFor="batchSearch">查找本批商品</label>
                            <input
                                id="batchSearch"
                                type="search"
                                placeholder="输入商品编号"
                                aria-controls="batchRows"
                            />
                        </div>
                        <p id="batchSearchEmpty" className="tip" hidden>
                            没有匹配的商品，请换一个编号。
                        </p>
                        <div id="batchRows" className="execution-history" aria-live="polite"></div>
                    </section>
                </section>
                <aside className="panel batch-history">
                    <div id="batchHistoryPanel">
                        <h3>最近的批次</h3>
                        <p className="tip">
                            选择旧批次后可查看原有进度、续跑未完成任务或进入人工审核；历史浏览不会自动删除原图或评测账本。
                        </p>
                        <div className="actions">
                            <label htmlFor="batchHistoryFilter">状态</label>
                            <select id="batchHistoryFilter" aria-label="筛选历史批次">
                                <option value="">全部</option>
                                <option value="COMPLETED">已完成</option>
                                <option value="FAILED">失败</option>
                                <option value="INTERRUPTED">已中断</option>
                                <option value="READY">待生成</option>
                                <option value="RUNNING">执行中</option>
                            </select>
                            <button id="refreshBatchHistory" className="secondary" type="button">
                                刷新历史
                            </button>
                            <button
                                id="batchHistoryPrev"
                                className="secondary"
                                type="button"
                                disabled
                            >
                                上一页
                            </button>
                            <button
                                id="batchHistoryNext"
                                className="secondary"
                                type="button"
                                disabled
                            >
                                下一页
                            </button>
                        </div>
                        <p id="batchHistoryInfo" className="tip" aria-live="polite">
                            尚未加载历史批次
                        </p>
                        <div
                            id="batchHistoryItems"
                            className="execution-history"
                            aria-live="polite"
                        ></div>
                    </div>
                </aside>
            </div>
        </section>
    );
}
