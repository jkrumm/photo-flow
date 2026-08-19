import { useNavigate } from '@tanstack/react-router'
import { Loader, Progress, Tooltip } from '@mantine/core'
import { useActiveJobStore, useJobLiveStore } from '../../lib/store'
import { OP_LABELS } from '../../lib/op-metadata'
import classes from './job-progress-pill.module.css'

/**
 * Compact job-progress indicator rendered in the app header.
 *
 * Visible on every route while a job is active (activeJobId !== null).
 * Shows the op label, a thin progress bar, and a spinner. Clicking navigates
 * to /pipeline so the user can view the full console panel.
 *
 * Progress state is read from useJobLiveStore — updated by JobController on
 * every SSE event regardless of which route is mounted.
 */
export function JobProgressPill() {
  const navigate = useNavigate()
  const activeJobId = useActiveJobStore((s) => s.activeJobId)
  const activeOp = useActiveJobStore((s) => s.activeOp)
  const taskProgress = useJobLiveStore((s) => s.taskProgress)
  const taskTotal = useJobLiveStore((s) => s.taskTotal)
  const taskDesc = useJobLiveStore((s) => s.taskDesc)

  if (activeJobId === null || activeOp === null) return null

  const hasTotal = taskTotal !== null && taskTotal > 0
  const pct = hasTotal ? Math.min((taskProgress / taskTotal) * 100, 99) : 0
  const label = OP_LABELS[activeOp]

  return (
    <Tooltip label={taskDesc ?? `${label} running…`} withArrow openDelay={300}>
      <button
        type="button"
        className={classes.pill}
        onClick={() => void navigate({ to: '/pipeline' })}
        aria-label={`${label} running — click to view progress`}
      >
        <Loader size={10} />
        <span>{label}</span>
        {hasTotal && (
          <Progress
            value={pct}
            size={3}
            radius="xs"
            animated
            style={{ width: 40, flexShrink: 0 }}
          />
        )}
      </button>
    </Tooltip>
  )
}
