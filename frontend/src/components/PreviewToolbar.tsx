import { useState } from 'react';
import { Maximize, Minus, Plus } from 'lucide-react';

export function PreviewToolbar() {
    const [zoom, setZoom] = useState(100);
    function changeZoom(next: number) {
        setZoom(next);
        const stage = document.getElementById('previewStage');
        if (stage) stage.style.setProperty('--preview-zoom', String(next / 100));
    }
    return (
        <div
            className="preview-toolbar flex items-center justify-between gap-3"
            aria-label="海报预览工具"
        >
            <span className="text-xs text-slate-500">
                方形画布 <span className="ml-2 text-slate-400">1024 × 1024</span>
            </span>
            <div className="flex items-center gap-1">
                <button
                    type="button"
                    aria-label="缩小预览"
                    disabled={zoom <= 50}
                    onClick={() => changeZoom(zoom - 25)}
                >
                    <Minus size={15} />
                </button>
                <output className="w-12 text-center text-xs tabular-nums" aria-live="polite">
                    {zoom}%
                </output>
                <button
                    type="button"
                    aria-label="放大预览"
                    disabled={zoom >= 150}
                    onClick={() => changeZoom(zoom + 25)}
                >
                    <Plus size={15} />
                </button>
                <button
                    type="button"
                    className="ml-2"
                    aria-label="适应画布"
                    onClick={() => changeZoom(100)}
                >
                    <Maximize size={15} />
                </button>
            </div>
        </div>
    );
}
