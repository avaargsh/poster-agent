import { CreationForm } from './CreationForm';
import { PosterEditor } from './PosterEditor';
import { PreviewToolbar } from './PreviewToolbar';

export function CreateWorkspace() {
    return (
        <section
            className="workspace-view"
            id="view-create"
            role="tabpanel"
            aria-labelledby="nav-create"
            tabIndex={0}
        >
            <div className="workspace-heading">
                <div>
                    <h2>创作海报</h2>
                    <p className="tip">上传商品图、填写内容，在右侧预览和调整。</p>
                </div>
                <span className="quiet-label">默认使用免费本地背景</span>
            </div>
            <div className="creation-grid">
                <CreationForm />
                <section className="panel canvas-panel" aria-label="预览与编辑">
                    <div className="canvas-heading">
                        <h3>海报预览</h3>
                        <span className="tag" id="source">
                            尚未生成
                        </span>
                    </div>
                    <div id="previewEmpty" className="preview-empty">
                        <svg
                            width="56"
                            height="56"
                            viewBox="0 0 56 56"
                            fill="none"
                            aria-hidden="true"
                        >
                            <rect
                                x="8"
                                y="4"
                                width="40"
                                height="48"
                                rx="5"
                                stroke="currentColor"
                                strokeWidth="2"
                            ></rect>
                            <circle
                                cx="20"
                                cy="18"
                                r="4"
                                stroke="currentColor"
                                strokeWidth="2"
                            ></circle>
                            <path
                                d="M14 43l10-12 7 7 6-6 6 11"
                                stroke="currentColor"
                                strokeWidth="2"
                                strokeLinejoin="round"
                            ></path>
                        </svg>
                        <h3>海报会出现在这里</h3>
                        <p>先选择商品图，再填写标题和价格。</p>
                        <button type="button" id="chooseProduct" className="secondary">
                            选择商品图片
                        </button>
                    </div>
                    <PreviewToolbar />
                    <div id="previewStage" className="preview-stage">
                        <img id="preview" className="preview" alt="生成海报预览" hidden />
                    </div>
                    <p className="tip" id="metadata" role="status">
                        生成后可修改文案、保留历史版本，并导出不同尺寸。
                    </p>
                    <PosterEditor />
                </section>
            </div>
        </section>
    );
}
