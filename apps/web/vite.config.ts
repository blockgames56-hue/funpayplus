import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tailwind from '@tailwindcss/vite';
import { fileURLToPath, URL } from 'node:url';

export default defineConfig({
  resolve: { alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) } },
  plugins: [react(), tailwind()],
  clearScreen: false,
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      '/api': { target: 'http://127.0.0.1:8765', ws: true },
    },
  },
  build: {
    rollupOptions: {
      output: {
        manualChunks(id) {
          const path = id.replace(/\\/g, '/');
          if (!path.includes('/node_modules/')) return;
          if (/\/node_modules\/(react|react-dom|scheduler)\//.test(path)) return 'react';
          if (/\/node_modules\/(recharts|recharts-scale|d3-[^/]+|victory-vendor)\//.test(path)) return 'charts';
          if (/\/node_modules\/(motion|motion-dom|motion-utils|framer-motion)\//.test(path)) return 'motion';
          if (path.includes('/node_modules/@radix-ui/') || path.includes('/node_modules/cmdk/')) return 'ui';
        },
      },
    },
  },
});
