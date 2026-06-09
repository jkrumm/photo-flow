// Vendored from argo/packages/charts — keep in sync manually.
import { VX } from '../tokens'

/** Pick evenly-spaced tick values that fit the available width */
export function smartTicks(dates: string[], xMax: number): string[] {
  if (dates.length === 0) return []
  const maxTicks = Math.max(2, Math.floor(xMax / VX.minPxPerTick))
  if (dates.length <= maxTicks) return dates
  const step = Math.ceil(dates.length / maxTicks)
  return dates.filter((_, i) => i % step === 0 || i === dates.length - 1)
}
