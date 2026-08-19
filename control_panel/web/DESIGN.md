# photo-flow-panel — Design

> Managed by basalt-ui (1.13.0). This is a **thin** instantiation — it records this
> app's **deltas only** on top of the shipped `basalt-*` rules. The universal law (earned color,
> neutral-by-default, three-tier `--vx-*` tokens, theme-is-data, the chart primitive contract, the
> elevation/density/shape doctrine) lives in those rules and the `/basalt:design` skill, and is
> **not** repeated here. Touch this file only to confirm identity, register the app's series, or
> record a genuine deviation.

## Precedence (when guidance conflicts)

This file's deltas win, then the shipped rules, then any skill:

1. **This file** (`photo-flow-panel` DESIGN.md) — app-specific deltas. Highest authority.
2. **`basalt-*` rules** (`.claude/rules/basalt-*.md`) — the shipped law and its enforcement.
3. **Skills** (`/basalt:design`, `/basalt:charts`, `/frontend-design`, …) — generic method, lowest.

A skill never overrides this file or the `basalt-*` rules. When a skill's instinct collides with the
law, the law wins.

## Identity

photo-flow-panel inherits the basalt-ui identity verbatim: modern zinc surfaces, one earned saturated
sky-blue accent, the `shadow-card` / `shadow-raised` / `shadow-overlay` depth split,
dense-by-default spacing, and the three-font system. The
law itself — every hex, role split, and enforcement rule — lives in the `basalt-tokens` and
`basalt-mantine` rules (`.claude/rules/basalt-{tokens,mantine}.md`) and `docs/DESIGN-SPEC.md` in
the basalt-ui repo; it is **not** restated here. Confirm or restate any intentional identity shift
below; **silence means "inherits the basalt-ui defaults unchanged."**

- **Accent hue:** blue (default: the saturated sky accent — `var(--vx-line)` neutral is
  still the default for single-series marks)
- **Tone deltas:** _(none — inherits)_

## Series dictionary

The framework owns the **roles** and the **available hues** (see the `basalt-tokens` rule). This
table is the app's **data dictionary** — which metric maps to which hue, as `{light,dark}` pairs,
wired through `defineSeries()`. This is the one design artifact that legitimately lives in the
consumer; keep it the single source of truth and never inline a hex elsewhere.

Every pair below is expressed as `p(BP.<family>)` against basalt-ui's OWN palette — there is not a
single raw hex in `src/lib/series.ts`, so a basalt palette retune carries these along instead of
stranding a copied hex. The hexes in this table are therefore *derived*, listed for reference only;
`src/lib/series.ts` is the source of truth.

| Series name | Light hex | Dark hex | `defineSeries` key | Role / earned reason |
|-|-|-|-|-|
| Camera | `#d33d17` | `#eb6847` | `camera` | Pipeline stage — warm arrival, kept distinct from Staging gold |
| Staging | `#d1980b` | `#f0b726` | `staging` | Pipeline stage — pending / not yet committed |
| Final | `#0284c7` | `#38bdf8` | `final` | Primary series — the main collection, on the identity accent hue |
| Published | `#29a634` | `#43bf4d` | `published` | Signal — rating ≥ 4, selected for the gallery |
| RAWs | `#147eb3` | `#3fa6da` | `raws` | Categorical — technical, secondary to Final |
| Videos | `#9d3f9d` | `#bd6bbd` | `videos` | Categorical — a distinct media type |
| Rating 1–5 | see `series.ts` | | `rating1…rating5` | Ordered scale — warm (low) to cool (high) |

Wired in `src/lib/series.ts` (the app's guard-exempt series file) under the group `pf`, and handed
to the provider in `src/main.tsx` as `<BasaltProvider paletteOptions={{ groups: paletteGroups }}>`.
Read `PF.final` etc. in charts and chrome — never inline a hex.

Rules for this table (from the `basalt-tokens` / `basalt-charts` rules — do not relax):
- One hue per series, drawn from the identity families only. Never raw Material/AntD/Tailwind.
- A series earns a color only for **trend**, **signal/status**, or **categorical separation**.
  A lone single-series metric stays neutral (`var(--vx-line)`).
- Light is one shade **deeper**, dark one shade **lighter** — same hue, never the same hex.

## App deviations

Genuine, intentional departures from the basalt-ui defaults — each with a one-line justification. An
empty section is the correct default; do not invent deviations to fill it.

- **Dark-first, not dark-only.** `defaultColorScheme="dark"` and an inline anti-FOUC script in
  `index.html` pin dark on first paint. The panel is a background utility that sits open on a
  desktop next to a photo editor; a white flash on load is the one thing it must never do. Light
  mode still works and is not a second-class path.
- **Shell header is 40px, not basalt's dense 48** (`src/styles/shell.css`, `!important` on
  `--app-shell-header-{height,offset}` at the desktop breakpoint; mobile keeps the shipped taller
  stacked header). The panel's primary screen is a photo viewer, where chrome above the image is
  space the photograph does not get — and the header's tallest content box measures 30px, so 40
  still clears it. This is the only place the app moves a shipped shell metric.
- **No destructive operation in the command palette.** ⌘K covers navigation and view toggles only
  (`src/lib/commands.ts`). Every pipeline op runs through a dry-run preview plus a confirm modal —
  a one-keystroke entry is the wrong door for something that deletes camera RAWs.
