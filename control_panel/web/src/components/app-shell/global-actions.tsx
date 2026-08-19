import { Divider, Group } from '@mantine/core'
import { ThemeToggle } from 'basalt-ui'
import { NotificationBell } from 'basalt-ui/notifications'
import { JobProgressPill } from './job-progress-pill'
import { NotificationSettings } from './notification-settings'
import { StatusIndicator } from './status-indicator'

/**
 * Persistent, shell-owned top-bar slot — rendered via BasaltShell's `globalActions` prop.
 * Photo-flow domain pieces (device status, job progress, sound/OS-notification preferences)
 * alongside basalt-ui's theme toggle and notification bell.
 */
export function GlobalActions() {
  return (
    <Group gap="xs" wrap="nowrap">
      <StatusIndicator />
      <JobProgressPill />
      <NotificationSettings />
      <Divider orientation="vertical" visibleFrom="sm" style={{ height: 24 }} />
      <ThemeToggle />
      <NotificationBell />
    </Group>
  )
}
