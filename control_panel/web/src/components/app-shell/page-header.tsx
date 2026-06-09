import { createContext, useContext, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'

/**
 * Page-header slot system — portal-based control row for the app shell top bar.
 * The active route portals its actions here via <PageActions>. Adapted from argo.
 */

const TargetContext = createContext<HTMLElement | null>(null)
const SetTargetContext = createContext<(el: HTMLElement | null) => void>(() => {})

export function PageHeaderProvider({ children }: { children: ReactNode }) {
  const [target, setTarget] = useState<HTMLElement | null>(null)
  return (
    <SetTargetContext.Provider value={setTarget}>
      <TargetContext.Provider value={target}>{children}</TargetContext.Provider>
    </SetTargetContext.Provider>
  )
}

export function PageActionsOutlet({ className }: { className?: string }) {
  const setTarget = useContext(SetTargetContext)
  return <div ref={setTarget} className={className} />
}

export function PageActions({ children }: { children: ReactNode }) {
  const target = useContext(TargetContext)
  return target ? createPortal(children, target) : null
}
