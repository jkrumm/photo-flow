import { createBasaltQueryClient } from 'basalt-ui/query'

/**
 * The panel polls a localhost FastAPI, so a focus refetch buys nothing and a long retry just
 * delays the error — both defaults are tightened over the basalt dashboard baseline.
 */
export const queryClient = createBasaltQueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      refetchOnWindowFocus: false,
      retry: 1,
    },
  },
})
