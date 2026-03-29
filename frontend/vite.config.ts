import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  root: '.',
  base: '/dist/',
  plugins: [react()],
  build: {
    outDir: 'dist',
    emptyOutDir: false,      // do not wipe dist/ on each build
    rollupOptions: {
      input: {
        globe: 'globe.html', // output is dist/globe.html, not dist/index.html
      },
    },
  },
})
