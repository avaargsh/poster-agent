export function ExportPanel() {
    return (
        <div
            id="editor-export"
            role="tabpanel"
            aria-labelledby="editor-tab-export"
            tabIndex={0}
            hidden
        >
            <section className="mt" id="channelFormats">
                <h3>尺寸与导出</h3>
                <p className="tip">
                    4:5
                    可以选择从原商品、Logo、背景和当前规格重新排版；原来的安全缩放导出仍可选。两种方式都不调用
                    Qwen，也不会自动通过验收。
                </p>
                <div className="row">
                    <label>选择导出规格</label>
                    <select id="channelPreset">
                        <option value="feed_4x5_native">
                            4:5 原生排版 · 左侧文案 / 右侧商品 · 1080×1350
                        </option>
                        <option value="feed_4x5_native_product_focus">
                            4:5 商品主体优先 · 商品上方 / 促销下方 · 1080×1350
                        </option>
                        <option value="feed_4x5">信息流竖版 · 1080×1350（4:5）</option>
                        <option value="story_9x16">故事 / 竖屏 · 1080×1920（9:16）</option>
                        <option value="landscape_16x9">横向展示 · 1200×675（16:9）</option>
                    </select>
                </div>
                <img
                    id="channelPreview"
                    className="preview"
                    alt="渠道海报预览"
                    loading="lazy"
                    style={{ maxHeight: '650px' }}
                />
                <p id="channelStatus" className="tip">
                    请先生成海报，再选择尺寸。
                </p>
                <div className="actions">
                    <a id="channelDownload" href="#" download="poster-channel.png">
                        导出该尺寸 PNG
                    </a>
                </div>
                <p className="tip">
                    原生 4:5 使用独立文字、SKU 与价格布局；其它规格延用原图完整安全缩放。方形版 OCR
                    验收结果不适用于任一派生图。
                </p>
                <div className="row mt" id="nativeTemplateCompare">
                    <div className="actions">
                        <button type="button" id="compareNativeLayouts" className="secondary">
                            并排对比两种 4:5 模板
                        </button>
                        <span className="tip" id="nativeCompareStatus" aria-live="polite">
                            点击后按需生成两个本地排版预览，不调用 AI。
                        </span>
                    </div>
                    <div id="nativeCompareGrid" className="template-compare-grid hidden">
                        <div className="template-compare-card">
                            <h3>经典分栏 · 左文案 / 右商品</h3>
                            <img
                                id="nativeCompareSplit"
                                className="preview"
                                alt="经典分栏 4:5 原生模板预览"
                                loading="eager"
                            />
                            <button
                                id="chooseNativeSplit"
                                type="button"
                                className="secondary"
                                disabled
                            >
                                选择经典分栏并查看导出
                            </button>
                        </div>
                        <div className="template-compare-card">
                            <h3>商品主体优先 · 上商品 / 下促销</h3>
                            <img
                                id="nativeCompareFocus"
                                className="preview"
                                alt="商品主体优先 4:5 原生模板预览"
                                loading="eager"
                            />
                            <button
                                id="chooseNativeFocus"
                                type="button"
                                className="secondary"
                                disabled
                            >
                                选择商品主体优先并查看导出
                            </button>
                        </div>
                    </div>
                    <p className="tip">
                        对比使用同一张已保存的商品和同一个海报版本，不修改原文件；两种模板都需要各自独立验收。
                    </p>
                </div>

                <details className="disclosure">
                    <summary>检查与审核这张 4:5 海报</summary>
                    <div className="row">
                        <label>原生 4:5 单独验收</label>
                        <select id="nativeOcrMode">
                            <option value="none">规则检查（不调用 OCR）</option>
                            <option value="paddle">真实 PaddleOCR（本地需安装）</option>
                        </select>
                    </div>
                    <div className="actions">
                        <button id="checkNative" className="secondary" type="button" disabled>
                            检查当前 4:5 导出
                        </button>
                        <span id="nativeGateStatus" className="tip">
                            选择原生 4:5 并生成海报后可检查。
                        </span>
                    </div>
                    <pre
                        id="nativeGateResult"
                        style={{
                            whiteSpace: 'pre-wrap',
                            overflowWrap: 'anywhere',
                            background: '#f1f4f9',
                            padding: '12px',
                            borderRadius: '9px',
                            fontSize: '12px',
                            maxHeight: '240px',
                            overflow: 'auto',
                        }}
                    >
                        未检查该导出版本
                    </pre>
                    <p className="tip">
                        每种 1080×1350
                        原生版式均使用独立报告，不继承方形版或其他版式的质量结论。即使文字 OCR
                        PASS，商品保真、品牌与投放规范也必须人工检查。
                    </p>
                    <div id="nativeHumanPanel">
                        <h3>4:5 原生海报人工验收</h3>
                        <p className="tip">
                            请对照上面的原生导出预览检查商品是否完整、文字是否可读、构图与具体文案。每条标签绑定当前
                            4:5 PNG、商品与背景 SHA-256。此审核与下方方形版标注完全分开。
                        </p>
                        <div className="row">
                            <label>4:5 审核员代号</label>
                            <input
                                id="nativeReviewer"
                                defaultValue="local-operator"
                                maxLength={40}
                            />
                        </div>
                        <div className="review-draft-tools">
                            <p id="nativeDraftStatus" className="tip" role="status">
                                填写过程会保存为草稿，提交审核后才进入正式记录。
                            </p>
                            <div className="actions">
                                <button
                                    id="saveNativeDraft"
                                    type="button"
                                    className="secondary"
                                    disabled
                                >
                                    保存进度
                                </button>
                                <button
                                    id="reloadNativeDraft"
                                    type="button"
                                    className="secondary"
                                    disabled
                                >
                                    载入已保存进度
                                </button>
                            </div>
                        </div>
                        <div id="nativeReviewFields"></div>
                        <div className="actions">
                            <button
                                type="button"
                                id="saveNativeReview"
                                className="secondary"
                                disabled
                            >
                                提交 4:5 审核
                            </button>
                            <a
                                id="exportNativeReview"
                                href="#"
                                download="poster-native-4x5-labels.csv"
                            >
                                导出该审核员 4:5 CSV
                            </a>
                        </div>
                        <p className="tip" id="nativeReviewStatus">
                            生成原生 4:5 海报后可审核。
                        </p>
                    </div>
                </details>
            </section>
        </div>
    );
}
