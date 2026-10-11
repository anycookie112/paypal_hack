import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    // During development the FastAPI backend runs on :8000 (uv run python main.py).
    proxy: { '/api': 'http://127.0.0.1:8000' },
  },
})
