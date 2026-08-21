/**
 * Saved collections, in the sidebar next to the physical folders.
 *
 * The whole point of the arrangement: a saved query and a folder sit in the same list,
 * look the same, and are picked the same way — so "does a query replace a folder?" is a
 * question you answer by using the screen rather than by reasoning about it.
 *
 * Selecting a collection sets the SCOPE, it does not overwrite the filters. That is the
 * F2 change and it is what makes a collection a place you work inside rather than a
 * preset you load: the folder rail and the filter panel keep narrowing on top, and the
 * server composes the two (see `lib/narrowing.ts` for the rule). A collection stays
 * selected while you refine it, so "which collection am I in" survives touching a slider —
 * the F1 build derived activeness by comparing filter values and therefore lost the
 * collection the instant you changed anything.
 *
 * What a collection is NOT is equally load-bearing. Selecting one filters; deleting one
 * deletes a query. No file is moved, copied or linked, ever. That is why prototyping
 * library structures this way is safe on an irreplaceable library, and why this component
 * has no confirmation gate on anything except delete (which is a courtesy, not a safety).
 *
 * Naming happens inline rather than in a modal. A modal for a one-field form on a screen
 * whose entire vocabulary is single keypresses is a lot of ceremony for a text box — and
 * the inline field keeps the collection list visible while you name the thing you are
 * adding to it.
 */
import { useEffect, useRef, useState } from 'react'
import { ActionIcon, Box, Button, Group, Menu, Stack, Text, TextInput, Tooltip } from '@mantine/core'
import { modals } from '@mantine/modals'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { IconBookmark, IconDots, IconPencil, IconPlus, IconTrash } from '@tabler/icons-react'
import { notifyError, notifySuccess } from 'basalt-ui/notifications'
import {
  MAX_COLLECTION_NAME,
  filtersToQuery,
  isSavable,
  type Collection,
} from '../../lib/collections'
import { collectionsApi, collectionsQueries } from '../../lib/queries/collections'
import type { PhotoFilters } from '../../lib/photos'
import { FolderRow } from './photo-folders'

/** Every mutation here invalidates this prefix — the counted and uncounted lists both. */
const COLLECTIONS_KEY = ['collections'] as const

// ── Inline name field ────────────────────────────────────────────────────────

type NameFieldProps = {
  initial: string
  placeholder: string
  pending: boolean
  onSubmit: (name: string) => void
  onCancel: () => void
}

/**
 * One-line name editor used for both "save as" and "rename".
 *
 * Enter commits, Escape cancels, blur cancels — the same contract as renaming a file in
 * Finder, which is the gesture this is imitating.
 */
function NameField({ initial, placeholder, pending, onSubmit, onCancel }: NameFieldProps) {
  const [value, setValue] = useState(initial)
  const ref = useRef<HTMLInputElement>(null)

  useEffect(() => {
    ref.current?.focus()
    ref.current?.select()
  }, [])

  const commit = (): void => {
    const trimmed = value.trim()
    if (trimmed.length === 0) {
      onCancel()
      return
    }
    onSubmit(trimmed)
  }

  return (
    <TextInput
      ref={ref}
      size="xs"
      value={value}
      maxLength={MAX_COLLECTION_NAME}
      placeholder={placeholder}
      disabled={pending}
      onChange={(event) => setValue(event.currentTarget.value)}
      onKeyDown={(event) => {
        if (event.key === 'Enter') {
          event.preventDefault()
          commit()
        }
        if (event.key === 'Escape') {
          event.preventDefault()
          onCancel()
        }
      }}
      onBlur={onCancel}
    />
  )
}

// ── Panel ────────────────────────────────────────────────────────────────────

export type PhotoCollectionsProps = {
  filters: PhotoFilters
  /** The active scope's id, or null for the whole library. */
  scope: string | null
  onScope: (id: string | null) => void
}

export function PhotoCollections({ filters, scope, onScope }: PhotoCollectionsProps) {
  const queryClient = useQueryClient()
  // Counts are asked for: a collection whose size you cannot see is a bookmark, and the
  // question this prototype exists to answer is whether it reads as a FOLDER.
  const listQuery = useQuery(collectionsQueries.list(true))
  const items = listQuery.data?.items ?? []

  const [naming, setNaming] = useState(false)
  const [renaming, setRenaming] = useState<string | null>(null)

  const invalidate = (): void => {
    void queryClient.invalidateQueries({ queryKey: COLLECTIONS_KEY })
  }

  const createMutation = useMutation({
    // The scope rides along so the SERVER composes it with the ad-hoc filters. Saving
    // "Keepers, narrowed to 200mm" must store one flat query, not a reference — a stored
    // parent would change the saved set under you the next time the parent is edited.
    mutationFn: (name: string) => collectionsApi.create(name, filtersToQuery(filters), scope),
    onSuccess: (created) => {
      setNaming(false)
      invalidate()
      onScope(created.id)
      notifySuccess(`Saved “${created.name}”.`)
    },
    onError: (error: Error) => notifyError(error.message, { title: 'Could not save' }),
  })

  const renameMutation = useMutation({
    mutationFn: ({ id, name }: { id: string; name: string }) => collectionsApi.rename(id, name),
    onSuccess: () => {
      setRenaming(null)
      invalidate()
    },
    onError: (error: Error) => notifyError(error.message, { title: 'Could not rename' }),
  })

  const replaceMutation = useMutation({
    mutationFn: (id: string) =>
      collectionsApi.replaceQuery(id, filtersToQuery(filters), scope),
    onSuccess: (updated) => {
      invalidate()
      notifySuccess(`“${updated.name}” now holds the current filters.`)
    },
    onError: (error: Error) => notifyError(error.message, { title: 'Could not update' }),
  })

  const deleteMutation = useMutation({
    mutationFn: (id: string) => collectionsApi.remove(id),
    onSuccess: (_result, id) => {
      // Leaving the scope selected would point the whole screen at a 404.
      if (scope === id) onScope(null)
      invalidate()
    },
    onError: (error: Error) => notifyError(error.message, { title: 'Could not delete' }),
  })

  const confirmDelete = (collection: Collection): void => {
    modals.openConfirmModal({
      title: `Delete “${collection.name}”?`,
      children: (
        <Text size="sm">
          This removes the saved query. Not one photograph is touched — a collection is a
          filter, not a folder.
        </Text>
      ),
      labels: { confirm: 'Delete', cancel: 'Cancel' },
      confirmProps: { color: 'red' },
      onConfirm: () => deleteMutation.mutate(collection.id),
    })
  }

  // Savable when the ad-hoc filters narrow anything, OR when a scope is selected (in
  // which case the composed query is at minimum the scope's own — a legitimate "duplicate
  // this collection").
  const savable = isSavable(filters) || scope !== null

  return (
    <Stack gap={2}>
      <Group justify="space-between" gap={4} wrap="nowrap" pl={6}>
        <Text size="xs" c="dimmed" tt="uppercase" fw={600}>
          Collections
        </Text>
        <Tooltip
          label={savable ? 'Save the current view' : 'Narrow the view first'}
          withArrow
          position="left"
        >
          {/* A disabled ActionIcon swallows pointer events, so the Tooltip needs a wrapper
              to hear them — otherwise the one control that explains WHY it is disabled is
              also the one that cannot say so. */}
          <Box style={{ lineHeight: 0 }}>
            <ActionIcon
              size="xs"
              variant="subtle"
              color="gray"
              aria-label="Save current filters as a collection"
              disabled={!savable}
              onClick={() => setNaming(true)}
            >
              <IconPlus size={13} />
            </ActionIcon>
          </Box>
        </Tooltip>
      </Group>

      {naming && (
        <Box px={6} py={2}>
          <NameField
            initial=""
            placeholder="Name this view…"
            pending={createMutation.isPending}
            onSubmit={(name) => createMutation.mutate(name)}
            onCancel={() => setNaming(false)}
          />
        </Box>
      )}

      {items.length === 0 && !naming && (
        <Text size="xs" c="dimmed" pl={6}>
          {savable
            ? 'Save this view to come back to it.'
            : 'Filter the library, then save the view as a collection.'}
        </Text>
      )}

      {items.map((collection) => {
        if (renaming === collection.id) {
          return (
            <Box key={collection.id} px={6} py={2}>
              <NameField
                initial={collection.name}
                placeholder="Name"
                pending={renameMutation.isPending}
                onSubmit={(name) => renameMutation.mutate({ id: collection.id, name })}
                onCancel={() => setRenaming(null)}
              />
            </Box>
          )
        }

        const active = scope === collection.id
        return (
          <Group key={collection.id} gap={2} wrap="nowrap" align="center">
            <Box style={{ flex: 1, minWidth: 0 }}>
              <FolderRow
                label={collection.name}
                count={collection.count ?? undefined}
                active={active}
                leading={<IconBookmark size={12} />}
                onClick={() => onScope(active ? null : collection.id)}
              />
            </Box>
            {/* No `shadow` prop: basalt's theme already sets the dropdown's box-shadow to
                `--vx-shadow-overlay`, and a Mantine shadow key that does not exist is a
                dead prop that reads as configuration. */}
            <Menu position="bottom-end" withinPortal>
              <Menu.Target>
                <ActionIcon
                  size="xs"
                  variant="subtle"
                  color="gray"
                  aria-label={`Actions for ${collection.name}`}
                >
                  <IconDots size={13} />
                </ActionIcon>
              </Menu.Target>
              <Menu.Dropdown>
                <Menu.Item
                  leftSection={<IconPencil size={13} />}
                  onClick={() => setRenaming(collection.id)}
                >
                  Rename
                </Menu.Item>
                <Menu.Item
                  disabled={!isSavable(filters)}
                  onClick={() => replaceMutation.mutate(collection.id)}
                >
                  Save current view over it
                </Menu.Item>
                <Menu.Divider />
                <Menu.Item
                  color="red"
                  leftSection={<IconTrash size={13} />}
                  onClick={() => confirmDelete(collection)}
                >
                  Delete
                </Menu.Item>
              </Menu.Dropdown>
            </Menu>
          </Group>
        )
      })}

      {listQuery.isError && (
        <Text size="xs" c="dimmed" pl={6}>
          Collections could not be read.
        </Text>
      )}

      {savable && items.length > 0 && !naming && (
        <Button
          size="compact-xs"
          variant="subtle"
          justify="flex-start"
          fullWidth
          onClick={() => setNaming(true)}
        >
          Save this view…
        </Button>
      )}
    </Stack>
  )
}
