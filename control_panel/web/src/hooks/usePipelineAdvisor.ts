import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { pipelineQueries } from '../lib/queries/pipeline'
import { backupQueries } from '../lib/queries/backup'
import type { ActiveOp } from '../lib/store'

export type Advice = {
  op: ActiveOp
  headline: string
  detail?: string
  priority: number
  tone: 'action' | 'attention' | 'idle'
  disabled?: boolean
  disabledReason?: string
}

/** Backup is considered stale after 3 days without a successful run. */
const STALE_BACKUP_MS = 3 * 24 * 60 * 60 * 1000

/**
 * Derives a ranked list of workflow advice items from the pipeline snapshot.
 *
 * Rules (in priority order):
 *  1. import  — when camera has files (highest, fresh capture)
 *  2. finalize — when staging has files
 *  3. backup  — soft signal; when backup is older than 3 days, never run, or the homelab is
 *              holding stale .photo-edit history (a Photomator re-edit changes ONLY the sidecar,
 *              so a fresh-looking timestamp says nothing about whether it has been carried up)
 *  4. sync-gallery — when there are unpublished 4+ rated photos
 *  5. cleanup — only when orphaned RAWs exist; always ranked last (destructive)
 *
 * The opt-in Staging mirror is deliberately NOT advised here: Staging holding files is the normal
 * state between import and finalize, so an advice for it would fire on every import and read as
 * noise. It stays an explicit action on the backup control.
 *
 * Disabled advices (precondition not met) are included with a disabledReason
 * rather than dropped. When nothing is actionable, a single idle advice is returned.
 */
export function usePipelineAdvisor(): Advice[] {
  const { data: snap } = useQuery(pipelineQueries.snapshot())
  const { data: backupAvail } = useQuery(backupQueries.availability())
  // Same queryKey the Pipeline hero and Library card already poll — a cache hit, not a 4th request.
  const { data: backupRemote } = useQuery(backupQueries.availabilityRemote())

  return useMemo(() => {
    if (!snap) return []

    const tailscaleConnected =
      backupAvail?.connection !== null && backupAvail?.connection !== undefined

    const advices: Advice[] = []

    // ── 1. Import ─────────────────────────────────────────────────────────────
    const pendingTotal = snap.pending_photos + snap.pending_videos + snap.pending_raws
    if (pendingTotal > 0) {
      const parts: string[] = []
      if (snap.pending_photos > 0)
        parts.push(`${snap.pending_photos} photo${snap.pending_photos !== 1 ? 's' : ''}`)
      if (snap.pending_raws > 0)
        parts.push(`${snap.pending_raws} RAW${snap.pending_raws !== 1 ? 's' : ''}`)
      if (snap.pending_videos > 0)
        parts.push(`${snap.pending_videos} video${snap.pending_videos !== 1 ? 's' : ''}`)

      const a: Advice = {
        op: 'import',
        headline: `${pendingTotal} file${pendingTotal !== 1 ? 's' : ''} on camera`,
        detail: parts.join(', '),
        priority: 1,
        tone: 'action',
      }
      if (!snap.camera_connected) {
        a.disabled = true
        a.disabledReason = 'Camera not connected'
      }
      advices.push(a)
    }

    // ── 2. Finalize ───────────────────────────────────────────────────────────
    if (snap.staging_files > 0) {
      advices.push({
        op: 'finalize',
        headline: `${snap.staging_files} photo${snap.staging_files !== 1 ? 's' : ''} in Staging`,
        detail: 'Move to Final at full quality',
        priority: 2,
        tone: 'action',
      })
    }

    // ── 3. Backup (soft staleness signal) ─────────────────────────────────────
    if (snap.final_count > 0) {
      const backupRun = snap.last_runs.backup
      const backupTsStr = backupRun?.ts ?? null
      const backupTs = backupTsStr ? new Date(backupTsStr) : null
      const ageMs = backupTs !== null ? Date.now() - backupTs.getTime() : null
      // A run that did not finish must never buy staleness credit for its timestamp.
      // The startup sweep stamps an interrupted backup with the moment it was detected,
      // so age alone would read "backed up 2 minutes ago" for a transfer that died 80 %
      // through and then go quiet for three days.
      const lastRunIncomplete = backupRun !== null && backupRun.ok === false
      const isStale = ageMs === null || ageMs > STALE_BACKUP_MS || lastRunIncomplete

      // A re-edit in Photomator rewrites only the .photo-edit sidecar, so the JPG count and the
      // last-run timestamp both stay clean while the homelab holds outdated edit history. -1 means
      // the remote count is unknown (unreachable), not a gap.
      const sidecarBehind = backupRemote?.final?.sidecar_needs_sync ?? 0
      const editHistoryStale = sidecarBehind > 0

      if (isStale || editHistoryStale) {
        let ageLabel = 'never backed up'
        if (ageMs !== null) {
          const days = Math.floor(ageMs / (24 * 60 * 60 * 1000))
          if (days >= 1) {
            ageLabel = `last backup ${days}d ago`
          } else {
            const hours = Math.floor(ageMs / (60 * 60 * 1000))
            ageLabel = `last backup ${hours}h ago`
          }
        }
        let detail: string
        if (lastRunIncomplete) {
          detail =
            backupRun?.interrupted === true
              ? 'last backup interrupted — the server stopped mid-transfer'
              : 'last backup failed'
        } else if (editHistoryStale) {
          detail = `${sidecarBehind} edit histor${sidecarBehind !== 1 ? 'ies' : 'y'} not backed up`
        } else {
          detail = ageLabel
        }

        const a: Advice = {
          op: 'backup',
          headline: 'Backup to homelab',
          detail,
          priority: 3,
          tone: 'attention',
        }
        if (!tailscaleConnected) {
          a.disabled = true
          a.disabledReason = 'Homelab unreachable over Tailscale'
        }
        advices.push(a)
      }
    }

    // ── 4. Sync Gallery ───────────────────────────────────────────────────────
    if (snap.unpublished_high_rated > 0) {
      advices.push({
        op: 'sync-gallery',
        headline: `${snap.unpublished_high_rated} unpublished photo${snap.unpublished_high_rated !== 1 ? 's' : ''}`,
        detail: 'Rating ≥ 4 — not yet in gallery',
        priority: 4,
        tone: 'attention',
      })
    }

    // ── 5. Cleanup (always last — destructive housekeeping) ───────────────────
    if (snap.orphaned_raws > 0) {
      const a: Advice = {
        op: 'cleanup',
        headline: `${snap.orphaned_raws} orphaned RAW${snap.orphaned_raws !== 1 ? 's' : ''}`,
        detail: 'No matching Final JPG',
        priority: 5,
        tone: 'attention',
      }
      if (!snap.ssd_connected) {
        a.disabled = true
        a.disabledReason = 'Requires external SSD'
      }
      advices.push(a)
    }

    if (advices.length === 0) {
      return [
        {
          op: 'import' as const,
          headline: 'Pipeline clear — nothing pending',
          priority: 0,
          tone: 'idle' as const,
        },
      ]
    }

    return advices.toSorted((a, b) => a.priority - b.priority)
  }, [snap, backupAvail, backupRemote])
}
