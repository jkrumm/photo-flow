import { Badge } from '@mantine/core'
import type { JobStatus } from '../../lib/api-types'

/**
 * One badge for every job status, shared by the live queue and the durable history so
 * the two surfaces can never drift into two vocabularies for the same state.
 *
 * `interrupted` is the status this whole feature exists for: a job the server was
 * still running when the process died. It takes the same attention hue as
 * `needs_confirm` rather than the failure red — nothing went wrong with the photos,
 * the work simply stopped partway and is worth re-running.
 */
const STATUS_MAP: Record<JobStatus, { color: string; label: string }> = {
  queued: { color: 'blue', label: 'Queued' },
  running: { color: 'green', label: 'Running' },
  needs_confirm: { color: 'orange', label: 'Needs review' },
  interrupted: { color: 'orange', label: 'Interrupted' },
  done: { color: 'gray', label: 'Done' },
  failed: { color: 'red', label: 'Failed' },
  cancelled: { color: 'gray', label: 'Cancelled' },
}

export function JobStatusBadge({ status }: { status: string }) {
  const meta = STATUS_MAP[status as JobStatus] ?? { color: 'gray', label: status }
  return (
    <Badge color={meta.color} variant="light" size="xs" radius="sm">
      {meta.label}
    </Badge>
  )
}
