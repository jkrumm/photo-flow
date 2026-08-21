/**
 * Typed API client — thin fetch wrapper over the photo-flow FastAPI backend.
 *
 * All requests are same-origin in production (FastAPI serves both SPA and API).
 * In dev, Vite proxies path prefixes to the uvicorn process on :7717.
 *
 * Usage:
 *   import { api } from './api'
 *   const status = await api.get<StatusResponse>('/status')
 *   const job = await api.post<JobStarted>('/ops/import')
 *   const job2 = await api.post<JobStarted>('/ops/cleanup', {}, { approved_preview: preview })
 */

const BASE = (import.meta.env.VITE_API_URL as string | undefined) ?? ''

type QueryParams = Record<string, string | number | boolean | undefined>

function buildUrl(path: string, params?: QueryParams): string {
  if (!params) return `${BASE}${path}`
  const entries = Object.entries(params).filter(([, v]) => v !== undefined) as [string, string | number | boolean][]
  if (entries.length === 0) return `${BASE}${path}`
  return `${BASE}${path}?${new URLSearchParams(entries.map(([k, v]) => [k, String(v)]))}`
}

/** Per-call transport options. `signal` lets React Query abort a superseded request. */
type RequestOptions = { signal?: AbortSignal }

async function request<T>(
  method: string,
  path: string,
  params?: QueryParams,
  body?: Record<string, unknown>,
  options?: RequestOptions,
): Promise<T> {
  const url = buildUrl(path, params)
  const hasBody = body !== undefined && body !== null
  // exactOptionalPropertyTypes forbids explicit `undefined` for optional RequestInit fields.
  // Spread headers + body conditionally so they are simply absent when not needed.
  const res = await fetch(url, {
    method,
    ...(hasBody
      ? { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }
      : {}),
    ...(options?.signal === undefined ? {} : { signal: options.signal }),
  })
  if (!res.ok) {
    const text = await res.text().catch(() => '')
    throw new Error(`API ${method} ${path} → ${res.status}: ${text}`)
  }
  return res.json() as Promise<T>
}

export const api = {
  get: <T>(path: string, params?: QueryParams, options?: RequestOptions) =>
    request<T>('GET', path, params, undefined, options),
  post: <T>(path: string, params?: QueryParams, body?: Record<string, unknown>) =>
    request<T>('POST', path, params, body),
  /** Partial update. Used by the saved-collections CRUD; `POST`-for-everything hides
   *  the difference between creating a collection and editing one. */
  patch: <T>(path: string, params?: QueryParams, body?: Record<string, unknown>) =>
    request<T>('PATCH', path, params, body),
  /** `delete` is a reserved word, so the method is `del`. */
  del: <T>(path: string, params?: QueryParams) => request<T>('DELETE', path, params),
}
