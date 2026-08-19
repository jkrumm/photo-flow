import { defineNotifications, emit } from 'basalt-ui/notifications'
import { isDesktopNotifyEnabled } from './store'
import type { ActiveOp } from './store'
import { OP_LABELS } from './op-metadata'

/** Request OS notification permission — call on a user gesture (e.g. when the user enables desktop notifications). */
export function requestNotifyPermission(): void {
  if (typeof Notification !== 'undefined' && Notification.permission === 'default') {
    void Notification.requestPermission()
  }
}

type JobResult = Record<string, unknown> | null

function buildSummary(op: ActiveOp, result: JobResult): string {
  if (result === null) return 'Done.'
  switch (op) {
    case 'import': {
      const photos = Number(result['photos'] ?? 0)
      const raws = Number(result['raws'] ?? 0)
      const videos = Number(result['videos'] ?? 0)
      return `${photos} photos, ${raws} RAWs, ${videos} videos imported.`
    }
    case 'finalize': {
      const moved = Number(result['moved'] ?? 0)
      return `${moved} photos moved to Final.`
    }
    case 'cleanup': {
      const deleted = Number(result['deleted'] ?? 0)
      return `${deleted} orphaned RAWs deleted.`
    }
    case 'sync-gallery': {
      const synced = Number(result['synced'] ?? 0)
      return `${synced} photos synced to gallery.`
    }
    case 'backup': {
      const total = Number(result['total_scanned'] ?? result['scanned'] ?? 0)
      return `${total} files backed up.`
    }
    default:
      return 'Done.'
  }
}

/**
 * Typed job-completion notification registry — one success + one error kind per op, keyed
 * `${op}:done` / `${op}:error`. Routed through basalt-ui/notifications' intent mapping so every
 * job toast lands in the persisted history/bell for free (see basalt-notifications.md).
 */
// NotificationSpec.toMessage is (payload: unknown) => ReactNode — this repo's strict
// tsconfig (exactOptionalPropertyTypes) rejects a narrower per-kind param type there, so each
// entry casts back to the shape only `notifyJobDone`'s own emit() calls ever pass.
function doneMessage(op: ActiveOp) {
  return (p: unknown) => buildSummary(op, p as JobResult)
}
function errorMessage(p: unknown): string {
  return p as string
}

const JOB_NOTIFICATIONS = defineNotifications({
  'import:done': { intent: 'success', toMessage: doneMessage('import') },
  'import:error': { intent: 'error', toMessage: errorMessage },
  'import:interrupted': { intent: 'warning', toMessage: errorMessage },
  'finalize:done': { intent: 'success', toMessage: doneMessage('finalize') },
  'finalize:error': { intent: 'error', toMessage: errorMessage },
  'finalize:interrupted': { intent: 'warning', toMessage: errorMessage },
  'cleanup:done': { intent: 'success', toMessage: doneMessage('cleanup') },
  'cleanup:error': { intent: 'error', toMessage: errorMessage },
  'cleanup:interrupted': { intent: 'warning', toMessage: errorMessage },
  'sync-gallery:done': { intent: 'success', toMessage: doneMessage('sync-gallery') },
  'sync-gallery:error': { intent: 'error', toMessage: errorMessage },
  'sync-gallery:interrupted': { intent: 'warning', toMessage: errorMessage },
  'backup:done': { intent: 'success', toMessage: doneMessage('backup') },
  'backup:error': { intent: 'error', toMessage: errorMessage },
  'backup:interrupted': { intent: 'warning', toMessage: errorMessage },
})

declare module 'basalt-ui' {
  interface BasaltRegister {
    notifications: typeof JOB_NOTIFICATIONS
  }
}

/**
 * Emits the typed job-completion toast (recorded to the notification bell/history).
 * Additionally fires a native OS notification only when:
 *   - the tab is backgrounded (document.hidden)
 *   - Notification.permission === 'granted'
 *   - the user's desktop-notify preference is on
 */
export function notifyJobDone(
  op: ActiveOp,
  result: Record<string, unknown> | null,
  error: string | null,
): void {
  const label = OP_LABELS[op]
  const message = error !== null ? error : buildSummary(op, result)

  if (error !== null) {
    emit(`${op}:error`, error, { title: `${label} failed` })
  } else {
    emit(`${op}:done`, result, { title: `${label} complete` })
  }

  if (
    document.hidden &&
    typeof Notification !== 'undefined' &&
    Notification.permission === 'granted' &&
    isDesktopNotifyEnabled()
  ) {
    try {
      // The Notification constructor fires the OS notification as a side effect.
      void new Notification(error !== null ? `${label} failed` : `${label} complete`, {
        body: message,
      })
    } catch {
      // Toast already shown above — OS notification is optional
    }
  }
}

/**
 * Records the outcome of a job that finished while the panel was NOT watching.
 *
 * Jobs run server-side in an always-on daemon, so a backup started before dinner
 * completes whether or not a tab is open — and until now that completion produced no
 * toast and no history entry, leaving the bell a record of what was watched rather
 * than of what happened. This replays such a job into the notification history when
 * the panel next opens.
 *
 * Deliberately quieter than the live seam: no chime and no OS notification. The event
 * is already over, several may arrive at once, and a burst of chimes for things that
 * finished hours ago is noise. The toast + bell entry is the record.
 */
export function notifyJobCatchUp(
  op: ActiveOp,
  status: string,
  result: Record<string, unknown> | null,
  error: string | null,
): void {
  const label = OP_LABELS[op]

  if (status === 'interrupted') {
    emit(`${op}:interrupted`, error ?? 'The server stopped while this job was running.', {
      title: `${label} interrupted`,
    })
    return
  }
  if (status === 'done') {
    emit(`${op}:done`, result, { title: `${label} complete` })
    return
  }
  // failed / cancelled / needs_confirm — all carry a reason worth keeping.
  const TITLES: Record<string, string> = {
    failed: `${label} failed`,
    cancelled: `${label} cancelled`,
    needs_confirm: `${label} needs review`,
  }
  emit(`${op}:error`, error ?? status, { title: TITLES[status] ?? `${label} — ${status}` })
}
