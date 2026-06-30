import { notifications } from '@mantine/notifications'
import { useUiStore } from './store'
import type { ActiveOp } from './store'
import { OP_LABELS } from './op-metadata'

/** Request OS notification permission — call on a user gesture (e.g. when the user enables desktop notifications). */
export function requestNotifyPermission(): void {
  if (typeof Notification !== 'undefined' && Notification.permission === 'default') {
    void Notification.requestPermission()
  }
}

/**
 * Always shows a Mantine toast (green / red).
 * Additionally fires a native OS notification only when:
 *   - the tab is backgrounded (document.hidden)
 *   - Notification.permission === 'granted'
 *   - useUiStore.desktopNotifyEnabled
 */
export function notifyJobDone(
  op: ActiveOp,
  result: Record<string, unknown> | null,
  error: string | null,
): void {
  const label = OP_LABELS[op]
  const isError = error !== null
  const message = isError ? error : buildSummary(op, result)

  notifications.show({
    title: isError ? `${label} failed` : `${label} complete`,
    message,
    color: isError ? 'red' : 'green',
    autoClose: 6000,
  })

  if (
    document.hidden &&
    typeof Notification !== 'undefined' &&
    Notification.permission === 'granted' &&
    useUiStore.getState().desktopNotifyEnabled
  ) {
    try {
      // The Notification constructor fires the OS notification as a side effect.
      void new Notification(isError ? `${label} failed` : `${label} complete`, { body: message })
    } catch {
      // Toast already shown above — OS notification is optional
    }
  }
}

function buildSummary(op: ActiveOp, result: Record<string, unknown> | null): string {
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
