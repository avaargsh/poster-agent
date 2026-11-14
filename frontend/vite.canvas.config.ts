import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
    plugins: [react()],
    base: '/static/canvas/',
    build: {
        outDir: '../app/static/canvas',
        emptyOutDir: true,
        manifest: true,
        rollupOptions: {
            input: 'src/canvas-main.tsx',
            output: { format: 'iife', name: 'PosterCanvas' },
        },
    },
});
