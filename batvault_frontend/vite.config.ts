import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      // Dev-only proxy so FE hits the Edge (BFF) locally
      '/v2': {
        target: 'http://localhost:8080',
        changeOrigin: true,
      },
      // Dev-only proxy for the Memory API used by the Allowed-IDs drawer.
      // Strip the /memory prefix so FastAPI sees /api/… paths.
      '/memory': {
        target: 'http://localhost:8082',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/memory/, ''),
      },
    },
  },
})
