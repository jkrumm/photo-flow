import '@mantine/core/styles.layer.css'
import '@mantine/notifications/styles.layer.css'
import '@mantine/spotlight/styles.layer.css'
import 'basalt-ui/styles.css'
import './styles/native.css'
import './styles/shell.css'

import { StrictMode, useEffect } from 'react'
import { createRoot } from 'react-dom/client'
import { Button, Code, Stack, Text, Title, useMantineColorScheme } from '@mantine/core'
import { createRouter, RouterProvider } from '@tanstack/react-router'
import { BasaltErrorBoundary, BasaltProvider, createBasaltTheme } from 'basalt-ui'
import { BasaltOverlays } from 'basalt-ui/commands'
import { BasaltQueryDevtools, QueryClientProvider } from 'basalt-ui/query'
import { registerCommands } from './lib/commands'
import { queryClient } from './lib/query-client'
import { paletteGroups } from './lib/series'
import { routeTree } from './routeTree.gen'

const router = createRouter({
  routeTree,
  context: { queryClient },
  defaultPreload: 'intent',
  defaultPreloadStaleTime: 0,
})

declare module '@tanstack/react-router' {
  interface Register {
    router: typeof router
  }
}

const theme = createBasaltTheme()

function ErrorFallback({ error }: { error: unknown }) {
  return (
    <Stack p="xl" gap="sm">
      <Title order={2}>Something went wrong</Title>
      <Code block>{error instanceof Error ? error.message : String(error)}</Code>
      <Button variant="default" w="fit-content" onClick={() => window.location.reload()}>
        Reload panel
      </Button>
      <Text size="sm" c="dimmed">
        The photoflow service keeps running — only this view crashed.
      </Text>
    </Stack>
  )
}

/** Commands need the live router + Mantine's scheme setter, so they register inside the tree. */
function Commands() {
  const { toggleColorScheme } = useMantineColorScheme()
  useEffect(() => {
    registerCommands(router, toggleColorScheme)
  }, [toggleColorScheme])
  return null
}

/**
 * Reload once the freshly-deployed service worker takes control.
 *
 * `basaltAppPlugin`'s PWA defaults are `registerType: 'autoUpdate'` with `skipWaiting` +
 * `clientsClaim`, but the `registerSW.js` it injects only *registers* — it never reacts to the
 * new worker claiming the page. So a deploy played out like this: load N still ran the
 * precached OLD bundle while the new worker installed and claimed underneath it, and only
 * load N+1 saw the change. In a browser tab that self-corrects on the next ⌘R; in the Dock
 * PWA, which stays open for days, it means a shipped change is simply never visible — which
 * is exactly what "the Mac app isn't updating" looked like from the outside.
 *
 * `controllerchange` fires when a *different* worker takes over, so the guard is only against
 * a double fire, not against the first-ever registration (there the page starts uncontrolled
 * and this never runs). `window.location.reload()` then hits the new precache.
 */
if ('serviceWorker' in navigator) {
  let reloading = false
  navigator.serviceWorker.addEventListener('controllerchange', () => {
    if (reloading) return
    reloading = true
    window.location.reload()
  })
}

const rootEl = document.getElementById('root')
if (!rootEl) throw new Error('Root element not found')

createRoot(rootEl).render(
  <StrictMode>
    <BasaltProvider
      theme={theme}
      paletteOptions={{ groups: paletteGroups }}
      defaultColorScheme="dark"
      healthUrl="/health"
    >
      <BasaltErrorBoundary onError={() => {}} fallback={(error) => <ErrorFallback error={error} />}>
        <BasaltOverlays>
          <QueryClientProvider client={queryClient}>
            <Commands />
            <RouterProvider router={router} />
            {process.env.NODE_ENV !== 'production' && <BasaltQueryDevtools />}
          </QueryClientProvider>
        </BasaltOverlays>
      </BasaltErrorBoundary>
    </BasaltProvider>
  </StrictMode>,
)
