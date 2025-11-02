import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import tsconfigPaths from 'vite-tsconfig-paths'
import { resolve } from 'path'

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const isSDK = process.env.BV_BUILD_SDK === '1'
  const EDGE = env.VITE_API_BASE || 'http://localhost:8080'
  const GW   = env.VITE_GATEWAY_BASE || 'http://localhost:8081'
  const MEM  = env.VITE_MEMORY_BASE || 'http://localhost:8082'
  return {
    plugins: [
      react(),
      tsconfigPaths(), // enables @bv/* path aliases from tsconfig
    ],
    server: {
      host: true,
      port: 5173,
      // Use env-provided bases to avoid hardcoding & duplication
      proxy: {
        // /config must come from the Gateway in dev
        '/config': { target: GW, changeOrigin: true },
        // v3 (bundles, ops, query) must also come from Gateway – FE must NOT hit 5173 here
        '/v3': {
          target: GW, changeOrigin: true,
        },
        // Dev-only proxy so FE hits the Edge (BFF) locally
        '/v2': { target: EDGE, changeOrigin: true },
        // Dev-only proxy for the Memory API used by the Allowed-IDs drawer.
        // Strip the /memory prefix so FastAPI sees /api/… paths.
        '/memory': { target: MEM, changeOrigin: true, rewrite: (p) => p.replace(/^\/memory/, '') },
      },
    },
    // Default (app) build stays unchanged; SDK build only when BV_BUILD_SDK=1
    build: isSDK
      ? {
          target: 'es2020',
          lib: {
            entry: resolve(__dirname, 'src/sdk/client.ts'),
            name: 'BVClient',
            fileName: () => 'client',
            formats: ['es'], // ensure top-level `export` is valid
          },
          rollupOptions: {
            external: ['@bv/fp'], // don’t inline your local crypto/fp lib
          },
          emptyOutDir: true,
        }
      : {
          target: 'es2020',
          rollupOptions: {
            output: {
              manualChunks: {
                'vendor-react': ['react', 'react-dom', '@tanstack/react-query'],
                'vendor-sodium': ['libsodium-sumo'],
              },
            },
          },
          chunkSizeWarningLimit: 1500,
        },
  }
})
