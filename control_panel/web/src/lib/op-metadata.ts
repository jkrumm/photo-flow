import type { ActiveOp } from './store'
import type { BackupSource } from './api-types'

export type OpMeta = {
  label: string
  destructive: boolean
}

export const OP_META: Record<ActiveOp, OpMeta> = {
  import: { label: 'Import', destructive: true },
  finalize: { label: 'Finalize', destructive: true },
  cleanup: { label: 'Cleanup RAWs', destructive: true },
  'sync-gallery': { label: 'Sync Gallery', destructive: false },
  backup: { label: 'Backup', destructive: false },
}

/** Human label for each op — consumed by PipelineHero, DryRunModal, and notify. */
export const OP_LABELS: Record<ActiveOp, string> = {
  import: 'Import',
  finalize: 'Finalize',
  cleanup: 'Cleanup RAWs',
  'sync-gallery': 'Sync Gallery',
  backup: 'Backup',
}

export const BACKUP_SOURCES: { value: BackupSource; label: string }[] = [
  { value: 'all', label: 'All sources' },
  { value: 'final', label: 'Final' },
  { value: 'raws', label: 'RAWs' },
  { value: 'videos', label: 'Videos' },
]

/** Dry-run preview field labels (DryRunModal). */
export const FIELD_LABELS: Record<string, string> = {
  photos: 'Photos',
  raws: 'RAWs',
  videos: 'Videos',
  skipped: 'Skipped (duplicates)',
  errors: 'Errors',
  moved: 'Photos to move',
  edits_moved: 'Sidecars to move',
  orphaned_raws: 'Orphaned RAWs',
  deleted_raws: 'RAWs to delete',
  deleted_camera_raws: 'Camera RAWs to delete',
  orphaned: 'Orphaned RAWs',
  deleted: 'To delete',
  scanned: 'Files scanned',
  synced: 'Photos to sync',
  removed: 'Photos to remove',
  unchanged: 'Unchanged',
  total_in_gallery: 'Total in gallery',
  json_updated: 'Metadata JSON updated',
  build_successful: 'Build successful',
  sync_successful: 'Sync successful',
  source: 'Source',
  sources: 'Sources',
  total_scanned: 'Total files scanned',
  all_successful: 'All successful',
  connection_method: 'Connection',
  immich_scan_triggered: 'Immich scan triggered',
  trash_path: 'Trash path',
}

/** Result summary field labels (JobProgressPanel). */
export const RESULT_LABELS: Record<string, string> = {
  photos: 'Photos imported',
  raws: 'RAWs imported',
  videos: 'Videos imported',
  skipped: 'Skipped (duplicates)',
  moved: 'Photos moved to Final',
  edits_moved: 'Sidecars moved',
  orphaned_raws: 'Orphaned RAWs found',
  deleted_raws: 'RAWs deleted',
  deleted_camera_raws: 'Camera RAWs deleted',
  orphaned: 'Orphaned RAWs found',
  deleted: 'RAWs deleted',
  scanned: 'Files scanned',
  synced: 'Photos synced',
  removed: 'Photos removed from gallery',
  unchanged: 'Unchanged',
  total_in_gallery: 'Total in gallery',
  build_successful: 'Build',
  sync_successful: 'Remote sync',
  total_scanned: 'Total files synced',
  all_successful: 'All sources succeeded',
  errors: 'Errors',
}
