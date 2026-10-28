export function CreationForm() {
    return (
        <section className="panel creation-form">
            <h3>商品与文案</h3>
            <details className="disclosure brief-disclosure" id="briefPanel">
                <summary>用一句话填写需求（可选）</summary>
                <div className="row">
                    <label>
                        输入运营需求（如：主标题：双十一限时优惠，活动价199元，原价399元，风格：蓝色科技风）
                    </label>
                    <textarea
                        id="briefText"
                        rows={3}
                        placeholder="主标题：电动牙刷双十一，活动价199元，原价399元，风格：蓝色科技风"
                        defaultValue=""
                    ></textarea>
                </div>
                <div className="row">
                    <label>规格编译模式</label>
                    <select id="briefCompiler">
                        <option value="demo">本地规则演示（不是 AI，免费）</option>
                        <option value="qwen">Qwen LLM 结构化抽取（需要 API Key）</option>
                    </select>
                </div>
                <div className="actions">
                    <button className="secondary" id="compileBrief" type="button">
                        编译并保存审核任务
                    </button>
                    <span id="compileStatus" className="tip">
                        创建后需明确批准，才能继续生图
                    </span>
                </div>
                <div className="actions" style={{ marginTop: '10px' }}>
                    <button id="approveBrief" className="hidden" type="button">
                        确认规格
                    </button>
                    <button id="rejectBrief" className="secondary hidden" type="button">
                        拒绝草稿
                    </button>
                    <button id="clearBrief" className="secondary hidden" type="button">
                        清除任务 / 手动填写
                    </button>
                </div>
                <p id="runInfo" className="tip"></p>
                <p id="compileWarnings" className="tip"></p>
            </details>
            <form id="createForm">
                <input type="hidden" name="review_run_id" defaultValue="" />
                <section className="create-draft-tools" aria-label="创作草稿">
                    <p id="createDraftStatus" className="tip" role="status">
                        文案与版式会自动保存在此浏览器，图片需重新选择。
                    </p>
                    <div className="actions">
                        <button id="restoreCreateDraft" className="secondary" type="button" hidden>
                            恢复上次草稿
                        </button>
                        <button id="saveCreateDraft" className="secondary" type="button">
                            保存当前进度
                        </button>
                        <button id="discardCreateDraft" className="secondary" type="button" hidden>
                            删除已存草稿
                        </button>
                    </div>
                </section>
                <div className="row">
                    <label htmlFor="create-product">商品图片（推荐透明 PNG）*</label>
                    <input
                        type="file"
                        id="create-product"
                        name="product"
                        accept="image/png,image/jpeg,image/webp"
                        required
                    />
                </div>
                <p className="tip">PNG、JPG 或 WEBP，单张不超过 10 MB。</p>
                <div id="productSelection" className="asset-selection" hidden>
                    <img id="productThumb" alt="已选择的商品图" />
                    <span id="productFileName"></span>
                </div>
                <p id="productFileError" className="tip bad" role="alert"></p>
                <div className="actions">
                    <button id="cutoutPreviewButton" type="button" className="secondary">
                        本地抠图预览（需预装模型）
                    </button>
                    <button id="acceptCutout" type="button" className="secondary" disabled>
                        使用抠图结果生成
                    </button>
                    <button id="resetCutout" type="button" className="secondary" disabled>
                        保留原图
                    </button>
                </div>
                <input type="hidden" name="product_source_mode" defaultValue="original" />
                <div id="cutoutCompare" className="split" hidden>
                    <div className="row">
                        <label>原始商品图</label>
                        <img
                            id="cutoutOriginalPreview"
                            className="preview"
                            alt="抠图前的原始商品图"
                        />
                    </div>
                    <div className="row">
                        <label>待确认抠图结果</label>
                        <img
                            id="cutoutResultPreview"
                            className="preview"
                            alt="本地模型处理后的透明商品图"
                        />
                    </div>
                </div>
                <p id="cutoutStatus" className="tip" role="status">
                    抠图需要明确点击，不会自动执行。请对照检查边缘和主体完整性后决定是否采用。
                </p>
                <div className="row">
                    <label htmlFor="create-logo">品牌 Logo（可选）</label>
                    <input
                        type="file"
                        id="create-logo"
                        name="logo"
                        accept="image/png,image/jpeg,image/webp"
                    />
                </div>
                <div className="row">
                    <label htmlFor="create-title">主标题*</label>
                    <input
                        id="create-title"
                        name="title"
                        required
                        maxLength={35}
                        defaultValue="双十一限时优惠"
                    />
                </div>
                <div className="row">
                    <label htmlFor="create-subtitle">副标题</label>
                    <input
                        id="create-subtitle"
                        name="subtitle"
                        maxLength={45}
                        defaultValue="智能清洁 · 品质生活"
                    />
                </div>
                <div className="split">
                    <div className="row">
                        <label htmlFor="create-price">活动价*</label>
                        <input
                            id="create-price"
                            name="price"
                            type="number"
                            step="0.01"
                            min="0.01"
                            defaultValue="199"
                            required
                        />
                    </div>
                    <div className="row">
                        <label htmlFor="create-original_price">原价</label>
                        <input
                            id="create-original_price"
                            name="original_price"
                            type="number"
                            step="0.01"
                            defaultValue="399"
                        />
                    </div>
                </div>
                <div className="row">
                    <label htmlFor="create-cta">按钮文案</label>
                    <input id="create-cta" name="cta" maxLength={12} defaultValue="立即抢购" />
                </div>
                <div className="row">
                    <label htmlFor="createCampaignPreset">活动文案预设（仅本地文案与配色）</label>
                    <select id="createCampaignPreset" disabled>
                        <option value="">正在读取本地预设…</option>
                    </select>
                </div>
                <div className="actions">
                    <button
                        id="applySingleCampaignPreset"
                        type="button"
                        className="secondary"
                        disabled
                    >
                        套用副标题、CTA 与配色
                    </button>
                </div>
                <p id="createCampaignPresetStatus" className="tip" role="status">
                    只修改当前表单的三个字段，不修改标题和价格，也不会自动调用生图模型。使用已保存的
                    Brief 前，请先清除审核任务。
                </p>

                <div className="row">
                    <label htmlFor="create-layout_id">方形版式</label>
                    <select id="create-layout_id" name="layout_id">
                        <option value="classic">经典分栏 · 左文案右商品</option>
                        <option value="hero">商品主体优先 · 上商品下促销</option>
                    </select>
                </div>
                <div className="split">
                    <div className="row">
                        <label htmlFor="create-product_align">商品垂直位置</label>
                        <select
                            id="create-product_align"
                            name="product_align"
                            defaultValue="center"
                        >
                            <option value="top">靠上</option>
                            <option value="center">居中</option>
                            <option value="bottom">靠下</option>
                        </select>
                    </div>
                    <div className="row">
                        <label htmlFor="create-product_scale">商品大小</label>
                        <select id="create-product_scale" name="product_scale">
                            <option value="full">标准</option>
                            <option value="compact">紧凑（保留留白）</option>
                        </select>
                    </div>
                </div>
                <p className="tip">
                    版式仅改变方形 1024×1024
                    海报；商品像素等比缩放不裁切。其他比例仍用已有独立导出规则。
                </p>
                <details className="disclosure" id="create-layer-editor">
                    <summary>高级：独立图层位置（受控像素偏移）</summary>
                    <p className="tip">
                        分别调整标题组、价格组、CTA 和 Logo 的位置；0
                        是默认值。为防止越界仅允许有限偏移，商品位置使用上方的独立控件。修改后请重新做
                        OCR 和人工审核。
                    </p>
                    <div className="row">
                        <label htmlFor="create-layer-title_dx">标题水平（-20～20 px）</label>
                        <input
                            id="create-layer-title_dx"
                            type="number"
                            min="-20"
                            max="20"
                            step="1"
                            defaultValue="0"
                            required
                        />
                    </div>
                    <div className="row">
                        <label htmlFor="create-layer-title_dy">标题垂直（-20～20 px）</label>
                        <input
                            id="create-layer-title_dy"
                            type="number"
                            min="-20"
                            max="20"
                            step="1"
                            defaultValue="0"
                            required
                        />
                    </div>
                    <div className="row">
                        <label htmlFor="create-layer-price_dx">价格水平（-12～12 px）</label>
                        <input
                            id="create-layer-price_dx"
                            type="number"
                            min="-12"
                            max="12"
                            step="1"
                            defaultValue="0"
                            required
                        />
                    </div>
                    <div className="row">
                        <label htmlFor="create-layer-price_dy">价格垂直（-8～8 px）</label>
                        <input
                            id="create-layer-price_dy"
                            type="number"
                            min="-8"
                            max="8"
                            step="1"
                            defaultValue="0"
                            required
                        />
                    </div>
                    <div className="row">
                        <label htmlFor="create-layer-cta_dx">CTA 水平（-16～16 px）</label>
                        <input
                            id="create-layer-cta_dx"
                            type="number"
                            min="-16"
                            max="16"
                            step="1"
                            defaultValue="0"
                            required
                        />
                    </div>
                    <div className="row">
                        <label htmlFor="create-layer-cta_dy">CTA 垂直（-12～12 px）</label>
                        <input
                            id="create-layer-cta_dy"
                            type="number"
                            min="-12"
                            max="12"
                            step="1"
                            defaultValue="0"
                            required
                        />
                    </div>
                    <div className="row">
                        <label htmlFor="create-layer-logo_dx">Logo 水平（-16～16 px）</label>
                        <input
                            id="create-layer-logo_dx"
                            type="number"
                            min="-16"
                            max="16"
                            step="1"
                            defaultValue="0"
                            required
                        />
                    </div>
                    <div className="row">
                        <label htmlFor="create-layer-logo_dy">Logo 垂直（-16～16 px）</label>
                        <input
                            id="create-layer-logo_dy"
                            type="number"
                            min="-16"
                            max="16"
                            step="1"
                            defaultValue="0"
                            required
                        />
                    </div>
                    <div className="actions">
                        <button id="checkCreateLayout" type="button" className="secondary">
                            检测图层碰撞
                        </button>
                        <button id="fixCreateLayout" type="button" className="secondary" disabled>
                            应用安全偏移建议
                        </button>
                    </div>
                    <p id="createLayoutStatus" className="tip" aria-live="polite">
                        检测当前方形版式的文本、价格与 CTA 交叠。建议需主动应用，再保存版本。上传
                        Logo 的实际尺寸会在最终生成时再次验证。
                    </p>
                </details>
                <div className="row">
                    <label id="stylePresetLabel">本地背景配色（快速选择，非完整布局模板）</label>
                    <div className="style-presets" role="group" aria-labelledby="stylePresetLabel">
                        <button
                            className="style-preset"
                            type="button"
                            data-style-preset="蓝色科技风"
                            aria-pressed="true"
                            aria-label="选择蓝色科技风本地背景"
                        >
                            <span
                                className="style-swatch"
                                style={{
                                    background: 'linear-gradient(145deg,#1e376f,#081637)',
                                }}
                            ></span>
                            <span className="style-name">科技蓝</span>
                            <span className="style-hint">数码与科技</span>
                        </button>
                        <button
                            className="style-preset"
                            type="button"
                            data-style-preset="粉色浪漫"
                            aria-pressed="false"
                            aria-label="选择粉色浪漫本地背景"
                        >
                            <span
                                className="style-swatch"
                                style={{
                                    background: 'linear-gradient(145deg,#974178,#261b47)',
                                }}
                            ></span>
                            <span className="style-name">浪漫粉</span>
                            <span className="style-hint">美妆与礼物</span>
                        </button>
                        <button
                            className="style-preset"
                            type="button"
                            data-style-preset="绿色自然"
                            aria-pressed="false"
                            aria-label="选择绿色自然本地背景"
                        >
                            <span
                                className="style-swatch"
                                style={{
                                    background: 'linear-gradient(145deg,#24705e,#0b2f44)',
                                }}
                            ></span>
                            <span className="style-name">自然绿</span>
                            <span className="style-hint">健康与生活</span>
                        </button>
                        <button
                            className="style-preset"
                            type="button"
                            data-style-preset="橙色暖阳"
                            aria-pressed="false"
                            aria-label="选择橙色暖阳本地背景"
                        >
                            <span
                                className="style-swatch"
                                style={{
                                    background: 'linear-gradient(145deg,#c25c22,#59271d)',
                                }}
                            ></span>
                            <span className="style-name">活力橙</span>
                            <span className="style-hint">节日与促销</span>
                        </button>
                        <button
                            className="style-preset"
                            type="button"
                            data-style-preset="紫色梦幻"
                            aria-pressed="false"
                            aria-label="选择紫色梦幻本地背景"
                        >
                            <span
                                className="style-swatch"
                                style={{
                                    background: 'linear-gradient(145deg,#6e419d,#271c52)',
                                }}
                            ></span>
                            <span className="style-name">梦幻紫</span>
                            <span className="style-hint">时尚与创意</span>
                        </button>
                    </div>
                    <p id="stylePickerStatus" className="tip" aria-live="polite">
                        可选预设也可手动输入关键词；当前选择不会调用模型。
                    </p>
                </div>
                <div className="row">
                    <label htmlFor="create-style">画面风格</label>
                    <input
                        id="create-style"
                        name="style"
                        maxLength={80}
                        defaultValue="蓝色科技风"
                    />
                </div>
                <div className="row">
                    <label htmlFor="create-background_mode">背景模式</label>
                    <select id="create-background_mode" name="background_mode">
                        <option value="local">本地渐变（无需 API Key，不是 AI）</option>
                        <option value="ai">真实 Qwen-Image（需要 API Key，会计费）</option>
                    </select>
                </div>
                <div className="actions">
                    <button id="generate" type="submit">
                        生成海报
                    </button>
                    <span id="createStatus" className="tip">
                        上传商品图后生成
                    </span>
                </div>
            </form>
        </section>
    );
}
