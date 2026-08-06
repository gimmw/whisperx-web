import { defineConfig } from 'vite'
import { svelte } from '@sveltejs/vite-plugin-svelte'

// Backend to proxy /api to during development. Override when the backend runs
// somewhere other than the local default.
const DEV_BACKEND = process.env.DEV_BACKEND ?? 'http://localhost:49585'

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [svelte()],
  server: {
    // Mirrors the nginx /api/ proxy so dev matches production: requests stay
    // same-origin and CORS is never exercised locally. Without this, the dev
    // server would make genuine cross-origin calls that production does not.
    proxy: {
      '/api': {
        target: DEV_BACKEND,
        changeOrigin: true,
        // Strip the /api prefix, matching the trailing slash on nginx's
        // proxy_pass.
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
    },
  },
})
