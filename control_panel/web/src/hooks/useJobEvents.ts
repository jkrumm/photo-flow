import { useEffect, useReducer, useRef } from 'react'

type RawSseEvent = Record<string, unknown> & { type: string }

export type LogEntry = { level: string; message: string; id: number }
export type TransferInfo = {
  pct?: number
  speed?: string
  eta?: string
  files?: number | string
}

export type JobEventsState = {
  taskDesc: string | null
  taskTotal: number | null
  taskProgress: number
  logs: LogEntry[]
  lastTransfer: TransferInfo | null
  lastFileDone: string | null
  isDone: boolean
  error: string | null
  result: Record<string, unknown> | null
}

const initialState: JobEventsState = {
  taskDesc: null,
  taskTotal: null,
  taskProgress: 0,
  logs: [],
  lastTransfer: null,
  lastFileDone: null,
  isDone: false,
  error: null,
  result: null,
}

type Action = { type: 'reset' } | { type: 'raw_event'; event: RawSseEvent }

const MAX_LOGS = 80
let logIdSeq = 0

function str(v: unknown): string | undefined {
  return typeof v === 'string' ? v : undefined
}
function num(v: unknown): number | undefined {
  return typeof v === 'number' ? v : undefined
}

function reducer(state: JobEventsState, action: Action): JobEventsState {
  if (action.type === 'reset') return { ...initialState }
  const e = action.event
  switch (e.type) {
    case 'task':
      return {
        ...state,
        taskDesc: str(e['desc']) ?? null,
        taskTotal: num(e['total']) ?? null,
        taskProgress: 0,
      }
    case 'advance':
      return { ...state, taskProgress: state.taskProgress + (num(e['n']) ?? 1) }
    case 'log': {
      const entry: LogEntry = {
        level: str(e['level']) ?? 'info',
        message: str(e['message']) ?? '',
        id: ++logIdSeq,
      }
      return { ...state, logs: [...state.logs, entry].slice(-MAX_LOGS) }
    }
    case 'done': {
      const raw = e['result']
      const result =
        raw !== null && typeof raw === 'object' && !Array.isArray(raw)
          ? (raw as Record<string, unknown>)
          : null
      return {
        ...state,
        isDone: true,
        result,
        error: str(e['error']) ?? null,
      }
    }
    case 'file_done':
      return { ...state, lastFileDone: str(e['filename']) ?? null }
    case 'transfer': {
      const pct = num(e['pct'])
      const speed = str(e['speed'])
      const eta = str(e['eta'])
      const files = typeof e['files'] === 'number' ? e['files'] : str(e['files'])
      return {
        ...state,
        lastTransfer: {
          ...(pct !== undefined && { pct }),
          ...(speed !== undefined && { speed }),
          ...(eta !== undefined && { eta }),
          ...(files !== undefined && { files }),
        },
      }
    }
    default:
      return state
  }
}

export type UseJobEventsOptions = {
  onDone?: (result: Record<string, unknown> | null, error: string | null) => void
}

export function useJobEvents(
  jobId: string | null,
  options: UseJobEventsOptions = {},
): JobEventsState {
  const [state, dispatch] = useReducer(reducer, initialState)
  const onDoneRef = useRef(options.onDone)
  onDoneRef.current = options.onDone

  useEffect(() => {
    dispatch({ type: 'reset' })
    if (jobId === null) return

    const base = (import.meta.env.VITE_API_URL as string | undefined) ?? ''
    const es = new EventSource(`${base}/events/${jobId}`)

    es.addEventListener('message', (e: MessageEvent<string>) => {
      try {
        const event = JSON.parse(e.data) as RawSseEvent
        dispatch({ type: 'raw_event', event })
        if (event.type === 'done') {
          es.close()
          const raw = event['result']
          const result =
            raw !== null && typeof raw === 'object' && !Array.isArray(raw)
              ? (raw as Record<string, unknown>)
              : null
          const error = str(event['error']) ?? null
          onDoneRef.current?.(result, error)
        }
      } catch {
        // ignore malformed SSE frames
      }
    })

    es.addEventListener('error', () => {
      es.close()
    })

    return () => {
      es.close()
    }
  }, [jobId])

  return state
}
