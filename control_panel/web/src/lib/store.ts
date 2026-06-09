import { create } from 'zustand'
import { persist } from 'zustand/middleware'

type UiState = {
  sidebarCollapsed: boolean
  setSidebarCollapsed: (v: boolean) => void
  toggleSidebar: () => void
}

export const useUiStore = create<UiState>()(
  persist(
    (set) => ({
      sidebarCollapsed: false,
      setSidebarCollapsed: (v) => set({ sidebarCollapsed: v }),
      toggleSidebar: () => set((s) => ({ sidebarCollapsed: !s.sidebarCollapsed })),
    }),
    { name: 'photoflow-ui' },
  ),
)

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
}

export const useActiveJobStore = create<ActiveJobState>()((set) => ({
  activeJobId: null,
  activeOp: null,
  setActiveJob: (jobId, op) => set({ activeJobId: jobId, activeOp: op }),
  clearActiveJob: () => set({ activeJobId: null, activeOp: null }),
}))
