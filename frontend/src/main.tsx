import { hydrateRoot, createRoot } from 'react-dom/client';
import { flushSync } from 'react-dom';
import { App } from './App';
import './styles.css';

const root = document.getElementById('root');
if (!root) throw new Error('工作台挂载节点缺失');
flushSync(() => {
    if (root.hasChildNodes()) {
        hydrateRoot(root, <App />, {
            onRecoverableError(error) {
                console.error('工作台界面恢复异常', error);
            },
        });
    } else {
        createRoot(root).render(<App />);
    }
});
document.documentElement.dataset.reactMounted = 'true';

if (import.meta.env.DEV) {
    import('./dev-controller').then(({ initializeDevelopment }) => initializeDevelopment());
}
