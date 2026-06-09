import {
  ActionIcon,
  Box,
  Divider,
  Group,
  Menu,
  NavLink,
  Stack,
  Text,
  Tooltip,
  UnstyledButton,
  useMantineColorScheme,
} from '@mantine/core'
import {
  IconCheck,
  IconDeviceDesktop,
  IconLayoutSidebarLeftCollapse,
  IconLayoutSidebarLeftExpand,
  IconMoon,
  IconSun,
  IconX,
} from '@tabler/icons-react'
import type { MouseEvent, ReactNode } from 'react'
import classes from './app-sidebar.module.css'

export type SidebarItem = {
  key: string
  label: string
  short?: string
  mobile?: boolean
  icon: ReactNode
  href?: string
  active?: boolean
  disabled?: boolean
  onClick?: (e: MouseEvent) => void
  badge?: ReactNode
}

export type SidebarSection = { label: string; items: SidebarItem[] }

type AppSidebarProps = {
  sections: SidebarSection[]
  collapsed: boolean
  onToggleCollapse: () => void
  onClose: () => void
}

const THEME_OPTIONS = [
  { value: 'auto', label: 'System', icon: <IconDeviceDesktop size={16} /> },
  { value: 'light', label: 'Light', icon: <IconSun size={16} /> },
  { value: 'dark', label: 'Dark', icon: <IconMoon size={16} /> },
] as const

function SectionLabel({ children }: { children: ReactNode }) {
  return (
    <Text
      component="div"
      px="xs"
      mb={4}
      size="xs"
      fw={600}
      c="dimmed"
      tt="uppercase"
      className={classes.sectionLabel}
      style={{ letterSpacing: '0.06em' }}
    >
      {children}
    </Text>
  )
}

export function AppSidebar({ sections, collapsed, onToggleCollapse, onClose }: AppSidebarProps) {
  const { colorScheme, setColorScheme } = useMantineColorScheme()
  const current = THEME_OPTIONS.find((o) => o.value === colorScheme) ?? THEME_OPTIONS[0]

  return (
    <Stack gap={0} h="100%" className={classes.root} data-collapsed={collapsed || undefined}>
      <Group className={classes.brand} h={36} mb="md" px="xs" gap="sm" wrap="nowrap">
        <Group className={classes.brandLead} gap="sm" wrap="nowrap">
          <img src="/favicon.svg" alt="" width={26} height={26} style={{ display: 'block' }} aria-hidden="true" />
          <Text fw={700} fz="lg">
            Photo Flow
          </Text>
        </Group>
        <ActionIcon
          variant="subtle"
          color="gray"
          size="md"
          visibleFrom="sm"
          className={classes.collapseBtn}
          onClick={onToggleCollapse}
          aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
        >
          {collapsed ? (
            <IconLayoutSidebarLeftExpand size={18} />
          ) : (
            <IconLayoutSidebarLeftCollapse size={18} />
          )}
        </ActionIcon>
      </Group>

      <Stack gap="lg" style={{ flex: 1, minHeight: 0, overflowY: 'auto' }}>
        {sections.map((section) => (
          <Stack key={section.label} gap={2}>
            <SectionLabel>{section.label}</SectionLabel>
            {section.items.map((item) =>
              item.disabled ? (
                <Tooltip key={item.key} label="Coming soon" position="right" withArrow>
                  <Box>
                    <NavLink
                      classNames={{ root: classes.link }}
                      label={item.label}
                      leftSection={item.icon}
                      disabled
                    />
                  </Box>
                </Tooltip>
              ) : (
                <Tooltip
                  key={item.key}
                  label={item.label}
                  position="right"
                  withArrow
                  disabled={!collapsed}
                >
                  <NavLink
                    classNames={{ root: classes.link }}
                    component="a"
                    href={item.href}
                    label={item.label}
                    leftSection={item.icon}
                    rightSection={item.badge}
                    active={item.active}
                    onClick={item.onClick}
                  />
                </Tooltip>
              ),
            )}
          </Stack>
        ))}
      </Stack>

      <Divider my="sm" />
      <Group gap="xs" wrap="nowrap">
        <Menu position="top-start" withArrow shadow="md" width={180} zIndex={500}>
          <Menu.Target>
            <UnstyledButton className={classes.footerBtn} aria-label="Theme">
              {current.icon}
              <Text className={classes.footerText} size="sm">
                {current.label}
              </Text>
            </UnstyledButton>
          </Menu.Target>
          <Menu.Dropdown>
            <Menu.Label>Theme</Menu.Label>
            {THEME_OPTIONS.map((o) => (
              <Menu.Item
                key={o.value}
                leftSection={o.icon}
                rightSection={colorScheme === o.value ? <IconCheck size={14} /> : null}
                onClick={() => setColorScheme(o.value)}
              >
                {o.label}
              </Menu.Item>
            ))}
          </Menu.Dropdown>
        </Menu>
        <ActionIcon
          variant="subtle"
          color="gray"
          onClick={onClose}
          aria-label="Close navigation"
          hiddenFrom="sm"
        >
          <IconX size={18} />
        </ActionIcon>
      </Group>
    </Stack>
  )
}
