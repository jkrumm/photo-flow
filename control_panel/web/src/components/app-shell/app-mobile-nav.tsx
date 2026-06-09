import { Text, UnstyledButton } from '@mantine/core'
import type { MouseEvent, ReactNode } from 'react'
import classes from './app-mobile-nav.module.css'

export type MobileNavItem = {
  key: string
  short: string
  icon: ReactNode
  href?: string
  active?: boolean
  onClick?: (e: MouseEvent) => void
}

export function MobileNav({ items }: { items: MobileNavItem[] }) {
  return (
    <nav className={classes.bar}>
      {items.map((item) => {
        const inner = (
          <>
            {item.icon}
            <Text className={classes.label}>{item.short}</Text>
          </>
        )
        return item.href ? (
          <UnstyledButton
            key={item.key}
            component="a"
            href={item.href}
            onClick={item.onClick}
            className={classes.tab}
            data-active={item.active ?? undefined}
            aria-label={item.short}
          >
            {inner}
          </UnstyledButton>
        ) : (
          <UnstyledButton
            key={item.key}
            onClick={item.onClick}
            className={classes.tab}
            data-active={item.active ?? undefined}
            aria-label={item.short}
          >
            {inner}
          </UnstyledButton>
        )
      })}
    </nav>
  )
}
