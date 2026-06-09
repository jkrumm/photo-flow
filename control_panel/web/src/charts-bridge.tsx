import { useMantineColorScheme } from '@mantine/core'
import { VxThemeProvider, PALETTE_CSS } from './lib/charts'
import type { ReactNode } from 'react'

/**
 * Single bridge between Mantine's color scheme and the @pf/charts token system.
 * This is the ONLY file allowed to import both @mantine/* and @pf/charts.
 *
 * Injects the chart palette CSS variables once. They key off Mantine's
 * [data-mantine-color-scheme] attribute, so dark/light resolution is pure CSS.
 */
export function VxBridge({ children }: { children: ReactNode }) {
  const { colorScheme } = useMantineColorScheme()
  const resolved = colorScheme === 'auto' ? 'dark' : colorScheme
  return (
    <VxThemeProvider colorScheme={resolved}>
      <style>{PALETTE_CSS}</style>
      {children}
    </VxThemeProvider>
  )
}
