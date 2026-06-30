import {
  createRootRouteWithContext,
  Outlet,
  useMatchRoute,
  useNavigate,
} from '@tanstack/react-router'
import type { QueryClient } from '@tanstack/react-query'
import { AppShell, Divider } from '@mantine/core'
import { useDisclosure, useHotkeys } from '@mantine/hooks'
import {
  IconCamera,
  IconChartHistogram,
  IconHeartbeat,
  IconMenu2,
} from '@tabler/icons-react'
import type { MouseEvent } from 'react'
import { AppSidebar, type SidebarSection } from '../components/app-shell/app-sidebar'
import { MobileNav } from '../components/app-shell/app-mobile-nav'
import { AppBreadcrumbs } from '../components/app-shell/app-breadcrumbs'
import { GlobalActions } from '../components/app-shell/global-actions'
import { PageActionsOutlet, PageHeaderProvider } from '../components/app-shell/page-header'
import { JobController } from '../components/app-shell/job-controller'
import { JobProgressPill } from '../components/app-shell/job-progress-pill'
import { NotificationSettings } from '../components/app-shell/notification-settings'
import { useUiStore } from '../lib/store'
import classes from '../components/app-shell/app-header.module.css'

export const Route = createRootRouteWithContext<{ queryClient: QueryClient }>()({
  component: RootLayout,
})

const ICON = 18

function RootLayout() {
  const [mobileOpened, { toggle: toggleMobile, close: closeMobile }] = useDisclosure()
  const matchRoute = useMatchRoute()
  const navigate = useNavigate()
  const sidebarCollapsed = useUiStore((s) => s.sidebarCollapsed)
  const toggleSidebar = useUiStore((s) => s.toggleSidebar)

  useHotkeys([['mod+B', toggleSidebar]])

  const isPipelineActive =
    !!matchRoute({ to: '/pipeline', fuzzy: true }) ||
    !!matchRoute({ to: '/operations', fuzzy: true }) ||
    !!matchRoute({ to: '/', fuzzy: false })
  const isAnalyticsActive = !!matchRoute({ to: '/analytics', fuzzy: true })
  const isLibraryActive = !!matchRoute({ to: '/library', fuzzy: true })

  const go = (run: () => void) => (e: MouseEvent) => {
    e.preventDefault()
    closeMobile()
    run()
  }

  const sections: SidebarSection[] = [
    {
      label: 'Workflow',
      items: [
        {
          key: 'pipeline',
          label: 'Pipeline',
          short: 'Pipeline',
          mobile: true,
          icon: <IconCamera size={ICON} />,
          href: '/pipeline',
          active: isPipelineActive,
          onClick: go(() => void navigate({ to: '/pipeline' })),
        },
      ],
    },
    {
      label: 'Data',
      items: [
        {
          key: 'analytics',
          label: 'Analytics',
          short: 'Analytics',
          mobile: true,
          icon: <IconChartHistogram size={ICON} />,
          href: '/analytics',
          active: isAnalyticsActive,
          onClick: go(() => void navigate({ to: '/analytics' })),
        },
        {
          key: 'library',
          label: 'Library Health',
          short: 'Library',
          mobile: false,
          icon: <IconHeartbeat size={ICON} />,
          href: '/library',
          active: isLibraryActive,
          onClick: go(() => void navigate({ to: '/library' })),
        },
      ],
    },
  ]

  const activeCrumb = sections
    .flatMap((s) => s.items.map((it) => ({ section: s.label, page: it.label, active: it.active })))
    .find((x) => x.active)

  const mobileItems = [
    ...sections
      .flatMap((s) => s.items)
      .filter((it) => it.mobile)
      .map((it) => ({
        key: it.key,
        short: it.short ?? it.label,
        icon: it.icon,
        href: it.href,
        active: it.active,
        onClick: it.onClick,
      })),
    { key: 'menu', short: 'Menu', icon: <IconMenu2 size={ICON} />, onClick: () => toggleMobile() },
  ]

  return (
    <PageHeaderProvider>
      <JobController />
      <AppShell
        h="100dvh"
        layout="alt"
        header={{ height: { base: 108, sm: 56 } }}
        navbar={{
          width: { base: 240, sm: sidebarCollapsed ? 72 : 240 },
          breakpoint: 'sm',
          collapsed: { mobile: !mobileOpened },
        }}
        footer={{ height: { base: 56, sm: 0 } }}
        padding="md"
      >
        <AppShell.Header px="md">
          <div className={classes.bar}>
            <div className={classes.lead}>
              <AppBreadcrumbs section={activeCrumb?.section} page={activeCrumb?.page} />
            </div>
            <PageActionsOutlet className={classes.pageActions} />
            <Divider orientation="vertical" visibleFrom="sm" style={{ height: 24 }} />
            <JobProgressPill />
            <NotificationSettings />
            <GlobalActions className={classes.global} />
          </div>
        </AppShell.Header>

        <AppShell.Navbar p="md">
          <AppSidebar
            sections={sections}
            collapsed={sidebarCollapsed}
            onToggleCollapse={toggleSidebar}
            onClose={closeMobile}
          />
        </AppShell.Navbar>

        <AppShell.Main>
          <Outlet />
        </AppShell.Main>

        <AppShell.Footer hiddenFrom="sm" p={0}>
          <MobileNav items={mobileItems} />
        </AppShell.Footer>
      </AppShell>
    </PageHeaderProvider>
  )
}
