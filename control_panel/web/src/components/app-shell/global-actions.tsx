import { StatusIndicator } from './status-indicator'

/**
 * Global slot of the app-shell top bar — shell-owned and persistent across routes.
 * Photo-flow: shows device connection status + staging count.
 */
export function GlobalActions({ className }: { className?: string }) {
  return (
    <div className={className}>
      <StatusIndicator />
    </div>
  )
}
