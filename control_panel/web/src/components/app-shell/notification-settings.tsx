import { useState } from 'react'
import { ActionIcon, Button, Divider, Popover, Stack, Switch, Text, Tooltip } from '@mantine/core'
import { IconBell } from '@tabler/icons-react'
import { useDesktopNotifyEnabled, useSoundEnabled } from '../../lib/store'
import { resumeAudio, playSuccess } from '../../lib/sound'

// Moved to module scope: captures no component state, so recreating on every
// render would be wasteful and triggers the consistent-function-scoping lint rule.
function testChime() {
  resumeAudio() // unblock AudioContext on the user gesture
  playSuccess()
}

/**
 * Notification settings popover — bell icon in the app header.
 *
 * Persisted state lives in basalt-ui/state's createPersistedState (src/lib/store.ts):
 *   useSoundEnabled — play a chime on job completion.
 *   useDesktopNotifyEnabled — fire an OS notification
 *     when the tab is backgrounded (requires Notification permission).
 *
 * Enabling desktop notifications calls Notification.requestPermission() inside
 * the click handler (user gesture required by the browser). The toggle only
 * activates if the user grants permission; denied state is reflected in the UI.
 *
 * "Test chime" unblocks the AudioContext (resumeAudio) and plays the success
 * chime — useful to verify the sound volume before a real job finishes.
 */
export function NotificationSettings() {
  const [soundEnabled, setSoundEnabled] = useSoundEnabled()
  const [desktopNotifyEnabled, setDesktopNotifyEnabled] = useDesktopNotifyEnabled()

  // Track permission status locally so we can re-render after a requestPermission call.
  const [permStatus, setPermStatus] = useState<NotificationPermission>(
    typeof Notification !== 'undefined' ? Notification.permission : 'default',
  )

  const denied = permStatus === 'denied'

  const handleDesktopToggle = (checked: boolean) => {
    if (!checked) {
      setDesktopNotifyEnabled(false)
      return
    }
    if (typeof Notification === 'undefined') return
    if (Notification.permission === 'granted') {
      setDesktopNotifyEnabled(true)
      return
    }
    if (Notification.permission === 'default') {
      // requestPermission must be called inside a user-gesture handler.
      void Notification.requestPermission().then((result) => {
        setPermStatus(result)
        if (result === 'granted') setDesktopNotifyEnabled(true)
        return result
      })
    }
    // If 'denied': nothing to do — browser blocks re-requesting.
  }

  return (
    <Popover width={220} position="bottom-end" withArrow shadow="md">
      <Popover.Target>
        <Tooltip label="Notification settings" withArrow openDelay={500}>
          <ActionIcon variant="subtle" size="sm" aria-label="Notification settings">
            <IconBell size={16} />
          </ActionIcon>
        </Tooltip>
      </Popover.Target>
      <Popover.Dropdown>
        <Stack gap="sm">
          <Text size="xs" fw={600} tt="uppercase" c="dimmed" style={{ letterSpacing: '0.06em' }}>
            Job notifications
          </Text>

          <Switch
            size="xs"
            label="Sound"
            description="Chime when a job finishes"
            checked={soundEnabled}
            onChange={() => setSoundEnabled(!soundEnabled)}
          />

          <Switch
            size="xs"
            label="Desktop notifications"
            description={
              denied
                ? 'Permission blocked — allow in browser settings'
                : 'OS alert when tab is in background'
            }
            checked={desktopNotifyEnabled && !denied}
            disabled={denied}
            onChange={(e) => handleDesktopToggle(e.currentTarget.checked)}
          />

          <Divider />

          <Button
            size="compact-xs"
            variant="light"
            disabled={!soundEnabled}
            onClick={testChime}
          >
            Test chime
          </Button>
        </Stack>
      </Popover.Dropdown>
    </Popover>
  )
}
