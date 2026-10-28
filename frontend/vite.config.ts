import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';

export default defineConfig({
    plugins: [
        react(),
        tailwindcss(),
        {
            name: 'workbench-full-reload',
            handleHotUpdate({ server }) {
                // DOM controllers must register once against a fresh document.
                server.ws.send({ type: 'full-reload' });
                return [];
            },
        },
    ],
    base: '/static/workbench/',
    build: {
        outDir: '../app/static/workbench',
        emptyOutDir: true,
        manifest: true,
        rollupOptions: {
            input: 'src/main.tsx',
            output: { format: 'iife', name: 'PosterWorkbench' },
        },
    },
    server: {
        proxy: {
            '/static/workbench/static': {
                target: 'http://127.0.0.1:8000',
                rewrite: (path) => path.replace('/static/workbench', ''),
            },
            '/api': {
                target: 'http://127.0.0.1:8000',
                // Preserve the browser's local Host so the backend can verify Origin.
                changeOrigin: false,
            },
            '/static/create-draft.js': 'http://127.0.0.1:8000',
            '/static/review-drafts.js': 'http://127.0.0.1:8000',
        },
    },
});
