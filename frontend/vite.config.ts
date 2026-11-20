import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: { outDir: '../agenthub/static', emptyOutDir: true },
  server: { proxy: { '/api': process.env.AGENTHUB_BACKEND || 'http://127.0.0.1:8000' } },
})
