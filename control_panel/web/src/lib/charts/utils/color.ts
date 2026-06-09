// Vendored from argo/packages/charts — keep in sync manually.

/**
 * Apply opacity to any palette token, theme-aware. Use instead of raw rgba() so the
 * underlying hue still resolves per color scheme.
 */
export const alpha = (token: string, a: number): string =>
  `color-mix(in srgb, ${token} ${Math.round(a * 100)}%, transparent)`
