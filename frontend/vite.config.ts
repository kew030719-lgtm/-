import react from '@vitejs/plugin-react'
// vitest/config re-exports Vite's defineConfig with the `test` key typed.
import { defineConfig } from 'vitest/config'

export default defineConfig({
  plugins: [react()],
  // FastAPI serves the built SPA from career_radar/static/app at /app.
  base: '/app/',
  build: {
    outDir: '../career_radar/static/app',
    emptyOutDir: true,
  },
  server: {
    // Dev-only proxy: keeps the SPA same-origin with the API so there is no CORS
    // surface to configure. Production is same-origin by construction.
    proxy: {
      '/api': 'http://127.0.0.1:8000',
      '/health': 'http://127.0.0.1:8000',
      '/browser-helper.zip': 'http://127.0.0.1:8000',
    },
  },
  test: {
    environment: 'node',
  },
})