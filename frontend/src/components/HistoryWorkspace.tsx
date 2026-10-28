export function HistoryWorkspace() {
    return (
        <section
            className="workspace-view"
            id="view-history"
            role="tabpanel"
            aria-labelledby="nav-history"
            tabIndex={0}
            hidden
        >
            <div className="workspace-heading">
                <div>
                    <h2>执行记录</h2>
                    <p className="tip">找回已生成的海报，或核对中断的生成请求。</p>
                </div>
            </div>
            <section className="panel">
                <h3>海报生成记录</h3>
                <div className="actions" style={{ marginTop: '12px' }}>
                    <button id="checkExecution" type="button" className="secondary">
                        查询最近执行状态
                    </button>
                    <button id="refreshExecutionHistory" type="button" className="secondary">
                        刷新执行历史
                    </button>
                </div>
                <p id="executionStatus" className="tip">
                    生成请求会保存本地幂等键。网络断开时先查状态，不重复付费生图。
                </p>
                <div className="actions" style={{ marginTop: '12px' }}>
                    <label htmlFor="executionHistoryFilter">历史记录筛选</label>
                    <select id="executionHistoryFilter" aria-label="按执行状态筛选">
                        <option value="">全部状态</option>
                        <option value="COMPLETED">已完成</option>
                        <option value="UNKNOWN">结果未知</option>
                        <option value="RUNNING">执行中</option>
                    </select>
                    <button id="executionHistoryPrev" type="button" className="secondary" disabled>
                        上一页
                    </button>
                    <button id="executionHistoryNext" type="button" className="secondary" disabled>
                        下一页
                    </button>
                    <span id="executionHistoryRange" className="tip" aria-live="polite">
                        读取中…
                    </span>
                </div>
                <div id="executionHistory" className="execution-history" aria-live="polite">
                    读取最近执行记录…
                </div>
                <details className="disclosure">
                    <summary>命令行批次记录</summary>
                    <section id="cliBatchJournal" style={{ marginBottom: '24px' }}>
                        <h3>命令行批次账本状态（M5g）</h3>
                        <p className="tip">
                            只读默认目录 data/eval_runs/m5g_import/batch_import_results.json 中的
                            CLI 批次。它与上方网页批次分开统计；自定义 CLI
                            输出目录不会显示。此处不会生成海报、运行 OCR 或自动验收。
                        </p>
                        <div className="actions">
                            <button id="refreshCliBatchJournal" className="secondary" type="button">
                                刷新命令行账本
                            </button>
                            <span id="cliBatchJournalSummary" className="tip" aria-live="polite">
                                尚未读取
                            </span>
                        </div>
                        <div
                            id="cliBatchJournalRows"
                            className="execution-history"
                            aria-live="polite"
                        ></div>
                    </section>
                </details>
            </section>
        </section>
    );
}
