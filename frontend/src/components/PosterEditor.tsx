import { ReviewPanel } from './ReviewPanel';
import { ExportPanel } from './ExportPanel';
import { ContentEditor } from './ContentEditor';
export function PosterEditor() {
    return (
        <section id="editor" className="hidden">
            <div className="editor-tabs" role="tablist" aria-label="海报操作">
                <button
                    id="editor-tab-content"
                    type="button"
                    role="tab"
                    aria-selected="true"
                    aria-controls="editor-content"
                    data-editor-tab="content"
                >
                    编辑内容
                </button>
                <button
                    id="editor-tab-export"
                    type="button"
                    role="tab"
                    aria-selected="false"
                    tabIndex={-1}
                    aria-controls="editor-export"
                    data-editor-tab="export"
                >
                    尺寸与导出
                </button>
                <button
                    id="editor-tab-check"
                    type="button"
                    role="tab"
                    aria-selected="false"
                    tabIndex={-1}
                    aria-controls="editor-check"
                    data-editor-tab="check"
                >
                    检查与标注
                </button>
            </div>
            <ContentEditor />
            <ExportPanel />
            <ReviewPanel />
        </section>
    );
}
