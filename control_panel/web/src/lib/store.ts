import { create } from 'zustand'
import { createPersistedState, readPersistedValue } from 'basalt-ui/state'

// ── UI preferences (basalt-ui/state, not Zustand — see basalt-state.md) ────────
//
// Simple standalone booleans that must survive navigation but aren't URL-worthy.
// createPersistedState's returned hook is React-only (useSyncExternalStore), so
// the non-component call sites below (job-controller's SSE handler, notify.ts)
// read the current value via readPersistedValue instead of a store getState().

const SOUND_KEY = 'sound-enabled'
const SOUND_VERSION = 1

/** Play a chime when a job finishes. Default on. */
export const useSoundEnabled = createPersistedState({
  key: SOUND_KEY,
  version: SOUND_VERSION,
  initial: true,
})

/** Non-component read of the current sound preference (SSE event handlers, etc.). */
export function isSoundEnabled(): boolean {
  const v = readPersistedValue(SOUND_KEY, SOUND_VERSION)
  return typeof v === 'boolean' ? v : true
}

const DESKTOP_NOTIFY_KEY = 'desktop-notify-enabled'
const DESKTOP_NOTIFY_VERSION = 1

/** Fire a native OS notification when the tab is backgrounded on job completion. Default off. */
export const useDesktopNotifyEnabled = createPersistedState({
  key: DESKTOP_NOTIFY_KEY,
  version: DESKTOP_NOTIFY_VERSION,
  initial: false,
})

/** Non-component read of the current desktop-notify preference (used by notify.ts). */
export function isDesktopNotifyEnabled(): boolean {
  const v = readPersistedValue(DESKTOP_NOTIFY_KEY, DESKTOP_NOTIFY_VERSION)
  return typeof v === 'boolean' ? v : false
}

/**
 * Active-job store — shared between PipelineHero (Group 10) and Operations (Group 11).
 *
 * Contract for Group 11:
 *   - Call setActiveJob(jobId, op) when an SSE job starts.
 *   - Call clearActiveJob() when the SSE stream emits 'done' or 'error'.
 *   - `op` values: 'import' | 'finalize' | 'cleanup' | 'sync-gallery' | 'backup'
 *
 * PipelineHero reads activeOp to animate the matching edge.
 */
export type ActiveOp = 'import' | 'finalize' | 'cleanup' | 'sync-gallery' | 'backup'

type ActiveJobState = {
  activeJobId: string | null
  activeOp: ActiveOp | null
  setActiveJob: (jobId: string, op: ActiveOp) => void
  clearActiveJob: () => void
  /**
   * Durable terminal state — survives clearActiveJob() and the useJobEvents reset that follows,
   * so the result summary stays visible until the next job starts.
   */
  lastResult: Record<string, unknown> | null
  lastError: string | null
  lastFinishedOp: ActiveOp | null
  setLastResult: (op: ActiveOp, result: Record<string, unknown> | null, error: string | null) => void
}

export const useActiveJobStore = create<ActiveJobState>()((set) => ({
  activeJobId: null,
  activeOp: null,
  // Cleared on new job start so the panel switches to live progress immediately.
  setActiveJob: (jobId, op) =>
    set({ activeJobId: jobId, activeOp: op, lastResult: null, lastError: null, lastFinishedOp: null }),
  clearActiveJob: () => set({ activeJobId: null, activeOp: null }),
  lastResult: null,
  lastError: null,
  lastFinishedOp: null,
  setLastResult: (op, result, error) =>
    set({ lastResult: result, lastError: error, lastFinishedOp: op }),
}))

// ── Live job events store (non-persisted) ──────────────────────────────────────
//
// Mirrors the state produced by useJobEvents' reducer but lives in a shared
// zustand store so any route can read live progress — not just the mounted tab.
// Owned and updated by JobController (mounted once in __root.tsx).

type SseEvent = Record<string, unknown> & { type: string }

type JobLiveState = {
  taskDesc: string | null
  taskTotal: number | null
  taskProgress: number
  logs: Array<{ level: string; message: string; id: number }>
  lastTransfer: { pct?: number; speed?: string; eta?: string; files?: number | string } | null
  lastFileDone: string | null
  isDone: boolean
  error: string | null
  result: Record<string, unknown> | null
  reset: () => void
  applyEvent: (event: SseEvent) => void
}

const liveInitial = {
  taskDesc: null,
  taskTotal: null,
  taskProgress: 0,
  logs: [] as Array<{ level: string; message: string; id: number }>,
  lastTransfer: null,
  lastFileDone: null,
  isDone: false,
  error: null,
  result: null,
}

let liveLogSeq = 0
const MAX_LIVE_LOGS = 80

function lStr(v: unknown): string | undefined {
  return typeof v === 'string' ? v : undefined
}
function lNum(v: unknown): number | undefined {
  return typeof v === 'number' ? v : undefined
}

export const useJobLiveStore = create<JobLiveState>()((set) => ({
  ...liveInitial,

  reset: () => set({ ...liveInitial }),

  applyEvent: (event: SseEvent) => {
    switch (event.type) {
      case 'task':
        set({
          taskDesc: lStr(event['desc']) ?? null,
          taskTotal: lNum(event['total']) ?? null,
          taskProgress: 0,
        })
        break
      case 'advance':
        set((s) => ({ taskProgress: s.taskProgress + (lNum(event['n']) ?? 1) }))
        break
      case 'log': {
        const entry = {
          level: lStr(event['level']) ?? 'info',
          message: lStr(event['message']) ?? '',
          id: ++liveLogSeq,
        }
        set((s) => ({ logs: [...s.logs, entry].slice(-MAX_LIVE_LOGS) }))
        break
      }
      case 'done': {
        const raw = event['result']
        const result =
          raw !== null && typeof raw === 'object' && !Array.isArray(raw)
            ? (raw as Record<string, unknown>)
            : null
        set({ isDone: true, result, error: lStr(event['error']) ?? null })
        break
      }
      case 'file_done':
        set({ lastFileDone: lStr(event['filename']) ?? null })
        break
      case 'transfer': {
        // Build conditionally to satisfy exactOptionalPropertyTypes.
        const t: { pct?: number; speed?: string; eta?: string; files?: number | string } = {}
        const pct = lNum(event['pct'])
        const speed = lStr(event['speed'])
        const eta = lStr(event['eta'])
        if (pct !== undefined) t.pct = pct
        if (speed !== undefined) t.speed = speed
        if (eta !== undefined) t.eta = eta
        const rawFiles = event['files']
        const files = typeof rawFiles === 'number' ? rawFiles : lStr(rawFiles)
        if (files !== undefined) t.files = files
        set({ lastTransfer: t })
        break
      }
    }
  },
}))
