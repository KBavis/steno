import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    // The backend serves /api (Admin API) and /mcp on :8000 (`make api`).
    proxy: { '/api': process.env.STENO_API_URL ?? 'http://localhost:8000' },
  },
})
