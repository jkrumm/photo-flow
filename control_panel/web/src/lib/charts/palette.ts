/**
 * palette.ts — Blueprint-derived color system for photo-flow charts + UI chrome.
 *
 * Vendored from argo/packages/charts/src/palette.ts and extended with
 * photo-flow pipeline series colors. Pure data — no React, no Mantine, no browser APIs.
 */

/** Blueprint v6 palette. Each family is shade 1→5 (index 0 = darkest, 4 = lightest). */
export const BP = {
  black: '#111418',
  white: '#ffffff',
  darkGray: ['#1c2127', '#252a31', '#2f343c', '#383e47', '#404854'],
  gray: ['#5f6b7c', '#738091', '#8f99a8', '#abb3bf', '#c5cbd3'],
  lightGray: ['#d3d8de', '#dce0e5', '#e5e8eb', '#edeff2', '#f6f7f9'],

  blue: ['#184a90', '#215db0', '#2d72d2', '#4c90f0', '#8abbff'],
  green: ['#165a36', '#1c6e42', '#238551', '#32a467', '#72ca9b'],
  orange: ['#77450d', '#935610', '#c87619', '#ec9a3c', '#fbb360'],
  red: ['#8e292c', '#ac2f33', '#cd4246', '#e76a6e', '#fa999c'],
  vermilion: ['#96290d', '#b83211', '#d33d17', '#eb6847', '#ff9980'],
  rose: ['#a82255', '#c22762', '#db2c6f', '#f5498b', '#ff66a1'],
  violet: ['#5c255c', '#7c327c', '#9d3f9d', '#bd6bbd', '#d69fd6'],
  indigo: ['#5642a6', '#634dbf', '#7961db', '#9881f3', '#bdadff'],
  cerulean: ['#0c5174', '#0f6894', '#147eb3', '#3fa6da', '#68c1ee'],
  turquoise: ['#004d46', '#007067', '#00a396', '#13c9ba', '#7ae1d8'],
  forest: ['#1d7324', '#238c2c', '#29a634', '#43bf4d', '#62d96b'],
  lime: ['#43501b', '#5a701a', '#8eb125', '#b6d94c', '#d4f17e'],
  gold: ['#5c4405', '#866103', '#d1980b', '#f0b726', '#fbd065'],
  sepia: ['#5e4123', '#7a542e', '#946638', '#af855a', '#d0b090'],
} as const

export type ColorPair = { light: string; dark: string }

const p = (fam: readonly string[], light = 2, dark = 3): ColorPair => ({
  light: fam[light]!,
  dark: fam[dark]!,
})

/** Photo-flow pipeline series — stage identity colors. */
export const PHOTO = {
  /** Camera / import — vermilion (warm arrival; kept distinct from Staging gold) */
  camera: p(BP.vermilion),
  /** Staging — gold (pending) */
  staging: p(BP.gold),
  /** Final — blue (identity anchor, the main collection) */
  final: p(BP.blue),
  /** Published (rating ≥ 4) — forest green (selected, worthy) */
  published: p(BP.forest),
  /** RAWs — cerulean (technical, secondary) */
  raws: p(BP.cerulean),
  /** Videos — violet (distinct type) */
  videos: p(BP.violet),

  /** Rating distribution (1-5 shades from warm to cool) */
  rating1: p(BP.red, 3, 4),
  rating2: p(BP.vermilion, 3, 4),
  rating3: p(BP.gold, 2, 3),
  rating4: p(BP.forest, 2, 3),
  rating5: p(BP.blue, 2, 3),
} as const

export const SEMANTIC = {
  good: p(BP.forest),
  bad: p(BP.red),
  warn: p(BP.gold),
} as const

export const NEUTRAL = {
  line: { light: BP.gray[0], dark: BP.gray[4] },
  line2: { light: BP.gray[1], dark: BP.gray[2] },
  axis: { light: 'rgba(17,20,24,0.6)', dark: 'rgba(255,255,255,0.6)' },
  axisStroke: { light: 'rgba(17,20,24,0.12)', dark: 'rgba(255,255,255,0.12)' },
  grid: { light: 'rgba(17,20,24,0.07)', dark: 'rgba(255,255,255,0.06)' },
  crosshair: { light: 'rgba(17,20,24,0.32)', dark: 'rgba(255,255,255,0.42)' },
  dotStroke: { light: BP.white, dark: BP.darkGray[1] },
  tooltipBg: { light: BP.white, dark: 'rgba(28,33,39,0.96)' },
  tooltipText: { light: 'rgba(17,20,24,0.88)', dark: 'rgba(255,255,255,0.88)' },
  tooltipMuted: { light: 'rgba(17,20,24,0.5)', dark: 'rgba(255,255,255,0.5)' },
  tooltipBorder: { light: `1px solid ${BP.lightGray[1]}`, dark: 'none' },
  tooltipShadow: { light: '0 2px 8px rgba(17,20,24,0.1)', dark: '0 2px 8px rgba(0,0,0,0.35)' },
  neutral: { light: BP.gray[0], dark: BP.gray[2] },
} as const

export const STATUS = {
  excellent: p(BP.forest),
  good: p(BP.lime, 2, 3),
  warn: p(BP.gold),
  bad: p(BP.vermilion),
  neutral: p(BP.gray, 1, 2),
} as const

export const SURFACE = {
  bg: { light: BP.lightGray[4], dark: BP.darkGray[0] },
  panel: { light: BP.white, dark: BP.darkGray[1] },
  elevated: { light: BP.white, dark: BP.darkGray[2] },
  border: { light: BP.lightGray[1], dark: BP.darkGray[3] },
} as const
