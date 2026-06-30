/**
 * API types — hand-crafted snapshot matching photo_flow/api/ Pydantic models.
 *
 * Regenerate from a live server with:
 *   npm run gen:api   (requires photoflow serve running on :7717)
 *
 * Keep this in sync with photo_flow/api/routes_*.py when Pydantic models change.
 */

// ── Status ───────────────────────────────────────────────────────────────────

export type StatusResponse = {
  camera_connected: boolean
  ssd_connected: boolean
  staging_files: number
}

export type PendingResponse = {
  pending_videos: number
  pending_photos: number
  pending_raws: number
}

// ── Jobs ─────────────────────────────────────────────────────────────────────

export type JobStatus = 'queued' | 'running' | 'done' | 'failed' | 'cancelled' | 'needs_confirm'

/** Response from POST /ops/* — job is always admitted to the FIFO queue. */
export type JobStarted = {
  job_id: string
  /** Always 'queued'; the worker transitions it to 'running' asynchronously. */
  status: 'queued'
  /** 0-based position in the queue (0 = next to run). */
  position: number
}

/** One entry in the GET /jobs list. */
export type JobQueueItem = {
  job_id: string
  op: string
  status: JobStatus
  seq: number
  position: number
  result: Record<string, unknown> | null
  error: string | null
  fresh_preview: Record<string, unknown> | null
}

export type JobListResponse = {
  jobs: JobQueueItem[]
}

export type JobResponse = {
  job_id: string
  op: string
  status: JobStatus
  result: Record<string, unknown> | null
  error: string | null
  started_at: number | null
  finished_at: number | null
}

// ── SSE event payloads ────────────────────────────────────────────────────────

export type SseEventType =
  | 'task'
  | 'advance'
  | 'log'
  | 'event'
  | 'done'
  | 'error'

export type SseEvent = {
  type: SseEventType
  /** task description (type=task) */
  description?: string
  total?: number
  /** advance amount (type=advance) */
  n?: number
  /** log level (type=log): info | warning | error */
  level?: string
  message?: string
  /** event subtype (type=event) */
  event_type?: string
  payload?: Record<string, unknown>
  /** op result dict (type=done) */
  result?: Record<string, unknown>
  /** error message (type=error) */
  error?: string
}

// ── Operation results ─────────────────────────────────────────────────────────

export type ImportResult = {
  videos: number
  photos: number
  raws: number
  skipped: number
  errors: number
}

export type FinalizeResult = {
  moved: number
  edits_moved: number
  orphaned_raws: number
  deleted_raws: number
  deleted_camera_raws: number
  skipped: number
  errors: number
}

export type CleanupResult = {
  orphaned: number
  deleted: number
  errors: number
}

export type SyncGalleryResult = {
  scanned: number
  synced: number
  removed: number
  skipped: number
  unchanged: number
  errors: number
  json_updated: boolean
  total_in_gallery: number
  build_successful: boolean | null
  sync_successful: boolean | null
}

export type BackupResult = {
  source: string
  scanned: number
  sync_successful: boolean
  connection_method: string | null
  trash_path: string | null
  errors: number
  immich_scan_triggered: boolean | null
}

export type BackupAllResult = {
  sources: string[]
  total_scanned: number
  all_successful: boolean
  errors: number
}

export type BackupSourceInfo = {
  available: boolean
  local_count: number
  path: string | null
  remote_path: string | null
  extension: string | null
  remote_count: number | null
  needs_sync: number | null
  requires: string | null
}

export type BackupAvailabilityResponse = {
  final: BackupSourceInfo
  raws: BackupSourceInfo
  videos: BackupSourceInfo
  connection: string | null
}

export type BackupSource = 'final' | 'raws' | 'videos' | 'all'

// ── Gallery ───────────────────────────────────────────────────────────────────

export type GallerySyncStatusResponse = {
  target: number
  current: number
  pending: number
  up_to_date: boolean
}

// ── Analytics ─────────────────────────────────────────────────────────────────

export type BucketGrain = 'day' | 'week' | 'month' | 'year'

export type BucketPoint = {
  x: string
  total: number
  high_rated: number
  other: number
}

export type RatingHistogramEntry = {
  rating: number | null
  count: number
}

export type RatingsResponse = {
  histogram: RatingHistogramEntry[]
  total_final: number
  total_published: number
}

export type SettingsEntry = {
  value: number | null
  count: number
  label?: string
}

export type SettingsResponse = {
  iso: SettingsEntry[]
  aperture: SettingsEntry[]
  focal: SettingsEntry[]
  shutter: SettingsEntry[]
}

export type StageStorage = {
  count: number
  bytes: number
  available: boolean
}

export type StorageResponse = {
  final: StageStorage
  staging: StageStorage
  raws: StageStorage
  videos: StageStorage
}

export type MapPoint = {
  lat: number
  lng: number
  filename: string
  date_taken: string | null
}

export type SummaryResponse = {
  total_photos: number
  total_published: number
  this_month_count: number
  avg_rating: number | null
  earliest_date: string | null
  latest_date: string | null
}

export type RefreshResponse = {
  indexed: number
  updated: number
  skipped: number
  removed: number
}

export type LibraryHealthResponse = {
  orphaned_raws: number | null
  raws_available: boolean
  final_count: number
  raws_count: number
}

// ── Pipeline ──────────────────────────────────────────────────────────────────

export type LastRun = { ts: string | null; ok: boolean } | null

export type PipelineStatus = {
  camera_connected: boolean
  ssd_connected: boolean
  staging_files: number
  pending_photos: number
  pending_videos: number
  pending_raws: number
  final_count: number
  /** Final JPGs with rating ≥ 4 that are not yet in the gallery. */
  unpublished_high_rated: number
  orphaned_raws: number
  last_runs: {
    import: LastRun
    finalize: LastRun
    'sync-gallery': LastRun
    backup: LastRun
    cleanup: LastRun
  }
}
