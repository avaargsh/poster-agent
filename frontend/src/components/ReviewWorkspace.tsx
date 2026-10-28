export function ReviewWorkspace() {
    return (
        <section
            className="workspace-view"
            id="view-review"
            role="tabpanel"
            aria-labelledby="nav-review"
            tabIndex={0}
            hidden
        >
            <div className="workspace-heading">
                <div>
                    <h2>审核中心</h2>
                    <p className="tip">
                        按版式检查商品、文案和价格。未验证的结果会保留待审核状态。
                    </p>
                </div>
            </div>
            <section className="panel">
                <section id="trialQueue" style={{ marginBottom: '24px' }}>
                    <h3>待审核的海报</h3>
                    <p className="tip">
                        复用当前人工审核员代号（下方“4:5
                        原生海报人工验收”中可修改）。按版式独立列出最新版本的人工审核与 OCR
                        状态。READY 表示当前选中版式的人工审核与 OCR 证据齐备，可在 M5e 中指定相同的
                        --variant 单独评分；两种版式的标签与报告不能混用，也都不代表可投放。
                    </p>
                    <div className="actions">
                        <label htmlFor="reviewQueueVariant">验收版式</label>
                        <select
                            id="reviewQueueVariant"
                            aria-label="选择当前验收队列对应的 4:5 原生版式"
                        >
                            <option value="feed_4x5_native">经典分栏 4:5</option>
                            <option value="feed_4x5_native_product_focus">商品主体优先 4:5</option>
                        </select>
                        <button id="loadReviewQueue" className="secondary" type="button">
                            刷新本地验收队列
                        </button>
                        <label htmlFor="batchReviewFile">仅看 M5g 批次</label>
                        <input
                            type="file"
                            id="batchReviewFile"
                            accept=".json,application/json"
                            aria-label="选择 M5g 的 batch_import_results.json"
                        />
                        <button id="clearBatchScope" className="secondary" type="button">
                            清除批次筛选
                        </button>
                        <span id="reviewQueueStatus" className="tip">
                            按需读取本地状态；不会自动执行 OCR 或模型。
                        </span>
                    </div>
                    <p id="batchReviewScopeStatus" className="tip">
                        当前查看全部本地海报。批次筛选文件仅在此浏览器中读取，不会上传到服务器。
                    </p>
                    <div
                        id="reviewQueueItems"
                        style={{ marginTop: '12px', maxHeight: '360px', overflow: 'auto' }}
                    ></div>
                </section>
                <details className="disclosure">
                    <summary>查看离线审核表的填写进度</summary>
                    <section id="m6ReviewProgress" style={{ marginBottom: '24px' }}>
                        <h2>M6 · 本地评测进度预览</h2>
                        <p className="tip">
                            选择由 scripts.prepare_m6_review 生成并由人工填写的两个
                            CSV。点击后只传给本机服务在内存中统计，不留存
                            CSV、不访问图片路径，也不会执行 OCR
                            或收费模型。此处“完成”只指人工表格填写状态，不能代替最终 M6 证据门禁。
                        </p>
                        <div className="row">
                            <label htmlFor="m6RegistryCsv">
                                M6 cohort registry CSV（授权与 SKU 独立性记录）
                            </label>
                            <input id="m6RegistryCsv" type="file" accept=".csv,text/csv" />
                        </div>
                        <div className="row">
                            <label htmlFor="m6ReviewsCsv">
                                M6 output reviews CSV（方形 + 4:5 人工审核记录）
                            </label>
                            <input id="m6ReviewsCsv" type="file" accept=".csv,text/csv" />
                        </div>
                        <div className="actions">
                            <button id="checkM6Progress" type="button" className="secondary">
                                查看本地评测进度
                            </button>
                        </div>
                        <p id="m6ProgressSummary" className="tip" aria-live="polite">
                            尚未加载 M6 CSV；不会自动判断 OCR 或图片质量。
                        </p>
                        <div
                            id="m6ProgressIssues"
                            className="execution-history"
                            aria-live="polite"
                        ></div>
                    </section>
                </details>
            </section>
        </section>
    );
}
