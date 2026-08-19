import react from '@vitejs/plugin-react'
import { TanStackRouterVite } from '@tanstack/router-plugin/vite'
import { basaltAppPlugin, basaltViteConfig } from 'basalt-ui/vite'
import { defineConfig } from 'vite'
import babel from 'vite-plugin-babel'

const apiTarget = process.env['VITE_API_TARGET'] ?? 'http://127.0.0.1:7717'

/**
 * The FastAPI server mounts most of its routes at the bare prefixes below (not under a single
 * `/api`), so the preset's one-target `apiTarget` proxy can't express it — we override
 * `server.proxy`. `/api` is the newer, correctly-namespaced lane (photos): it must stay distinct
 * from the SPA's own `/photos` route, which is NOT proxied and falls through to the dev server.
 */
const API_PREFIXES = [
  '/api',
  '/health',
  '/status',
  '/ops',
  '/jobs',
  '/events',
  '/analytics',
  '/index',
  '/backup',
  '/gallery',
]

const base = basaltViteConfig({ port: 7718, allowedHosts: ['photoflow.test'] })

export default defineConfig({
  ...base,
  server: {
    ...base.server,
    proxy: Object.fromEntries(
      API_PREFIXES.map((p) => [p, { target: apiTarget, changeOrigin: true }]),
    ),
  },
  plugins: [
    TanStackRouterVite({ target: 'react', autoCodeSplitting: true }),
    babel({ babelConfig: { plugins: ['babel-plugin-react-compiler'] } }),
    react(),
    ...basaltAppPlugin({
      name: 'Photo Flow',
      shortName: 'PhotoFlow',
      description: 'Local control panel for the Fuji X-T4 photo pipeline.',
      serviceWorker: {
        workbox: {
          globPatterns: ['**/*.{js,css,html,svg,png,ico,woff2}'],
          navigateFallbackDenylist: [/^\/api/, /^\/events/, /^\/jobs/],
          maximumFileSizeToCacheInBytes: 3 * 1024 * 1024,
        },
      },
    }),
  ],
})
