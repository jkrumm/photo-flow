import { useRef, useState, useLayoutEffect, useCallback, useMemo, useEffect } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { useMutation, useQuery } from '@tanstack/react-query'
import { Loader, Menu, Tooltip } from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { IconPlayerPlayFilled, IconChevronDown } from '@tabler/icons-react'
import { VX } from '../../lib/charts/tokens'
import { alpha } from '../../lib/charts/utils/color'
import { statusQueries } from '../../lib/queries/status'
import { analyticsQueries } from '../../lib/queries/analytics'
import { backupQueries } from '../../lib/queries/backup'
import { galleryQueries } from '../../lib/queries/gallery'
import { opsApi } from '../../lib/queries/ops'
import { useActiveJobStore, useJobLiveStore, type ActiveOp } from '../../lib/store'
import { DryRunModal } from '../operations/DryRunModal'
import { JobProgressPanel } from '../operations/JobProgressPanel'
import { JobQueuePanel } from '../operations/JobQueuePanel'
import { OP_LABELS, BACKUP_SOURCES } from '../../lib/op-metadata'
import { resumeAudio } from '../../lib/sound'
import { usePipelineAdvisor, type Advice } from '../../hooks/usePipelineAdvisor'

// ── Types ─────────────────────────────────────────────────────────────────────

type StageId = 'camera' | 'staging' | 'final' | 'homelab' | 'gallery'

interface Point {
  x: number
  y: number
}

interface NodeRect extends Point {
  w: number
  h: number
}

type QueryParams = Record<string, string | number | boolean>

interface EdgeOpDef {
  op: Exclude<ActiveOp, 'cleanup'>
  label: string
  fromId: StageId
  toId: StageId
  destructive: boolean
  /** Verb-phrase tooltip shown on hover. */
  desc: string
}

// Stage identity colors — one source of truth for node accents, edge gradients,
// and pill accents. An edge flows from STAGE_COLOR[fromId] → STAGE_COLOR[toId];
// its pill carries the *destination* color (the action's outcome).
const STAGE_COLOR: Record<StageId, string> = {
  camera: VX.photo.camera,
  staging: VX.photo.staging,
  final: VX.photo.final,
  homelab: VX.photo.final,
  gallery: VX.photo.published,
}

// ── Edge operations (the pipeline transitions that ARE operations) ──────────────

const EDGE_OPS: EdgeOpDef[] = [
  {
    op: 'import',
    label: 'Import',
    fromId: 'camera',
    toId: 'staging',
    destructive: true,
    desc: 'Move files from the camera into Staging, RAWs and Videos — renamed with a timestamp. Removes them from the card.',
  },
  {
    op: 'finalize',
    label: 'Finalize',
    fromId: 'staging',
    toId: 'final',
    destructive: true,
    desc: 'Move Staging JPGs into Final at full quality, carrying their .photo-edit sidecars. Clears Staging.',
  },
  {
    op: 'backup',
    label: 'Backup',
    fromId: 'final',
    toId: 'homelab',
    destructive: false,
    desc: 'Rclone backup to the homelab over Tailscale. Pick which source to back up.',
  },
  {
    op: 'sync-gallery',
    label: 'Sync',
    fromId: 'final',
    toId: 'gallery',
    destructive: false,
    desc: 'Sync rating ≥ 4 photos to the gallery, build it, and deploy to the public site.',
  },
]

// ── Node card (informational — actions live on the edges) ───────────────────────

interface NodeCardProps {
  label: string
  color: string
  primaryCount: number | string | null
  primaryLabel: string
  secondaryCounts?: { label: string; value: number | string | null }[]
  statusDot?: { connected: boolean; label: string }
  isActive: boolean
  nodeRef: React.RefObject<HTMLDivElement | null>
  index: number
}

function NodeCard({
  label,
  color,
  primaryCount,
  primaryLabel,
  secondaryCounts,
  statusDot,
  isActive,
  nodeRef,
  index,
}: NodeCardProps) {
  return (
    <motion.div
      ref={nodeRef}
      initial={{ opacity: 0, y: 14 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.45, delay: index * 0.07, ease: [0.22, 1, 0.36, 1] }}
      style={{
        position: 'relative',
        background: VX.surface.panel,
        border: `1px solid ${isActive ? alpha(color, 0.6) : VX.surface.border}`,
        borderRadius: 12,
        padding: '18px 20px',
        minWidth: 158,
        flex: '0 0 auto',
        boxShadow: isActive ? `0 0 0 1px ${alpha(color, 0.35)}, ${VX.shadowCard}` : VX.shadowCard,
        overflow: 'hidden',
        transition: 'border-color 0.3s, box-shadow 0.3s',
      }}
    >
      <motion.div
        aria-hidden="true"
        style={{
          position: 'absolute',
          top: 0,
          left: 0,
          right: 0,
          height: 3,
          borderRadius: '12px 12px 0 0',
          background: color,
        }}
        animate={{ opacity: isActive ? 1 : 0.55 }}
        transition={{ duration: 0.3 }}
      />

      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 }}>
        <span
          style={{
            fontSize: 11,
            fontWeight: 600,
            letterSpacing: '0.07em',
            textTransform: 'uppercase',
            color: alpha(VX.neutral, 0.7),
          }}
        >
          {label}
        </span>
        {statusDot !== undefined && (
          <Tooltip label={`${statusDot.label}: ${statusDot.connected ? 'connected' : 'not found'}`} withArrow>
            <motion.div
              aria-label={`${statusDot.label} ${statusDot.connected ? 'connected' : 'disconnected'}`}
              style={{
                width: 7,
                height: 7,
                borderRadius: '50%',
                background: statusDot.connected ? 'var(--vx-goodSolid)' : alpha(VX.neutral, 0.3),
                flexShrink: 0,
              }}
              animate={{ scale: statusDot.connected && isActive ? [1, 1.35, 1] : 1 }}
              transition={{ duration: 1.2, repeat: statusDot.connected && isActive ? Infinity : 0 }}
            />
          </Tooltip>
        )}
      </div>

      <div
        style={{
          fontSize: 33,
          fontWeight: 700,
          lineHeight: 1,
          fontVariantNumeric: 'tabular-nums',
          color: primaryCount !== null && primaryCount !== 0 ? color : alpha(VX.neutral, 0.35),
          marginBottom: 5,
        }}
      >
        {primaryCount ?? '–'}
      </div>
      <div
        style={{
          fontSize: 11,
          color: alpha(VX.neutral, 0.6),
          marginBottom: secondaryCounts && secondaryCounts.length > 0 ? 8 : 0,
        }}
      >
        {primaryLabel}
      </div>

      {secondaryCounts && secondaryCounts.length > 0 && (
        <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
          {secondaryCounts.map(({ label: subLabel, value }) => (
            <div key={subLabel} style={{ display: 'flex', gap: 4, alignItems: 'baseline' }}>
              <span
                style={{
                  fontSize: 13,
                  fontWeight: 600,
                  fontVariantNumeric: 'tabular-nums',
                  color: value ? alpha(color, 0.9) : alpha(VX.neutral, 0.3),
                }}
              >
                {value ?? 0}
              </span>
              <span style={{ fontSize: 10, color: alpha(VX.neutral, 0.5) }}>{subLabel}</span>
            </div>
          ))}
        </div>
      )}
    </motion.div>
  )
}

// ── Edge action pill (positioned on the edge midpoint) ──────────────────────────

interface EdgePillProps {
  def: EdgeOpDef
  mid: Point | undefined
  active: boolean
  loading: boolean
  /** Non-null = disabled; the string is the reason shown on hover. */
  disabledReason: string | null
  onHover: (op: ActiveOp | null) => void
  onRun: (def: EdgeOpDef, extra?: QueryParams) => void
  /** Optional sync-state badge rendered after the label. */
  status?: { label: string; tone: 'good' | 'warn' } | null
}

function EdgePill({ def, mid, active, loading, disabledReason, onHover, onRun, status }: EdgePillProps) {
  if (!mid) return null

  const disabled = disabledReason !== null
  // Accent is the *destination* stage color — the action's outcome (Finalize → blue).
  // Destructive ops keep that identity but add a subtle warn ring rather than recoloring.
  const accent = STAGE_COLOR[def.toId]
  // We intentionally do NOT set the HTML `disabled` attribute: a disabled button swallows
  // pointer events, which would kill the "why is this disabled" tooltip. Instead we mark it
  // aria-disabled, style it as disabled, and guard the click handler.
  const inner = (
    <motion.button
      type="button"
      aria-disabled={disabled}
      aria-label={`${def.label} — ${def.desc}${disabled ? ` (unavailable: ${disabledReason})` : ''}`}
      onMouseEnter={() => onHover(def.op)}
      onMouseLeave={() => onHover(null)}
      onFocus={() => onHover(def.op)}
      onBlur={() => onHover(null)}
      onClick={disabled || def.op === 'backup' ? undefined : () => onRun(def)}
      whileHover={disabled ? {} : { scale: 1.05 }}
      whileTap={disabled ? {} : { scale: 0.95 }}
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 6,
        padding: '6px 11px',
        borderRadius: 999,
        border: `1px solid ${
          disabled
            ? VX.surface.border
            : def.destructive
              ? alpha(VX.warnSolid, active ? 0.85 : 0.55)
              : active
                ? alpha(accent, 0.7)
                : VX.surface.border
        }`,
        // Opaque base + tint overlay so the connector line is fully hidden behind the
        // pill — it cleanly interrupts the line instead of being cut across by it.
        backgroundColor: VX.surface.elevated,
        backgroundImage: active && !disabled
          ? `linear-gradient(${alpha(accent, 0.2)}, ${alpha(accent, 0.2)})`
          : 'none',
        color: disabled ? alpha(VX.neutral, 0.4) : VX.neutral,
        fontSize: 12,
        fontWeight: 600,
        lineHeight: 1,
        cursor: disabled ? 'not-allowed' : 'pointer',
        opacity: disabled ? 0.7 : 1,
        whiteSpace: 'nowrap',
        boxShadow: active && !disabled ? `0 0 0 4px ${alpha(accent, 0.1)}, ${VX.shadowCard}` : VX.shadowCard,
        transition: 'background 0.2s, border-color 0.2s, box-shadow 0.2s, color 0.2s, opacity 0.2s',
      }}
    >
      {loading ? (
        <Loader size={12} color={accent} />
      ) : (
        <IconPlayerPlayFilled size={12} style={{ color: disabled ? alpha(VX.neutral, 0.4) : accent }} />
      )}
      {def.label}
      {status && (
        <span
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            padding: '1px 5px',
            borderRadius: 999,
            fontSize: 10,
            fontWeight: 600,
            lineHeight: 1.4,
            background: alpha(status.tone === 'good' ? VX.good : VX.warn, 0.15),
            color: status.tone === 'good' ? VX.good : VX.warn,
            marginLeft: 2,
          }}
        >
          {status.label}
        </span>
      )}
      {def.op === 'backup' && <IconChevronDown size={12} style={{ opacity: 0.6 }} />}
    </motion.button>
  )

  const wrapped =
    def.op === 'backup' && !disabled ? (
      <Menu position="bottom" withArrow shadow="md">
        <Menu.Target>{inner}</Menu.Target>
        <Menu.Dropdown>
          <Menu.Label>Back up source</Menu.Label>
          {BACKUP_SOURCES.map((s) => (
            <Menu.Item key={s.value} onClick={() => onRun(def, { source: s.value })}>
              {s.label}
            </Menu.Item>
          ))}
        </Menu.Dropdown>
      </Menu>
    ) : (
      <Tooltip
        label={
          disabled ? (
            <>
              {def.desc}
              <div style={{ marginTop: 6, color: VX.warnSolid, fontWeight: 600 }}>⊘ {disabledReason}</div>
            </>
          ) : (
            def.desc
          )
        }
        withArrow
        multiline
        w={240}
        openDelay={disabled ? 200 : 400}
      >
        {inner}
      </Tooltip>
    )

  return (
    <div
      style={{
        position: 'absolute',
        left: mid.x,
        top: mid.y,
        transform: 'translate(-50%, -50%)',
        zIndex: 3,
      }}
    >
      {wrapped}
    </div>
  )
}

// ── Status strip ────────────────────────────────────────────────────────────────

function StatusChip({ label, connected, hint }: { label: string; connected: boolean; hint?: string }) {
  return (
    <Tooltip label={hint ?? `${label}: ${connected ? 'connected' : 'not found'}`} withArrow openDelay={300}>
      <span
        style={{
          display: 'inline-flex',
          alignItems: 'center',
          gap: 6,
          fontSize: 11.5,
          color: alpha(VX.neutral, connected ? 0.75 : 0.5),
        }}
      >
        <span
          style={{
            width: 7,
            height: 7,
            borderRadius: '50%',
            background: connected ? 'var(--vx-goodSolid)' : alpha(VX.neutral, 0.3),
            flexShrink: 0,
          }}
        />
        {label}
      </span>
    </Tooltip>
  )
}

// ── Advice block (replaces the former 2-transition "Next:" hint) ────────────────

// Maps each op to its destination stage accent color (mirrors STAGE_COLOR via EDGE_OPS).
const OP_ADVICE_ACCENT: Record<ActiveOp, string> = {
  import: VX.photo.staging,
  finalize: VX.photo.final,
  backup: VX.photo.final,
  'sync-gallery': VX.photo.published,
  cleanup: VX.warnSolid,
}

interface AdviceChipProps {
  advice: Advice
  primary: boolean
  onRun: (a: Advice) => void
  disabled: boolean
}

function AdviceChip({ advice, primary, onRun, disabled: parentDisabled }: AdviceChipProps) {
  const off = advice.disabled === true || parentDisabled
  const accent = OP_ADVICE_ACCENT[advice.op]

  // Primary action ops (import / finalize) use the warm amber style matching the
  // former suggestion button. Attention ops use their destination stage color.
  const isAction = advice.tone === 'action'
  const borderColor = off
    ? VX.surface.border
    : isAction
      ? alpha(VX.warn, primary ? 0.6 : 0.4)
      : alpha(accent, primary ? 0.55 : 0.35)
  const bgColor = off
    ? 'transparent'
    : isAction
      ? alpha(VX.warn, primary ? 0.16 : 0.10)
      : alpha(accent, primary ? 0.12 : 0.08)
  const iconColor = off ? alpha(VX.neutral, 0.35) : isAction ? VX.warn : accent
  const textColor = off ? alpha(VX.neutral, 0.4) : VX.neutral

  const btn = (
    <motion.button
      type="button"
      aria-disabled={off}
      onClick={off ? undefined : () => onRun(advice)}
      whileHover={off ? {} : { scale: 1.04 }}
      whileTap={off ? {} : { scale: 0.96 }}
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 6,
        padding: primary ? '6px 14px' : '5px 11px',
        borderRadius: 999,
        border: `1px solid ${borderColor}`,
        background: bgColor,
        color: textColor,
        fontSize: primary ? 12 : 11,
        fontWeight: 600,
        lineHeight: 1,
        cursor: off ? 'not-allowed' : 'pointer',
        opacity: off ? 0.7 : 1,
        whiteSpace: 'nowrap',
        transition: 'background 0.2s, border-color 0.2s, opacity 0.2s',
      }}
    >
      <IconPlayerPlayFilled size={primary ? 12 : 11} style={{ color: iconColor, flexShrink: 0 }} />
      {OP_LABELS[advice.op]}
    </motion.button>
  )

  if (off && advice.disabledReason) {
    return (
      <Tooltip label={advice.disabledReason} withArrow openDelay={150}>
        {btn}
      </Tooltip>
    )
  }
  return btn
}

interface AdviceBlockProps {
  advices: Advice[]
  onRun: (a: Advice) => void
  /** True while a dry-run request is in-flight — prevents double-trigger. */
  dryRunPending: boolean
}

function AdviceBlock({ advices, onRun, dryRunPending }: AdviceBlockProps) {
  // The hook returns [] while the pipeline snapshot is still loading (or errored);
  // render nothing until data arrives — an empty list is "not known yet", not "clear".
  const primary = advices[0]
  if (primary === undefined) return null
  const secondary = advices.slice(1)

  if (primary.tone === 'idle') {
    return (
      <span style={{ fontSize: 13, color: alpha(VX.neutral, 0.65) }}>
        Pipeline clear — nothing pending.
      </span>
    )
  }

  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
      {/* Primary CTA — headline + detail */}
      <span style={{ fontSize: 13, color: alpha(VX.neutral, 0.8) }}>
        Next:{' '}
        <strong style={{ color: VX.neutral }}>{primary.headline}</strong>
        {primary.detail !== undefined && (
          <> · <span style={{ color: alpha(VX.neutral, 0.55) }}>{primary.detail}</span></>
        )}
      </span>

      {/* Primary action button */}
      <AdviceChip
        advice={primary}
        primary
        onRun={onRun}
        disabled={dryRunPending}
      />

      {/* Secondary chips — rendered in priority order, each styled by op */}
      {secondary.map((a) => (
        <AdviceChip
          key={a.op}
          advice={a}
          primary={false}
          onRun={onRun}
          disabled={dryRunPending}
        />
      ))}
    </div>
  )
}

// ── Main unified command center ─────────────────────────────────────────────────

/**
 * Pipeline command center — the signature screen.
 *
 * Renders the Camera → Staging → Final → Homelab/Gallery graph with live counts,
 * where every edge IS its operation: hover highlights the transition, click runs
 * its dry-run → confirm → live SSE progress (which animates the same edge).
 * Folds the former separate Operations screen into one surface.
 */
export type PipelineHeroProps = {
  /** Op to trigger a dry-run modal for immediately on mount (from a Library Health deep-link). */
  initialAction?: ActiveOp
  /** Called synchronously before the dry-run fires — replaces the URL to consume the search param. */
  onConsumeAction?: () => void
}

export function PipelineHero({ initialAction, onConsumeAction }: PipelineHeroProps = {}) {
  const containerRef = useRef<HTMLDivElement | null>(null)
  const cameraRef = useRef<HTMLDivElement | null>(null)
  const stagingRef = useRef<HTMLDivElement | null>(null)
  const finalRef = useRef<HTMLDivElement | null>(null)
  const homelabRef = useRef<HTMLDivElement | null>(null)
  const galleryRef = useRef<HTMLDivElement | null>(null)

  const [geom, setGeom] = useState<Partial<Record<StageId, NodeRect>>>({})
  const [hoveredOp, setHoveredOp] = useState<ActiveOp | null>(null)

  const activeOp = useActiveJobStore((s) => s.activeOp)
  const activeJobId = useActiveJobStore((s) => s.activeJobId)
  const setActiveJob = useActiveJobStore((s) => s.setActiveJob)
  const lastFinishedOp = useActiveJobStore((s) => s.lastFinishedOp)

  // ── Data queries ───────────────────────────────────────────────────────────
  const { data: status } = useQuery(statusQueries.status())
  const { data: pending } = useQuery(statusQueries.pending())
  const { data: summary } = useQuery(analyticsQueries.summary())
  const { data: backupAvail } = useQuery(backupQueries.availability())
  const { data: backupRemote } = useQuery(backupQueries.availabilityRemote())
  const { data: gallery } = useQuery(galleryQueries.status())

  // ── Live job events (owned by JobController — read from shared store) ────────
  // JobController (mounted in __root.tsx) owns the single EventSource and writes
  // to useJobLiveStore. PipelineHero reads from it so the progress bar, log tail,
  // and edge animation stay driven by live data without re-owning the stream.
  const eventsState = useJobLiveStore()
  const isJobRunning = activeJobId !== null

  // ── Dry-run / start flow (one shared modal for every op) ─────────────────────
  const [modalOpen, setModalOpen] = useState(false)
  const [preview, setPreview] = useState<Record<string, unknown> | null>(null)
  const [pendingOp, setPendingOp] = useState<{ op: ActiveOp; label: string; destructive: boolean; extra?: QueryParams } | null>(null)

  const dryRunMutation = useMutation({
    mutationFn: (v: { op: ActiveOp; extra?: QueryParams }) => opsApi.dryRun(v.op, v.extra),
    onSuccess: (data) => {
      setPreview(data)
      setModalOpen(true)
    },
    onError: (err: Error) => {
      notifications.show({ title: 'Preview failed', message: err.message, color: 'red', autoClose: 6000 })
      setPendingOp(null)
    },
  })

  const cancelMutation = useMutation({
    mutationFn: () => {
      if (!activeJobId) throw new Error('No active job to cancel')
      return opsApi.cancel(activeJobId)
    },
    onSuccess: () => {
      notifications.show({
        title: 'Stopping…',
        message: 'The job will halt at the next file. Already-processed files are kept.',
        color: 'yellow',
        autoClose: 4000,
      })
    },
    onError: (err: Error) => {
      notifications.show({ title: 'Stop failed', message: err.message, color: 'red', autoClose: 6000 })
    },
  })

  const startMutation = useMutation({
    mutationFn: (v: { op: ActiveOp; extra?: QueryParams; approvedPreview?: Record<string, unknown> | null }) => {
      const body =
        v.approvedPreview !== undefined && v.approvedPreview !== null
          ? { approved_preview: v.approvedPreview }
          : undefined
      return opsApi.start(v.op, v.extra, body)
    },
    onSuccess: (data, variables) => {
      setModalOpen(false)
      const position = data.position ?? 0
      const currentActiveId = useActiveJobStore.getState().activeJobId

      if (position === 0 && currentActiveId === null && variables !== undefined) {
        // Job is at the front of the queue and will start immediately —
        // optimistically set it active (the manager stream's job_started event
        // will also fire, but calling setActiveJob twice with the same args is safe).
        setActiveJob(data.job_id, variables.op)
      } else if (position > 0) {
        // Job queued behind something already running.
        notifications.show({
          title: `Queued: ${variables !== undefined ? OP_LABELS[variables.op] : 'Job'}`,
          message: 'Runs automatically when the current job finishes.',
          color: 'blue',
          autoClose: 4000,
        })
      }
    },
    onError: (err: Error) => {
      setModalOpen(false)
      // 409 should never happen with the queue model but treat it as a graceful no-op.
      const is409 = err.message.includes('409')
      if (!is409) {
        notifications.show({
          title: 'Failed to start',
          message: err.message,
          color: 'red',
          autoClose: 6000,
        })
      }
    },
  })

  const runEdgeOp = useCallback(
    (def: EdgeOpDef, extra?: QueryParams) => {
      resumeAudio() // unblock AudioContext on the user gesture
      // exactOptionalPropertyTypes: omit `extra` when undefined rather than passing undefined explicitly.
      setPendingOp({ op: def.op, label: OP_LABELS[def.op], destructive: def.destructive, ...(extra !== undefined ? { extra } : {}) })
      dryRunMutation.mutate({ op: def.op, ...(extra !== undefined ? { extra } : {}) })
    },
    [dryRunMutation],
  )

  const runCleanup = useCallback(() => {
    resumeAudio() // unblock AudioContext on the user gesture
    setPendingOp({ op: 'cleanup', label: OP_LABELS.cleanup, destructive: true })
    dryRunMutation.mutate({ op: 'cleanup' })
  }, [dryRunMutation])

  // ── Advisor ───────────────────────────────────────────────────────────────
  const advices = usePipelineAdvisor()

  /** Dispatch the same flow as an edge pill or the cleanup button. */
  const runAdvice = useCallback(
    (a: Advice) => {
      if (a.disabled) return
      if (a.op === 'cleanup') {
        runCleanup()
        return
      }
      const def = EDGE_OPS.find((e) => e.op === a.op)
      if (def) runEdgeOp(def)
    },
    [runCleanup, runEdgeOp],
  )

  // ── Deep-link: initialAction from Library Health CTA ─────────────────────
  // Fires once on mount when the route was opened with ?action=... param.
  // onConsumeAction replaces the URL first so a hard-refresh doesn't re-trigger.
  useEffect(() => {
    if (!initialAction) return
    onConsumeAction?.()
    if (initialAction === 'cleanup') {
      runCleanup()
    } else {
      const def = EDGE_OPS.find((e) => e.op === initialAction)
      if (def) runEdgeOp(def)
    }
    // Intentionally empty dep array — fire once on mount only.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  // ── Layout measurement ─────────────────────────────────────────────────────
  const measure = useCallback(() => {
    const container = containerRef.current
    if (!container) return
    const cr = container.getBoundingClientRect()

    function rect(ref: React.RefObject<HTMLDivElement | null>): NodeRect | null {
      if (!ref.current) return null
      const r = ref.current.getBoundingClientRect()
      return { x: r.left - cr.left + r.width / 2, y: r.top - cr.top + r.height / 2, w: r.width, h: r.height }
    }

    const cam = rect(cameraRef)
    const stg = rect(stagingRef)
    const fin = rect(finalRef)
    const hom = rect(homelabRef)
    const gal = rect(galleryRef)
    if (!cam || !stg || !fin || !hom || !gal) return

    setGeom({ camera: cam, staging: stg, final: fin, homelab: hom, gallery: gal })
  }, [])

  useLayoutEffect(() => {
    measure()
    // Observe every card, not just the container: when live counts change a card's
    // width (e.g. 0 → 1918), the container stays 100% wide so a container-only
    // observer never fires, leaving connectors drawn to stale positions.
    const ro = new ResizeObserver(measure)
    for (const r of [containerRef, cameraRef, stagingRef, finalRef, homelabRef, galleryRef]) {
      if (r.current) ro.observe(r.current)
    }
    return () => ro.disconnect()
  }, [measure])

  // ── Counts ───────────────────────────────────────────────────────────────
  const pendingPhotos = pending?.pending_photos ?? null
  const pendingRaws = pending?.pending_raws ?? null
  const pendingVideos = pending?.pending_videos ?? null
  const cameraTotal =
    pendingPhotos !== null ? pendingPhotos + (pendingRaws ?? 0) + (pendingVideos ?? 0) : null
  const stagingFiles = status?.staging_files ?? null
  const finalTotal = summary?.total_photos ?? null
  const publishedCount = summary?.total_published ?? null
  const homelabCount = backupAvail?.final?.local_count ?? null

  const tailscaleConnected = backupAvail?.connection !== null && backupAvail?.connection !== undefined

  // ── Backup sync state (remote check, 3-min poll) ────────────────────────────
  const backupSyncs = [backupRemote?.final, backupRemote?.raws, backupRemote?.videos].map(
    (s) => s?.needs_sync,
  )
  const backupKnown = backupSyncs.filter((n): n is number => typeof n === 'number' && n >= 0)
  const backupPending = backupKnown.reduce((a, b) => a + b, 0)
  // Only claim up-to-date when ALL three sources are known and zero behind.
  const backupUpToDate = backupKnown.length === 3 && backupPending === 0

  // ── Gallery sync state ──────────────────────────────────────────────────────
  const galleryUpToDate = gallery?.up_to_date === true
  const galleryPending = gallery?.pending ?? 0

  // ── Per-edge enablement: gate on preconditions only; a running job no longer blocks.
  //    Clicking while a job is running now ENQUEUES instead of being blocked.
  //    Returns the disable reason (string) or null if the edge is available.
  const edgeDisabled = useCallback(
    (op: ActiveOp): string | null => {
      if (dryRunMutation.isPending || modalOpen) return 'Finishing the current action…'
      switch (op) {
        case 'import':
          if (!status?.camera_connected) return 'Camera not connected'
          if (!cameraTotal) return 'Nothing on the camera to import'
          return null
        case 'finalize':
          if (!stagingFiles) return 'Staging is empty — nothing to finalize'
          return null
        case 'backup':
          if (!tailscaleConnected) return 'Homelab unreachable over Tailscale'
          if (!finalTotal) return 'Final is empty — nothing to back up'
          if (backupUpToDate) return 'Backups are up to date'
          return null
        case 'sync-gallery':
          if (!finalTotal) return 'Final is empty'
          if (!publishedCount) return 'No rating ≥ 4 photos to publish'
          if (galleryUpToDate) return 'Gallery is already up to date'
          return null
        case 'cleanup':
          if (!status?.ssd_connected) return 'Requires the external SSD (RAWs live there)'
          return null
        default:
          return null
      }
    },
    [
      dryRunMutation.isPending,
      modalOpen,
      status?.camera_connected,
      status?.ssd_connected,
      cameraTotal,
      stagingFiles,
      tailscaleConnected,
      finalTotal,
      publishedCount,
      backupUpToDate,
      galleryUpToDate,
    ],
  )

  // ── Edge geometry — one source of truth for connectors AND pills ─────────────
  // The pill sits at the bezier's t=0.5 point, which equals (cmx, midY) for this
  // control-point layout, so the pill always lands exactly on the connector.
  const edges = useMemo(() => {
    return EDGE_OPS.map((def) => {
      const from = geom[def.fromId]
      const to = geom[def.toId]
      const grad = {
        id: `edge-grad-${def.op}`,
        from: STAGE_COLOR[def.fromId],
        to: STAGE_COLOR[def.toId],
        x1: 0,
        y1: 0,
        x2: 0,
        y2: 0,
      }
      if (!from || !to) return { def, path: null as string | null, mid: null as Point | null, grad }
      const x1 = from.x + from.w / 2
      const x2 = to.x - to.w / 2
      const cmx = (x1 + x2) / 2
      const path = `M ${x1} ${from.y} C ${cmx} ${from.y}, ${cmx} ${to.y}, ${x2} ${to.y}`
      const mid: Point = { x: cmx, y: (from.y + to.y) / 2 }
      // Gradient runs along the connector's bounding box (userSpaceOnUse).
      grad.x1 = x1
      grad.y1 = from.y
      grad.x2 = x2
      grad.y2 = to.y
      return { def, path, mid, grad }
    })
  }, [geom])

  const isEdgeActive = (op: ActiveOp) => activeOp === op || hoveredOp === op

  // Label for the progress panel — active op takes priority, then last finished, then pending.
  const panelOpLabel =
    activeOp !== null
      ? OP_LABELS[activeOp]
      : lastFinishedOp !== null
        ? OP_LABELS[lastFinishedOp]
        : pendingOp
          ? pendingOp.label
          : 'Last operation'

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        gap: 16,
        height: 'calc(100dvh - 168px)',
      }}
    >
      {/* Status strip */}
      <div
        style={{
          alignSelf: 'flex-start',
          display: 'flex',
          alignItems: 'center',
          gap: 18,
          flexWrap: 'wrap',
          padding: '7px 14px',
          borderRadius: 999,
          border: `1px solid ${VX.surface.border}`,
          background: VX.surface.panel,
        }}
      >
        <StatusChip label="Camera" connected={status?.camera_connected ?? false} />
        <StatusChip
          label="SSD"
          connected={status?.ssd_connected ?? false}
          hint={status?.ssd_connected ? 'External SSD connected' : 'SSD not found — RAW import & cleanup unavailable'}
        />
        <StatusChip label="Tailscale" connected={tailscaleConnected} hint={tailscaleConnected ? 'Homelab reachable over Tailscale' : 'Homelab unreachable'} />
        {activeOp && (
          <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 12 }}>
            <span
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 7,
                fontSize: 12,
                fontWeight: 600,
                color: 'var(--vx-goodSolid)',
              }}
            >
              <motion.span
                style={{ width: 7, height: 7, borderRadius: '50%', background: 'var(--vx-goodSolid)' }}
                animate={{ scale: [1, 1.4, 1], opacity: [1, 0.6, 1] }}
                transition={{ duration: 1, repeat: Infinity }}
              />
              {OP_LABELS[activeOp]} running…
            </span>
          </div>
        )}
      </div>

      {/* Command surface: pipeline diagram + live console, one framed panel */}
      <div style={{ flex: '1 1 auto', display: 'flex', alignItems: 'stretch', minHeight: 200 }}>
        <div
          style={{
            width: '100%',
            display: 'flex',
            flexDirection: 'column',
            border: `1px solid ${VX.surface.border}`,
            borderRadius: 16,
            background: alpha(VX.neutral, 0.018),
            padding: '32px 28px',
          }}
        >
        {/* Diagram (fills the upper canvas, centered) */}
        <div style={{ flex: '1 1 auto', display: 'flex', alignItems: 'center', minHeight: 180 }}>
        <div
          ref={containerRef}
          style={{
            position: 'relative',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            gap: 8,
            width: '100%',
          }}
        >
          {/* Connector lines (drawn under nodes); flowing dash while a job runs */}
          <svg
            aria-hidden="true"
            style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', pointerEvents: 'none', zIndex: 0 }}
          >
            <defs>
              {edges.map(({ def, path, grad }) =>
                path ? (
                  <linearGradient
                    key={def.op}
                    id={grad.id}
                    gradientUnits="userSpaceOnUse"
                    x1={grad.x1}
                    y1={grad.y1}
                    x2={grad.x2}
                    y2={grad.y2}
                  >
                    <stop offset="0%" style={{ stopColor: grad.from }} />
                    <stop offset="100%" style={{ stopColor: grad.to }} />
                  </linearGradient>
                ) : null,
              )}
            </defs>
            {edges.map(({ def, path, grad }) => {
              if (!path) return null
              const highlighted = isEdgeActive(def.op)
              const flowing = activeOp === def.op
              return (
                <g key={def.op}>
                  {/* Connector always carries its from→to gradient (the pipeline flow);
                      opacity/width lift on hover/active. */}
                  <path
                    d={path}
                    fill="none"
                    stroke={`url(#${grad.id})`}
                    strokeWidth={highlighted ? 2 : 1.5}
                    strokeOpacity={highlighted ? (flowing ? 0.35 : 0.9) : 0.4}
                    style={{ transition: 'stroke-width 0.25s, stroke-opacity 0.25s' }}
                  />
                  {flowing && (
                    <motion.path
                      d={path}
                      fill="none"
                      stroke={grad.to}
                      strokeWidth={2.5}
                      strokeLinecap="round"
                      strokeDasharray="5 13"
                      initial={{ strokeDashoffset: 0 }}
                      animate={{ strokeDashoffset: -36 }}
                      transition={{ duration: 0.9, repeat: Infinity, ease: 'linear' }}
                    />
                  )}
                </g>
              )
            })}
          </svg>

          {/* Edge action pills (positioned exactly on the connector midpoint) */}
          {edges.map(({ def, mid }) => {
            let edgeStatus: { label: string; tone: 'good' | 'warn' } | null = null
            if (def.op === 'backup') {
              edgeStatus = backupUpToDate
                ? { label: 'Synced', tone: 'good' }
                : backupKnown.length > 0
                  ? { label: `${backupPending} behind`, tone: 'warn' }
                  : null
            } else if (def.op === 'sync-gallery') {
              edgeStatus = galleryUpToDate
                ? { label: 'Synced', tone: 'good' }
                : { label: `${galleryPending} pending`, tone: 'warn' }
            }
            return (
              <EdgePill
                key={def.op}
                def={def}
                mid={mid ?? undefined}
                active={isEdgeActive(def.op)}
                loading={dryRunMutation.isPending && dryRunMutation.variables?.op === def.op}
                disabledReason={edgeDisabled(def.op)}
                onHover={setHoveredOp}
                onRun={runEdgeOp}
                status={edgeStatus}
              />
            )
          })}

          {/* Camera */}
          <NodeCard
            nodeRef={cameraRef}
            index={0}
            label="Camera"
            color={VX.photo.camera}
            primaryCount={cameraTotal}
            primaryLabel="pending"
            secondaryCounts={[
              { label: 'photos', value: pendingPhotos },
              { label: 'RAWs', value: pendingRaws },
              { label: 'videos', value: pendingVideos },
            ]}
            statusDot={{ connected: status?.camera_connected ?? false, label: 'Camera' }}
            isActive={isEdgeActive('import')}
          />

          {/* Staging */}
          <NodeCard
            nodeRef={stagingRef}
            index={1}
            label="Staging"
            color={VX.photo.staging}
            primaryCount={stagingFiles}
            primaryLabel="awaiting finalize"
            isActive={isEdgeActive('finalize')}
          />

          {/* Final */}
          <NodeCard
            nodeRef={finalRef}
            index={2}
            label="Final"
            color={VX.photo.final}
            primaryCount={finalTotal}
            primaryLabel="total photos"
            isActive={isEdgeActive('backup') || isEdgeActive('sync-gallery')}
          />

          {/* Publish column */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 16, flex: '0 0 auto' }}>
            <NodeCard
              nodeRef={homelabRef}
              index={3}
              label="Homelab"
              color={VX.photo.final}
              primaryCount={homelabCount}
              primaryLabel="backed up"
              statusDot={{ connected: tailscaleConnected, label: 'Tailscale' }}
              isActive={isEdgeActive('backup')}
            />
            <NodeCard
              nodeRef={galleryRef}
              index={4}
              label="Gallery"
              color={VX.photo.published}
              primaryCount={publishedCount}
              primaryLabel="published"
              isActive={isEdgeActive('sync-gallery')}
            />
          </div>
        </div>
        </div>

        {/* Console: live job progress (running/done) or next-step + maintenance (idle) */}
        <div
          style={{
            marginTop: 28,
            paddingTop: 20,
            borderTop: `1px solid ${VX.surface.border}`,
            display: 'flex',
            flexDirection: 'column',
            justifyContent: 'center',
            gap: 12,
            minHeight: 60,
          }}
        >
        <AnimatePresence mode="wait">
          {(isJobRunning || eventsState.isDone || lastFinishedOp !== null) && (
            <JobProgressPanel
              key="progress"
              op={activeOp ?? lastFinishedOp}
              opLabel={panelOpLabel}
              state={eventsState}
              embedded
              onStop={() => cancelMutation.mutate()}
              stopping={cancelMutation.isPending}
              isRunning={isJobRunning}
            />
          )}
        </AnimatePresence>

        {/* Queue panel — shows queued / needs_confirm jobs below the progress panel */}
        <JobQueuePanel />

        {!isJobRunning && !eventsState.isDone && lastFinishedOp === null && (
          <AdviceBlock
            advices={advices}
            onRun={runAdvice}
            dryRunPending={dryRunMutation.isPending || modalOpen}
          />
        )}
        </div>
        </div>
      </div>

      <DryRunModal
        opened={modalOpen}
        onClose={() => setModalOpen(false)}
        onConfirm={() =>
          pendingOp &&
          startMutation.mutate({
            op: pendingOp.op,
            // exactOptionalPropertyTypes: omit `extra` when undefined.
            ...(pendingOp.extra !== undefined ? { extra: pendingOp.extra } : {}),
            // Pass the approved preview for destructive ops so the worker can re-validate
            // the deletion set at dispatch time (data-loss guard).
            approvedPreview: pendingOp.destructive ? preview : null,
          })
        }
        isConfirming={startMutation.isPending}
        opId={pendingOp?.op ?? 'import'}
        opLabel={pendingOp?.label ?? ''}
        isDestructive={pendingOp?.destructive ?? false}
        preview={preview}
      />
    </div>
  )
}
