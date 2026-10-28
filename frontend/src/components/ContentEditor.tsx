export function ContentEditor() {
    return (
        <div id="editor-content" role="tabpanel" aria-labelledby="editor-tab-content" tabIndex={0}>
            <h3>编辑当前版本</h3>
            <form id="editForm">
                <p className="tip">
                    可修改文字、价格及方形版式与商品位置，保留原商品与背景。每次保存会生成新的不可变历史版本。
                </p>
                <div className="row">
                    <label htmlFor="editTitle">主标题</label>
                    <input id="editTitle" required maxLength={35} />
                </div>
                <div className="row">
                    <label htmlFor="editSubtitle">副标题（可清空）</label>
                    <input id="editSubtitle" maxLength={45} />
                </div>
                <div className="split">
                    <div className="row">
                        <label htmlFor="editPrice">活动价</label>
                        <input id="editPrice" type="number" step="0.01" min="0.01" required />
                    </div>
                    <div className="row">
                        <label htmlFor="editOriginal">原价（可清空）</label>
                        <input id="editOriginal" type="number" step="0.01" min="0.01" />
                    </div>
                </div>
                <div className="row">
                    <label htmlFor="editCta">CTA 按钮文案</label>
                    <input id="editCta" required maxLength={12} />
                </div>

                <div className="row">
                    <label htmlFor="editLayout">方形版式</label>
                    <select id="editLayout">
                        <option value="classic">经典分栏</option>
                        <option value="hero">商品主体优先</option>
                    </select>
                </div>
                <div className="split">
                    <div className="row">
                        <label htmlFor="editProductAlign">商品垂直位置</label>
                        <select id="editProductAlign">
                            <option value="top">靠上</option>
                            <option value="center">居中</option>
                            <option value="bottom">靠下</option>
                        </select>
                    </div>
                    <div className="row">
                        <label htmlFor="editProductSourceMode">商品素材</label>
                        <select id="editProductSourceMode">
                            <option value="original">原图</option>
                            <option value="cutout">已保存抠图</option>
                        </select>
                    </div>
                    <div className="row">
                        <label htmlFor="editProductScale">商品大小</label>
                        <select id="editProductScale">
                            <option value="full">标准</option>
                            <option value="compact">紧凑</option>
                        </select>
                    </div>
                </div>
                <p className="tip">
                    变更后点击保存会立即渲染新的方图版本，旧版本、素材和已保存报告不被覆盖；新版本必须重新验收。
                </p>
                <details className="disclosure" id="edit-layer-editor">
                    <summary>高级：独立图层位置（受控像素偏移）</summary>
                    <p className="tip">
                        分别调整标题组、价格组、CTA 和 Logo 的位置；0
                        是默认值。为防止越界仅允许有限偏移，商品位置使用上方的独立控件。修改后请重新做
                        OCR 和人工审核。
                    </p>
                    <div className="row">
                        <label htmlFor="edit-layer-title_dx">标题水平（-20～20 px）</label>
                        <input
                            id="edit-layer-title_dx"
                            type="number"
                            min="-20"
                            max="20"
                            step="1"
                            defaultValue="0"
                            required
                        />
                    </div>
                    <div className="row">
                        <label htmlFor="edit-layer-title_dy">标题垂直（-20～20 px）</label>
                        <input
                            id="edit-layer-title_dy"
                            type="number"
                            min="-20"
                            max="20"
                            step="1"
                            defaultValue="0"
                            required
                        />
                    </div>
                    <div className="row">
                        <label htmlFor="edit-layer-price_dx">价格水平（-12～12 px）</label>
                        <input
                            id="edit-layer-price_dx"
                            type="number"
                            min="-12"
                            max="12"
                            step="1"
                            defaultValue="0"
                            required
                        />
                    </div>
                    <div className="row">
                        <label htmlFor="edit-layer-price_dy">价格垂直（-8～8 px）</label>
                        <input
                            id="edit-layer-price_dy"
                            type="number"
                            min="-8"
                            max="8"
                            step="1"
                            defaultValue="0"
                            required
                        />
                    </div>
                    <div className="row">
                        <label htmlFor="edit-layer-cta_dx">CTA 水平（-16～16 px）</label>
                        <input
                            id="edit-layer-cta_dx"
                            type="number"
                            min="-16"
                            max="16"
                            step="1"
                            defaultValue="0"
                            required
                        />
                    </div>
                    <div className="row">
                        <label htmlFor="edit-layer-cta_dy">CTA 垂直（-12～12 px）</label>
                        <input
                            id="edit-layer-cta_dy"
                            type="number"
                            min="-12"
                            max="12"
                            step="1"
                            defaultValue="0"
                            required
                        />
                    </div>
                    <div className="row">
                        <label htmlFor="edit-layer-logo_dx">Logo 水平（-16～16 px）</label>
                        <input
                            id="edit-layer-logo_dx"
                            type="number"
                            min="-16"
                            max="16"
                            step="1"
                            defaultValue="0"
                            required
                        />
                    </div>
                    <div className="row">
                        <label htmlFor="edit-layer-logo_dy">Logo 垂直（-16～16 px）</label>
                        <input
                            id="edit-layer-logo_dy"
                            type="number"
                            min="-16"
                            max="16"
                            step="1"
                            defaultValue="0"
                            required
                        />
                    </div>
                    <div className="actions">
                        <button id="checkEditLayout" type="button" className="secondary">
                            检测图层碰撞
                        </button>
                        <button id="fixEditLayout" type="button" className="secondary" disabled>
                            应用安全偏移建议
                        </button>
                    </div>
                    <p id="editLayoutStatus" className="tip" aria-live="polite">
                        检测当前方形版式的文本、价格与 CTA 交叠。建议需主动应用，再保存版本。上传
                        Logo 的实际尺寸会在最终生成时再次验证。
                    </p>
                </details>
                <div className="actions">
                    <button id="saveEdit" type="submit">
                        保存新版本
                    </button>
                    <button id="reloadPoster" className="secondary" type="button">
                        加载最新版本
                    </button>
                    <a id="download" href="#" download="poster.png">
                        导出 PNG
                    </a>
                </div>
            </form>
            <div className="tip" id="editStatus"></div>
            <div className="tip" id="history"></div>
            <details className="disclosure" id="versionGallery">
                <summary>查看历史版本</summary>
                <div className="row">
                    <label htmlFor="historyVersionSelect">
                        历史版本（只读预览，不切换当前编辑版本）
                    </label>
                    <div className="split">
                        <select
                            id="historyVersionSelect"
                            aria-label="选择已发布的历史版本"
                        ></select>
                        <button id="viewHistoryVersion" className="secondary" type="button">
                            查看版本
                        </button>
                    </div>
                    <img
                        id="historyVersionPreview"
                        className="preview hidden"
                        alt="历史版本只读预览"
                        loading="lazy"
                    />
                    <p id="historyVersionStatus" className="tip" aria-live="polite">
                        选择版本后可查看当时的文案和图片。
                    </p>
                    <a
                        id="historyVersionDownload"
                        className="hidden"
                        href="#"
                        download="poster-history.png"
                    >
                        下载此历史版本 PNG
                    </a>
                </div>
            </details>
        </div>
    );
}
