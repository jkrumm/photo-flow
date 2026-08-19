/**
 * One collapsible section of the culling sidebar.
 *
 * **A section is a card, not a slice of a panel.** The sidebar column itself has no background:
 * each section is its own `Paper` — the app's panel surface with `shadow-card`'s ring — sitting
 * on the page. So the chrome occupies exactly as much of the right edge as it has content, the
 * screen ends where the photographs end, and opening a section reads as a card growing rather
 * than as a region of a full-height slab changing shape.
 *
 * That also makes the header a real object without needing a fill of its own: the card is the
 * object. The header carries only the icon, the uppercase label and the chevron, and tints on
 * hover.
 *
 * Two behaviours are load-bearing:
 *
 * - **`summary`** answers "is there anything in here?" while closed (the active root, the
 *   number of live filters), so opening a section is a choice rather than a search.
 * - **The body is capped, not unbounded.** `ScrollArea.Autosize` lets a short section size
 *   to its content and a long one (a date tree, the whole filter stack) stop at
 *   {@link SECTION_MAX_HEIGHT} and scroll inside itself. Without the cap, opening Filters
 *   pushed every section below it off the panel — the sidebar became a single long scroll
 *   and the section list stopped being a list.
 *
 * The open/close is a `motion` height-auto tween rather than Mantine's `Collapse` so the
 * chevron, the height and the fade run on one curve at the framework's own timings; the
 * reduced-motion branch renders a plain unanimated node, not a zero-duration animation.
 */
import type { ReactNode } from 'react'
import { Box, Group, Paper, ScrollArea, Text, UnstyledButton } from '@mantine/core'
import { useReducedMotion } from '@mantine/hooks'
import { IconChevronRight } from '@tabler/icons-react'
import { AnimatePresence, motion } from 'motion/react'
import { MOTION_DURATION, MOTION_EASE_STANDARD } from 'basalt-ui'
import { VX } from 'basalt-ui/tokens'
import classes from './photos-screen.module.css'

/**
 * Height at which a section stops growing and starts scrolling.
 *
 * Viewport-relative on purpose: the cap exists so that two open sections still both fit on
 * screen, which is a question about the window, not about the content.
 */
const SECTION_MAX_HEIGHT = '44vh'

export type SidebarSectionProps = {
  title: string
  /** Leading glyph — the fastest way to re-find a section without reading. */
  icon: ReactNode
  /** Right-aligned one-glance value shown whether the section is open or closed. */
  summary?: ReactNode
  open: boolean
  onToggle: () => void
  children: ReactNode
}

export function SidebarSection({
  title,
  icon,
  summary,
  open,
  onToggle,
  children,
}: SidebarSectionProps) {
  const reducedMotion = useReducedMotion()

  const body = (
    <ScrollArea.Autosize
      className={classes.sectionBody}
      mah={SECTION_MAX_HEIGHT}
      type="hover"
      scrollbars="y"
      scrollbarSize={9}
    >
      <Box px={10} pt={10} pb={10}>
        {children}
      </Box>
    </ScrollArea.Autosize>
  )

  return (
    // `overflow: hidden` clips the header's hover tint and the body to the card's own corners.
    // It sits on the Paper rather than an inner box because the card carries no separate fill
    // layer — and an element's outset shadow is painted outside its border box, so its own
    // `overflow` never reaches it.
    <Paper style={{ overflow: 'hidden' }}>
      <UnstyledButton
        className={classes.sectionHeader}
        data-open={open}
        onClick={onToggle}
        aria-expanded={open}
        px={10}
        py={8}
      >
        <Group gap={8} wrap="nowrap" justify="space-between">
          <Group gap={8} wrap="nowrap" style={{ minWidth: 0 }}>
            <Box className={classes.chevron} data-open={open}>
              <IconChevronRight size={13} />
            </Box>
            <Box style={{ lineHeight: 0, color: open ? VX.ink : VX.muted }}>{icon}</Box>
            <Text fz={VX.text.xs} fw={700} tt="uppercase" c={open ? VX.ink : VX.muted}>
              {title}
            </Text>
          </Group>
          {summary}
        </Group>
      </UnstyledButton>

      {reducedMotion ? (
        open && body
      ) : (
        <AnimatePresence initial={false}>
          {open && (
            <motion.div
              key="body"
              initial={{ height: 0, opacity: 0 }}
              animate={{ height: 'auto', opacity: 1 }}
              exit={{ height: 0, opacity: 0 }}
              transition={{ duration: MOTION_DURATION.fast, ease: MOTION_EASE_STANDARD }}
              // theme-allow: the wrapper must clip its own content while the height tweens —
              // this is the animation's clip box, not a chrome column that wants a ScrollArea.
              style={{ overflow: 'hidden' }}
            >
              {body}
            </motion.div>
          )}
        </AnimatePresence>
      )}
    </Paper>
  )
}
