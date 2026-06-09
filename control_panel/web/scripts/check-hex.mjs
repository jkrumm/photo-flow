#!/usr/bin/env node
/**
 * check-hex.mjs — enforce no raw hex / rgb() / hsl() in chart implementation + route files.
 *
 * Scans src/lib/charts/ (primitives, kinds, hooks, utils, sparklines) and src/routes/
 * for raw color literals. Fails if any are found outside the palette/token/theme-vars
 * exemption list. Use a `/* theme-allow *\/` comment on the line to explicitly exempt.
 *
 * Files exempt from scanning (they define the color system):
 *   - palette.ts, tokens.ts, theme-vars.ts (by basename)
 *   - theme.ts, charts-bridge.tsx (Mantine theme bridge — allowed to reference BP hex)
 *
 * Scope: only src/lib/charts/ and src/routes/ — Mantine chrome (theme.ts) is exempt
 * because it IS the palette definition boundary, not a chart consumer.
 */

import { readFileSync, readdirSync, statSync } from 'fs'
import { join, relative } from 'path'

const SRC = new URL('../src', import.meta.url).pathname

const SCAN_DIRS = [join(SRC, 'lib', 'charts'), join(SRC, 'routes')]

const EXEMPT_BASENAMES = new Set([
  'palette.ts',
  'tokens.ts',
  'theme-vars.ts',
  'theme.ts',
  'charts-bridge.tsx',
])

// Raw hex: #rgb, #rrggbb, #rrggbbaa (case-insensitive)
const HEX_RE = /#[0-9a-fA-F]{3,8}\b/
// rgb() / hsl() as color values (not CSS property names)
const RGB_HSL_RE = /\b(?:rgb|hsl)a?\s*\(/

function* walk(dir) {
  let entries
  try {
    entries = readdirSync(dir)
  } catch {
    return // dir may not exist yet (e.g. src/routes during initial setup)
  }
  for (const entry of entries) {
    const full = join(dir, entry)
    const st = statSync(full)
    if (st.isDirectory()) {
      yield* walk(full)
    } else if (/\.(ts|tsx)$/.test(entry)) {
      yield full
    }
  }
}

const violations = []

for (const dir of SCAN_DIRS) {
  for (const file of walk(dir)) {
    const basename = file.split('/').pop()
    if (EXEMPT_BASENAMES.has(basename)) continue

    const lines = readFileSync(file, 'utf8').split('\n')
    for (let i = 0; i < lines.length; i++) {
      const line = lines[i]
      const trimmed = line.trim()
      if (line.includes('theme-allow')) continue
      // Skip pure comment lines (// and * in block comments mention patterns without using them)
      if (trimmed.startsWith('//') || trimmed.startsWith('*') || trimmed.startsWith('/*')) continue
      if (HEX_RE.test(line) || RGB_HSL_RE.test(line)) {
        violations.push(`  ${relative(SRC + '/..', file)}:${i + 1}  ${line.trim()}`)
      }
    }
  }
}

if (violations.length > 0) {
  console.error('\n[check-hex] Raw color literals found — use VX.* tokens instead:\n')
  violations.forEach((v) => console.error(v))
  console.error(
    '\nTo exempt a specific line add a `/* theme-allow */` comment on that line.\n',
  )
  process.exit(1)
}

console.log('[check-hex] OK — no raw hex/rgb/hsl in chart/route files.')
