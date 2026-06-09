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
 */

const BASE = (import.meta.env.VITE_API_URL as string | undefined) ?? ''

type QueryParams = Record<string, string | number | boolean | undefined>

function buildUrl(path: string, params?: QueryParams): string {
  if (!params) return `${BASE}${path}`
  const entries = Object.entries(params).filter(([, v]) => v !== undefined) as [string, string | number | boolean][]
  if (entries.length === 0) return `${BASE}${path}`
  return `${BASE}${path}?${new URLSearchParams(entries.map(([k, v]) => [k, String(v)]))}`
}

async function request<T>(method: string, path: string, params?: QueryParams): Promise<T> {
  const url = buildUrl(path, params)
  const res = await fetch(url, { method })
  if (!res.ok) {
    const body = await res.text().catch(() => '')
    throw new Error(`API ${method} ${path} → ${res.status}: ${body}`)
  }
  return res.json() as Promise<T>
}

export const api = {
  get: <T>(path: string, params?: QueryParams) => request<T>('GET', path, params),
  post: <T>(path: string, params?: QueryParams) => request<T>('POST', path, params),
}
