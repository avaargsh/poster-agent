export function ReviewPanel() {
    return (
        <div
            id="editor-check"
            role="tabpanel"
            aria-labelledby="editor-tab-check"
            tabIndex={0}
            hidden
        >
            <section className="mt">
                <h2>方形海报检查</h2>
                <p className="tip">
                    检测每个海报版本。规则检查与真实 OCR 分开执行；未运行 OCR 不会视为通过。
                </p>
                <div className="row">
                    <label>验收模式</label>
                    <select id="ocrMode">
                        <option value="none">规则检查（不运行 OCR）</option>
                        <option value="paddle">PaddleOCR 3.x 真实文字识别（需本机单独安装）</option>
                    </select>
                </div>
                <div className="actions">
                    <button className="secondary" type="button" id="runGate">
                        检查当前版本
                    </button>
                    <span className="tip" id="gateStatus">
                        尚未验收
                    </span>
                </div>
                <pre
                    id="gateResult"
                    style={{
                        whiteSpace: 'pre-wrap',
                        overflowWrap: 'anywhere',
                        background: '#f1f4f9',
                        padding: '12px',
                        borderRadius: '9px',
                        fontSize: '12px',
                        lineHeight: '1.7',
                        maxHeight: '330px',
                        overflow: 'auto',
                    }}
                >
                    暂无报告
                </pre>
                <div id="overlayPanel" className="hidden">
                    <p className="tip">
                        OCR 框和价格区域叠加图（仅供诊断）。蓝色小框来自报告中的 OCR
                        坐标；其他区域颜色代表位置检查状态。无坐标不代表通过。
                    </p>
                    <img
                        id="overlayPreview"
                        alt="OCR bounding boxes and expected price zones diagnostic"
                        style={{
                            width: '100%',
                            borderRadius: '12px',
                            marginTop: '8px',
                        }}
                    />
                    <div className="actions">
                        <a id="downloadOverlay" href="#" download="ocr-diagnostic.png">
                            导出诊断 PNG
                        </a>
                    </div>
                </div>
            </section>
            <section className="mt">
                <h2>方形海报人工审核</h2>
                <p className="tip">
                    请对照上方 PNG 亲眼检查每个字段。提交后绑定当前版本的
                    SHA-256；这是你自己提供的标签，并非模型验证。
                </p>
                <div className="row">
                    <label>审核员代号（2–40 位英文、数字、_、-）</label>
                    <input id="humanReviewer" defaultValue="local-operator" maxLength={40} />
                </div>
                <div className="review-draft-tools">
                    <p id="humanDraftStatus" className="tip" role="status">
                        填写过程会保存为草稿，提交审核后才进入正式记录。
                    </p>
                    <div className="actions">
                        <button id="saveHumanDraft" type="button" className="secondary" disabled>
                            保存进度
                        </button>
                        <button id="reloadHumanDraft" type="button" className="secondary" disabled>
                            载入已保存进度
                        </button>
                    </div>
                </div>
                <div id="humanFields">
                    <div className="row" data-human-field="title">
                        <label>主标题</label>
                        <select data-judgment="">
                            <option value="">请选择</option>
                            <option value="correct">实际图片正确</option>
                            <option value="defective">实际图片有错误</option>
                            <option value="unsure">无法判断</option>
                        </select>
                        <input
                            data-reason=""
                            placeholder="依据 / 错误说明（可选）"
                            maxLength={300}
                        />
                    </div>
                    <div className="row" data-human-field="price">
                        <label>活动价</label>
                        <select data-judgment="">
                            <option value="">请选择</option>
                            <option value="correct">实际图片正确</option>
                            <option value="defective">实际图片有错误</option>
                            <option value="unsure">无法判断</option>
                        </select>
                        <input
                            data-reason=""
                            placeholder="依据 / 错误说明（可选）"
                            maxLength={300}
                        />
                    </div>
                    <div className="row" data-human-field="cta">
                        <label>CTA 按钮</label>
                        <select data-judgment="">
                            <option value="">请选择</option>
                            <option value="correct">实际图片正确</option>
                            <option value="defective">实际图片有错误</option>
                            <option value="unsure">无法判断</option>
                        </select>
                        <input
                            data-reason=""
                            placeholder="依据 / 错误说明（可选）"
                            maxLength={300}
                        />
                    </div>
                    <div className="row" data-human-field="subtitle">
                        <label>副标题</label>
                        <select data-judgment="">
                            <option value="">请选择</option>
                            <option value="correct">实际图片正确</option>
                            <option value="defective">实际图片有错误</option>
                            <option value="unsure">无法判断</option>
                        </select>
                        <input
                            data-reason=""
                            placeholder="依据 / 错误说明（可选）"
                            maxLength={300}
                        />
                    </div>
                    <div className="row" data-human-field="original_price">
                        <label>原价</label>
                        <select data-judgment="">
                            <option value="">请选择</option>
                            <option value="correct">实际图片正确</option>
                            <option value="defective">实际图片有错误</option>
                            <option value="unsure">无法判断</option>
                        </select>
                        <input
                            data-reason=""
                            placeholder="依据 / 错误说明（可选）"
                            maxLength={300}
                        />
                    </div>
                </div>
                <div className="actions">
                    <button type="button" id="saveHuman" className="secondary" disabled>
                        提交方形审核
                    </button>
                    <a id="exportHuman" href="#" download="poster-human-labels.csv">
                        导出该审核员 CSV
                    </a>
                </div>
                <p id="humanStatus" className="tip">
                    生成海报后，先核对 PNG 再标注。
                </p>
            </section>
        </div>
    );
}
