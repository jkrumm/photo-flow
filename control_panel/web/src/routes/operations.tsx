import { createFileRoute, redirect } from '@tanstack/react-router'

// Operations was merged into the Pipeline command center (every edge is its op).
// Kept as a redirect so old bookmarks / PWA shortcuts still resolve.
export const Route = createFileRoute('/operations')({
  beforeLoad: () => {
    throw redirect({ to: '/pipeline' })
  },
})
