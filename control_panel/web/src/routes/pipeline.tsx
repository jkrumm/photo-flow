import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { Stack, Text, Title } from '@mantine/core'
import { IconCamera } from '@tabler/icons-react'
import { useCallback } from 'react'
import { PipelineHero } from '../components/pipeline/PipelineHero'
import type { ActiveOp } from '../lib/store'

// ── Search params ─────────────────────────────────────────────────────────────
// Library Health deep-links via ?action=cleanup|backup|sync.
// The pipeline component reads this once on mount, opens the matching dry-run
// modal, then replaces the URL to consume-and-clear the param.

type PipelineSearch = {
  action?: 'cleanup' | 'backup' | 'sync'
}

export const Route = createFileRoute('/pipeline')({
  validateSearch: (search: Record<string, unknown>): PipelineSearch => {
    const raw = search['action']
    if (raw === 'cleanup' || raw === 'backup' || raw === 'sync') {
      return { action: raw }
    }
    return {}
  },
  component: PipelinePage,
})

function PipelinePage() {
  const { action } = Route.useSearch()
  const navigate = useNavigate()

  // Map the URL param to the ActiveOp used by PipelineHero.
  const initialAction: ActiveOp | undefined =
    action === 'cleanup' ? 'cleanup' :
    action === 'backup'  ? 'backup'  :
    action === 'sync'    ? 'sync-gallery' :
    undefined

  // Called by PipelineHero immediately on mount when initialAction is set,
  // replacing the URL so a hard-refresh doesn't re-trigger the modal.
  const onConsumeAction = useCallback(() => {
    void navigate({ to: '/pipeline', replace: true })
  }, [navigate])

  return (
    <Stack gap="md">
      <Stack gap={4}>
        <Title order={2} style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <IconCamera size={24} />
          Pipeline
        </Title>
        <Text c="dimmed" size="sm">
          Camera → Staging → Final → Publish — live counts; click any transition to run it
        </Text>
      </Stack>
      <PipelineHero
        {...(initialAction !== undefined ? { initialAction } : {})}
        onConsumeAction={onConsumeAction}
      />
    </Stack>
  )
}
