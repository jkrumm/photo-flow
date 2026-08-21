import { api } from '../api'
import type { ConfigResponse } from '../api-types'

/**
 * The resolved install configuration — currently only consumed for its `editors` list
 * (which external applications the culling view can offer, per file kind). Read-only,
 * matching the endpoint: nothing here edits `~/.photoflow/config.toml`.
 */
export const configQueries = {
  config: () => ({
    queryKey: ['config'] as const,
    queryFn: () => api.get<ConfigResponse>('/api/config'),
    // The install config changes only by hand-editing a TOML file and restarting the
    // daemon — there is nothing here that becomes stale while the panel is open.
    staleTime: 5 * 60_000,
  }),
}
