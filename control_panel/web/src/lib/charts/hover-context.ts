// Vendored from argo/packages/charts — keep in sync manually.
import { createContext } from 'react'

export type HoverCtx = {
  date: string | null
  source: string | null
  setHover: (date: string | null, source: string | null) => void
}

export const DEFAULT_NO_OP_SET_HOVER: HoverCtx['setHover'] = () => {}

export const HoverContext = createContext<HoverCtx>({
  date: null,
  source: null,
  setHover: DEFAULT_NO_OP_SET_HOVER,
})
