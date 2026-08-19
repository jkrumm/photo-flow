/**
 * The app's series dictionary — which pipeline stage / rating maps to which hue.
 *
 * This is the one design artifact that legitimately lives in the consumer (see DESIGN.md).
 * Every pair is drawn from basalt-ui's own palette families via `p()`, so there is not a single
 * raw hex here and the hues track a basalt palette retune automatically.
 *
 * `GROUP` feeds BOTH `groupTokens` and `BasaltProvider`'s `paletteOptions.groups` (keyed by CSS-var
 * PREFIX, hence the trailing dash) — deriving both from one constant is what keeps them in lockstep;
 * drift there emits `var(--vx-…)` refs the palette stylesheet never declares (silent, unstyled charts).
 */
import { BP, defineSeries, groupTokens, p } from 'basalt-ui/tokens'

export const GROUP = 'pf'

const SERIES = defineSeries({
  /** Camera / import — vermilion (warm arrival; kept distinct from Staging gold) */
  camera: p(BP.vermilion),
  /** Staging — gold (pending) */
  staging: p(BP.gold),
  /** Final — the identity accent; the main collection is the primary series */
  final: p(BP.blue),
  /** Published (rating ≥ 4) — forest green (selected, worthy) */
  published: p(BP.forest),
  /** RAWs — cerulean (technical, secondary) */
  raws: p(BP.cerulean),
  /** Videos — violet (distinct type) */
  videos: p(BP.violet),

  /** Rating distribution — warm (low) to cool (high) */
  rating1: p(BP.red, 3, 4),
  rating2: p(BP.vermilion, 3, 4),
  rating3: p(BP.gold),
  rating4: p(BP.forest),
  rating5: p(BP.blue),
})

/** `var(--vx-pf-*)` refs — read these in charts and chrome alike. */
export const PF = groupTokens(GROUP, SERIES)

/** Hand to `<BasaltProvider paletteOptions={{ groups: paletteGroups }}>`. */
export const paletteGroups = { [`${GROUP}-`]: SERIES }
