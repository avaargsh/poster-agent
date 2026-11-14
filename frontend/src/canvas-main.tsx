import { createRoot } from 'react-dom/client';
import { CanvasApp } from './canvas/CanvasApp';
import './canvas/canvas.css';

const root = document.getElementById('canvas-root');
if (!root) throw new Error('画布挂载节点缺失');
createRoot(root).render(<CanvasApp />);
document.getElementById('canvas-boot')?.remove();
