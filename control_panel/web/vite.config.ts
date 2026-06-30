import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import babel from 'vite-plugin-babel'
import { TanStackRouterVite } from '@tanstack/router-plugin/vite'
import { VitePWA } from 'vite-plugin-pwa'
import { resolve } from 'path'

const apiTarget = process.env['VITE_API_TARGET'] ?? 'http://127.0.0.1:7717'

export default defineConfig({
  plugins: [
    TanStackRouterVite({ target: 'react', autoCodeSplitting: true }),
    babel({ babelConfig: { plugins: ['babel-plugin-react-compiler'] } }),
    react(),
    VitePWA({
      registerType: 'autoUpdate',
      injectRegister: 'auto',
      manifest: false,
      workbox: {
        globPatterns: ['**/*.{js,css,html,svg,png,ico,woff2}'],
        navigateFallback: '/index.html',
        navigateFallbackDenylist: [/^\/api/, /^\/events/, /^\/jobs/],
        cleanupOutdatedCaches: true,
        clientsClaim: true,
        maximumFileSizeToCacheInBytes: 3 * 1024 * 1024,
      },
      devOptions: { enabled: false },
    }),
  ],
  server: {
    port: 7718,
    strictPort: true,
    allowedHosts: ['photoflow.test'],
    proxy: {
      '/health': { target: apiTarget, changeOrigin: true },
      '/status': { target: apiTarget, changeOrigin: true },
      '/ops': { target: apiTarget, changeOrigin: true },
      '/jobs': { target: apiTarget, changeOrigin: true },
      '/events': { target: apiTarget, changeOrigin: true },
      '/analytics': { target: apiTarget, changeOrigin: true },
      '/index': { target: apiTarget, changeOrigin: true },
      '/backup': { target: apiTarget, changeOrigin: true },
      '/gallery': { target: apiTarget, changeOrigin: true },
    },
  },
  resolve: {
    alias: {
      '@pf/charts': resolve(import.meta.dirname, 'src/lib/charts'),
    },
    dedupe: ['react', 'react-dom', '@mantine/core', '@mantine/hooks'],
  },
  optimizeDeps: {
    include: [
      '@mantine/core',
      '@mantine/hooks',
      '@mantine/form',
      '@mantine/modals',
      '@mantine/notifications',
    ],
  },
})
