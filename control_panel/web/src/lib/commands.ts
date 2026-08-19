/**
 * Command palette registry (⌘K) — navigation and view-level toggles only.
 *
 * Deliberately NO pipeline operations here: every mutating op goes through a dry-run preview and a
 * confirm modal, and a one-keystroke palette entry is the wrong door for something that deletes
 * camera RAWs. The palette moves you around; the Operations screen runs things.
 */
import type { AnyRouter } from '@tanstack/react-router'
import { defineCommands } from 'basalt-ui/commands'

export function registerCommands(router: AnyRouter, toggleColorScheme: () => void) {
  const go = (to: string) => () => {
    void router.navigate({ to })
  }

  const COMMANDS = defineCommands({
    'go:pipeline': {
      label: 'Go to Pipeline',
      group: 'Navigate',
      shortcut: 'Mod+1',
      run: go('/pipeline'),
    },
    'go:photos': {
      label: 'Go to Photos',
      group: 'Navigate',
      shortcut: 'Mod+2',
      run: go('/photos'),
    },
    'go:operations': {
      label: 'Go to Operations',
      group: 'Navigate',
      shortcut: 'Mod+3',
      run: go('/operations'),
    },
    'go:analytics': {
      label: 'Go to Analytics',
      group: 'Navigate',
      shortcut: 'Mod+4',
      run: go('/analytics'),
    },
    'go:library': {
      label: 'Go to Library Health',
      group: 'Navigate',
      shortcut: 'Mod+5',
      run: go('/library'),
    },
    'view:toggle-scheme': {
      label: 'Toggle color scheme',
      group: 'View',
      run: toggleColorScheme,
    },
  })

  return COMMANDS
}

declare module 'basalt-ui' {
  interface BasaltRegister {
    commands: ReturnType<typeof registerCommands>
  }
}
