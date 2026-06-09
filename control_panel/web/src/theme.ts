/**
 * Mantine theme reskinned to Blueprint palette so UI chrome and charts share one identity.
 *
 * Adapted from argo/apps/dashboard/src/theme.ts.
 * BP lives in src/lib/charts/palette.ts — importing the pure data is allowed.
 */
import {
  Card,
  createTheme,
  type CSSVariablesResolver,
  Input,
  NumberInput,
  Paper,
  PasswordInput,
  Select,
  Textarea,
  TextInput,
} from '@mantine/core'
import { BP } from './lib/charts/palette'

function hexToRgb(h: string): [number, number, number] {
  const n = Number.parseInt(h.slice(1), 16)
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255]
}
function mix(a: string, b: string, t: number): string {
  const A = hexToRgb(a)
  const B = hexToRgb(b)
  const c = A.map((v, i) => Math.round(v + (B[i]! - v) * t))
  return `#${c.map((v) => v.toString(16).padStart(2, '0')).join('')}`
}
function ramp10(stops: readonly string[]): [string, string, string, string, string, string, string, string, string, string] {
  const lite = stops.toReversed()
  const out: string[] = []
  for (let i = 0; i < 10; i++) {
    const pos = (i / 9) * (lite.length - 1)
    const lo = Math.floor(pos)
    const hi = Math.min(lo + 1, lite.length - 1)
    out.push(mix(lite[lo]!, lite[hi]!, pos - lo))
  }
  out[0] = mix(out[0]!, '#ffffff', 0.5)
  return out as [string, string, string, string, string, string, string, string, string, string]
}

const bpDark: [string, string, string, string, string, string, string, string, string, string] = [
  '#c5cbd3',
  '#abb3bf',
  '#8f99a8',
  '#738091',
  '#383e47',
  '#2f343c',
  '#252a31',
  '#1c2127',
  '#181c22',
  '#111418',
]

export const theme = createTheme({
  primaryColor: 'blue',
  primaryShade: { light: 6, dark: 4 },
  autoContrast: true,
  luminanceThreshold: 0.45,
  white: '#ffffff',
  black: '#111418',
  defaultRadius: 'sm',
  fontFamilyMonospace: "ui-monospace, 'SF Mono', Menlo, monospace",
  fontWeights: { normal: '400', medium: '500', semibold: '600', bold: '700' },
  spacing: { xs: '0.625rem', sm: '0.75rem', md: '1rem', lg: '1.25rem', xl: '2rem' },
  radius: { xs: '0.125rem', sm: '0.25rem', md: '0.5rem', lg: '1rem', xl: '2rem' },
  colors: {
    dark: bpDark,
    gray: ramp10(BP.gray),
    blue: ramp10(BP.blue),
    cyan: ramp10(BP.cerulean),
    teal: ramp10(BP.turquoise),
    green: ramp10(BP.forest),
    lime: ramp10(BP.lime),
    yellow: ramp10(BP.gold),
    orange: ramp10(BP.orange),
    red: ramp10(BP.red),
    pink: ramp10(BP.rose),
    grape: ramp10(BP.violet),
    violet: ramp10(BP.violet),
    indigo: ramp10(BP.indigo),
  },
  components: {
    Card: Card.extend({ defaultProps: { withBorder: true, radius: 'md' } }),
    Paper: Paper.extend({ defaultProps: { withBorder: true } }),
    Input: Input.extend({ defaultProps: { size: 'md' } }),
    TextInput: TextInput.extend({ defaultProps: { size: 'md' } }),
    NumberInput: NumberInput.extend({ defaultProps: { size: 'md' } }),
    PasswordInput: PasswordInput.extend({ defaultProps: { size: 'md' } }),
    Select: Select.extend({ defaultProps: { size: 'md' } }),
    Textarea: Textarea.extend({ defaultProps: { size: 'md' } }),
  },
})

export const cssVariablesResolver: CSSVariablesResolver = () => ({
  variables: {},
  light: {
    '--mantine-color-body': 'var(--vx-surface-bg, #f6f7f9)',
    '--mantine-color-default': 'var(--vx-surface-panel, #ffffff)',
    '--mantine-color-default-hover': 'var(--vx-surface-elevated, #ffffff)',
    '--mantine-color-default-border': 'var(--vx-surface-border, #dce0e5)',
    '--mantine-color-dimmed': 'var(--vx-neutral, #5f6b7c)',
  },
  dark: {
    '--mantine-color-body': 'var(--vx-surface-bg, #1c2127)',
    '--mantine-color-default': 'var(--vx-surface-panel, #252a31)',
    '--mantine-color-default-hover': 'var(--vx-surface-elevated, #2f343c)',
    '--mantine-color-default-border': 'var(--vx-surface-border, #383e47)',
    '--mantine-color-dimmed': 'var(--vx-neutral, #8f99a8)',
  },
})
