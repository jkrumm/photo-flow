import { createRootRouteWithContext, Link, Outlet } from '@tanstack/react-router'
import type { QueryClient } from '@tanstack/react-query'
import { NavLink as MantineNavLink } from '@mantine/core'
import { useHotkeys } from '@mantine/hooks'
import { IconCamera, IconChartHistogram, IconHeartbeat, IconPhoto } from '@tabler/icons-react'
import { BasaltShell, type NavLinkRenderer, type SidebarSection } from 'basalt-ui'
import { useBasaltNav } from 'basalt-ui/router-tanstack'
import { createPersistedState } from 'basalt-ui/state'
import { GlobalActions } from '../components/app-shell/global-actions'
import { JobController } from '../components/app-shell/job-controller'

export const Route = createRootRouteWithContext<{ queryClient: QueryClient }>()({
  component: RootLayout,
})

const ICON = 18

/**
 * Wires the theme's NavLink defaults (--nl-* vars, see basalt-mantine.md) through TanStack's
 * typed <Link> on every render path — desktop sidebar, hover popover, and the mobile drawer.
 * Module scope: doesn't depend on component state, so it's created once, not per render.
 */
const renderNavLink: NavLinkRenderer = (item, opts) => (
  <MantineNavLink
    component={Link}
    to={item.href ?? '/'}
    label={item.label}
    leftSection={item.icon}
    rightSection={item.badge}
    active={opts.active}
    onClick={() => opts.close?.()}
  />
)

/**
 * Desktop sidebar collapse — controlled (not BasaltShell's default internal storage) so the
 * mod+B hotkey below can drive it, per BasaltShellProps.onCollapsedChange's own documented use.
 */
const useSidebarCollapsed = createPersistedState({
  key: 'sidebar-collapsed',
  version: 1,
  initial: false,
})

function RootLayout() {
  const { isActive } = useBasaltNav()
  const [collapsed, setCollapsed] = useSidebarCollapsed()

  useHotkeys([['mod+B', () => setCollapsed(!collapsed)]])

  const sections: SidebarSection[] = [
    {
      label: 'Workflow',
      icon: <IconCamera size={ICON} />,
      items: [
        {
          key: 'pipeline',
          label: 'Pipeline',
          icon: <IconCamera size={ICON} />,
          href: '/pipeline',
          active: isActive('/pipeline') || isActive('/operations') || isActive('/'),
        },
        {
          key: 'photos',
          label: 'Photos',
          icon: <IconPhoto size={ICON} />,
          href: '/photos',
          active: isActive('/photos'),
        },
      ],
    },
    {
      label: 'Data',
      icon: <IconChartHistogram size={ICON} />,
      items: [
        {
          key: 'analytics',
          label: 'Analytics',
          icon: <IconChartHistogram size={ICON} />,
          href: '/analytics',
          active: isActive('/analytics'),
        },
        {
          key: 'library',
          label: 'Library Health',
          icon: <IconHeartbeat size={ICON} />,
          href: '/library',
          active: isActive('/library'),
        },
      ],
    },
  ]

  return (
    <BasaltShell
      brand={{ name: 'Photo Flow', logoSrc: '/favicon.svg' }}
      sections={sections}
      renderNavLink={renderNavLink}
      globalActions={<GlobalActions />}
      collapsed={collapsed}
      onCollapsedChange={setCollapsed}
    >
      <JobController />
      <Outlet />
    </BasaltShell>
  )
}
