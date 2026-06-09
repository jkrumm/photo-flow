import { useRef, useState, useLayoutEffect, useCallback } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { useQuery } from '@tanstack/react-query'
import { useNavigate } from '@tanstack/react-router'
import { Tooltip } from '@mantine/core'
import { VX } from '../../lib/charts/tokens'
import { alpha } from '../../lib/charts/utils/color'
import { statusQueries } from '../../lib/queries/status'
import { analyticsQueries } from '../../lib/queries/analytics'
import { backupQueries } from '../../lib/queries/backup'
import { useActiveJobStore } from '../../lib/store'

// ── Types ─────────────────────────────────────────────────────────────────────

interface NodeRect {
  cx: number // center x relative to hero container
  cy: number // center y relative to hero container
  w: number
  h: number
}

interface EdgeDef {
  key: string
  fromId: StageId
  toId: StageId
  op: string
  color: string
}

type StageId = 'camera' | 'staging' | 'final' | 'homelab' | 'gallery'

// ── Edge paths ─────────────────────────────────────────────────────────────────

function bezierH(
  fx: number,
  fy: number,
  tx: number,
  ty: number,
  fw: number,
  tw: number,
): string {
  const startX = fx + fw / 2
  const endX = tx - tw / 2
  const midX = (startX + endX) / 2
  return `M ${startX} ${fy} C ${midX} ${fy}, ${midX} ${ty}, ${endX} ${ty}`
}

// ── Particle ──────────────────────────────────────────────────────────────────

function FlowParticle({
  pathStr,
  color,
  delay,
}: {
  pathStr: string
  color: string
  delay: number
}) {
  return (
    <motion.div
      aria-hidden="true"
      style={{
        position: 'absolute',
        top: 0,
        left: 0,
        width: 7,
        height: 7,
        borderRadius: '50%',
        background: color,
        boxShadow: `0 0 6px ${alpha(color, 0.7)}`,
        pointerEvents: 'none',
        offsetPath: `path('${pathStr}')`,
        offsetDistance: '0%',
        offsetRotate: '0deg',
      }}
      initial={{ offsetDistance: '0%', opacity: 0 }}
      animate={{
        offsetDistance: ['0%', '100%'],
        opacity: [0, 0.9, 0.9, 0],
      }}
      exit={{ opacity: 0 }}
      transition={{
        duration: 1.6,
        delay,
        repeat: Infinity,
        ease: 'linear',
      }}
    />
  )
}

// ── Edge SVG ──────────────────────────────────────────────────────────────────

function EdgePath({
  pathStr,
  active,
  color,
}: {
  pathStr: string
  active: boolean
  color: string
}) {
  return (
    <>
      {/* base track */}
      <motion.path
        d={pathStr}
        fill="none"
        stroke={VX.surface.border}
        strokeWidth={1.5}
        animate={{ opacity: active ? 0.4 : 0.6 }}
        transition={{ duration: 0.4 }}
      />
      {/* active highlight */}
      <AnimatePresence>
        {active && (
          <motion.path
            key="highlight"
            d={pathStr}
            fill="none"
            stroke={color}
            strokeWidth={2}
            strokeDasharray="6 10"
            initial={{ opacity: 0, strokeDashoffset: 100 }}
            animate={{ opacity: 0.7, strokeDashoffset: [100, 0] }}
            exit={{ opacity: 0 }}
            transition={{
              strokeDashoffset: {
                duration: 1.2,
                repeat: Infinity,
                ease: 'linear',
              },
              opacity: { duration: 0.3 },
            }}
          />
        )}
      </AnimatePresence>
    </>
  )
}

// ── Node Card ─────────────────────────────────────────────────────────────────

interface NodeCardProps {
  id: StageId
  label: string
  color: string
  primaryCount: number | string | null
  primaryLabel: string
  secondaryCounts?: { label: string; value: number | string | null }[]
  statusDot?: { connected: boolean; label: string }
  isActive: boolean
  op: string | null
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
  op,
  nodeRef,
  index,
}: NodeCardProps) {
  const navigate = useNavigate()

  const handleClick = useCallback(() => {
    if (op) void navigate({ to: '/operations' })
  }, [navigate, op])

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (op && (e.key === 'Enter' || e.key === ' ')) {
        e.preventDefault()
        void navigate({ to: '/operations' })
      }
    },
    [navigate, op],
  )

  return (
    <motion.div
      ref={nodeRef}
      role={op ? 'button' : undefined}
      tabIndex={op ? 0 : undefined}
      aria-label={op ? `${label} — go to operations` : label}
      onClick={op ? handleClick : undefined}
      onKeyDown={op ? handleKeyDown : undefined}
      initial={{ opacity: 0, y: 14 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{
        duration: 0.45,
        delay: index * 0.08,
        ease: [0.22, 1, 0.36, 1],
      }}
      style={{
        position: 'relative',
        background: VX.surface.panel,
        border: `1px solid ${isActive ? alpha(color, 0.55) : VX.surface.border}`,
        borderRadius: 10,
        padding: '14px 16px',
        minWidth: 148,
        flex: '1 1 148px',
        cursor: op ? 'pointer' : 'default',
        userSelect: 'none',
        transition: 'border-color 0.3s, box-shadow 0.3s',
        boxShadow: isActive
          ? `0 0 0 1px ${alpha(color, 0.3)}, ${VX.shadowCard}`
          : VX.shadowCard,
        overflow: 'hidden',
      }}
      whileHover={op ? { scale: 1.015 } : {}}
      whileTap={op ? { scale: 0.98 } : {}}
    >
      {/* colored top bar */}
      <motion.div
        aria-hidden="true"
        style={{
          position: 'absolute',
          top: 0,
          left: 0,
          right: 0,
          height: 3,
          borderRadius: '10px 10px 0 0',
          background: color,
        }}
        animate={{ opacity: isActive ? 1 : 0.6 }}
        transition={{ duration: 0.3 }}
      />

      {/* header: label + status dot */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          marginBottom: 10,
        }}
      >
        <span
          style={{
            fontSize: 11,
            fontWeight: 600,
            letterSpacing: '0.06em',
            textTransform: 'uppercase',
            color: alpha(VX.neutral, 0.7),
          }}
        >
          {label}
        </span>
        {statusDot !== undefined && (
          <Tooltip
            label={`${statusDot.label}: ${statusDot.connected ? 'connected' : 'not found'}`}
            withArrow
          >
            <motion.div
              aria-label={`${statusDot.label} ${statusDot.connected ? 'connected' : 'disconnected'}`}
              style={{
                width: 7,
                height: 7,
                borderRadius: '50%',
                background: statusDot.connected ? 'var(--vx-goodSolid)' : alpha(VX.neutral, 0.3),
                flexShrink: 0,
              }}
              animate={{
                scale: statusDot.connected && isActive ? [1, 1.35, 1] : 1,
              }}
              transition={{
                duration: 1.2,
                repeat: statusDot.connected && isActive ? Infinity : 0,
              }}
            />
          </Tooltip>
        )}
      </div>

      {/* primary count */}
      <div
        style={{
          fontSize: 28,
          fontWeight: 700,
          lineHeight: 1,
          fontVariantNumeric: 'tabular-nums',
          color: primaryCount !== null && primaryCount !== 0 ? color : alpha(VX.neutral, 0.35),
          marginBottom: 4,
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

      {/* secondary counts */}
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

// ── Arrow ─────────────────────────────────────────────────────────────────────

function ArrowEdge({ active, color }: { active: boolean; color: string }) {
  return (
    <motion.svg
      width={28}
      height={24}
      viewBox="0 0 28 24"
      fill="none"
      aria-hidden="true"
      style={{ flexShrink: 0 }}
      animate={{ opacity: active ? 1 : 0.45 }}
      transition={{ duration: 0.3 }}
    >
      <motion.path
        d="M2 12 L22 12 M15 5 L22 12 L15 19"
        stroke={active ? color : VX.surface.border}
        strokeWidth={1.5}
        strokeLinecap="round"
        strokeLinejoin="round"
        animate={{ stroke: active ? color : VX.surface.border }}
        transition={{ duration: 0.3 }}
      />
    </motion.svg>
  )
}

// ── Publish Branch ────────────────────────────────────────────────────────────

function BranchArrow() {
  return (
    <svg
      width={24}
      height={80}
      viewBox="0 0 24 80"
      fill="none"
      aria-hidden="true"
      style={{ flexShrink: 0, alignSelf: 'stretch' }}
    >
      {/* upper branch */}
      <path
        d="M4 40 L4 20 L20 20"
        stroke={VX.surface.border}
        strokeWidth={1.5}
        strokeLinecap="round"
        fill="none"
      />
      {/* lower branch */}
      <path
        d="M4 40 L4 60 L20 60"
        stroke={VX.surface.border}
        strokeWidth={1.5}
        strokeLinecap="round"
        fill="none"
      />
    </svg>
  )
}

// ── Main PipelineHero ──────────────────────────────────────────────────────────

/**
 * Pipeline hero — the signature screen.
 *
 * Renders 4+2 stage nodes (Camera → Staging → Final → Homelab/Gallery)
 * with live counts from /status, /status/pending, /analytics/summary,
 * /backup/availability and animated edges driven by the active-job store.
 *
 * Designed for desktop-first (macOS control panel) — responsive down to ~600px.
 */
export function PipelineHero() {
  const containerRef = useRef<HTMLDivElement | null>(null)
  const cameraRef = useRef<HTMLDivElement | null>(null)
  const stagingRef = useRef<HTMLDivElement | null>(null)
  const finalRef = useRef<HTMLDivElement | null>(null)
  const homelabRef = useRef<HTMLDivElement | null>(null)
  const galleryRef = useRef<HTMLDivElement | null>(null)

  const [svgPaths, setSvgPaths] = useState<Record<string, string>>({})
  const [svgSize, setSvgSize] = useState({ w: 0, h: 0 })

  const activeOp = useActiveJobStore((s) => s.activeOp)

  // ── Data queries ───────────────────────────────────────────────────────────
  const { data: status } = useQuery(statusQueries.status())
  const { data: pending } = useQuery(statusQueries.pending())
  const { data: summary } = useQuery(analyticsQueries.summary())
  const { data: backupAvail } = useQuery(backupQueries.availability())

  // ── Layout measurement ─────────────────────────────────────────────────────
  const measurePaths = useCallback(() => {
    const container = containerRef.current
    if (!container) return
    const cr = container.getBoundingClientRect()
    const w = cr.width
    const h = cr.height

    function mid(ref: React.RefObject<HTMLDivElement | null>): NodeRect | null {
      if (!ref.current) return null
      const r = ref.current.getBoundingClientRect()
      return {
        cx: r.left - cr.left + r.width / 2,
        cy: r.top - cr.top + r.height / 2,
        w: r.width,
        h: r.height,
      }
    }

    const cam = mid(cameraRef)
    const stg = mid(stagingRef)
    const fin = mid(finalRef)
    const hom = mid(homelabRef)
    const gal = mid(galleryRef)

    if (!cam || !stg || !fin || !hom || !gal) return

    setSvgSize({ w, h })
    setSvgPaths({
      'camera-staging': bezierH(cam.cx, cam.cy, stg.cx, stg.cy, cam.w, stg.w),
      'staging-final': bezierH(stg.cx, stg.cy, fin.cx, fin.cy, stg.w, fin.w),
      'final-homelab': bezierH(fin.cx, fin.cy, hom.cx, hom.cy, fin.w, hom.w),
      'final-gallery': bezierH(fin.cx, fin.cy, gal.cx, gal.cy, fin.w, gal.w),
    })
  }, [])

  useLayoutEffect(() => {
    measurePaths()
    const ro = new ResizeObserver(measurePaths)
    if (containerRef.current) ro.observe(containerRef.current)
    return () => ro.disconnect()
  }, [measurePaths])

  // ── Edge definitions ──────────────────────────────────────────────────────
  const EDGES: EdgeDef[] = [
    { key: 'camera-staging', fromId: 'camera', toId: 'staging', op: 'import', color: VX.photo.camera },
    { key: 'staging-final', fromId: 'staging', toId: 'final', op: 'finalize', color: VX.photo.staging },
    { key: 'final-homelab', fromId: 'final', toId: 'homelab', op: 'backup', color: VX.photo.final },
    { key: 'final-gallery', fromId: 'final', toId: 'gallery', op: 'sync-gallery', color: VX.photo.published },
  ]

  // ── Counts ───────────────────────────────────────────────────────────────
  const pendingPhotos = pending?.pending_photos ?? null
  const pendingRaws = pending?.pending_raws ?? null
  const pendingVideos = pending?.pending_videos ?? null
  const cameraTotal =
    pendingPhotos !== null ? (pendingPhotos + (pendingRaws ?? 0) + (pendingVideos ?? 0)) : null

  const stagingFiles = status?.staging_files ?? null
  const finalTotal = summary?.total_photos ?? null
  const publishedCount = summary?.total_published ?? null
  const homelabCount = backupAvail?.final?.local_count ?? null
  const galleryCount = backupAvail?.final?.remote_count ?? null

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        gap: 4,
      }}
    >
      {/* legend row */}
      <div
        style={{
          display: 'flex',
          gap: 16,
          alignItems: 'center',
          marginBottom: 4,
          fontSize: 11,
          color: alpha(VX.neutral, 0.55),
        }}
      >
        {[
          { color: VX.photo.camera, label: 'Camera' },
          { color: VX.photo.staging, label: 'Staging' },
          { color: VX.photo.final, label: 'Final' },
          { color: VX.photo.published, label: 'Publish' },
        ].map(({ color, label }) => (
          <span key={label} style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
            <span
              style={{ width: 8, height: 8, borderRadius: 2, background: color, flexShrink: 0 }}
            />
            {label}
          </span>
        ))}
        {activeOp && (
          <span
            style={{
              marginLeft: 'auto',
              display: 'flex',
              alignItems: 'center',
              gap: 6,
              color: 'var(--vx-goodSolid)',
            }}
          >
            <motion.span
              style={{
                display: 'inline-block',
                width: 7,
                height: 7,
                borderRadius: '50%',
                background: 'var(--vx-goodSolid)',
                flexShrink: 0,
              }}
              animate={{ scale: [1, 1.4, 1], opacity: [1, 0.6, 1] }}
              transition={{ duration: 1, repeat: Infinity }}
            />
            {activeOp} running
          </span>
        )}
      </div>

      {/* hero layout: Camera + Staging + Final + branch(Homelab, Gallery) */}
      <div
        ref={containerRef}
        style={{
          position: 'relative',
          display: 'flex',
          alignItems: 'center',
          gap: 6,
          flexWrap: 'nowrap',
          minHeight: 160,
        }}
      >
        {/* SVG overlay for paths + particles */}
        {svgSize.w > 0 && (
          <svg
            aria-hidden="true"
            style={{
              position: 'absolute',
              inset: 0,
              pointerEvents: 'none',
              zIndex: 0,
            }}
            width={svgSize.w}
            height={svgSize.h}
            viewBox={`0 0 ${svgSize.w} ${svgSize.h}`}
          >
            {EDGES.map((edge) => {
              const pathStr = svgPaths[edge.key]
              if (!pathStr) return null
              const isActive = activeOp === edge.op
              return (
                <g key={edge.key}>
                  <EdgePath pathStr={pathStr} active={isActive} color={edge.color} />
                </g>
              )
            })}
          </svg>
        )}

        {/* Particle overlays on edges (HTML, uses CSS offset-path) */}
        {svgSize.w > 0 && EDGES.map((edge) => {
          const pathStr = svgPaths[edge.key]
          const isActive = activeOp === edge.op
          if (!pathStr || !isActive) return null
          return (
            <AnimatePresence key={edge.key}>
              {[0, 1, 2].map((i) => (
                <FlowParticle
                  key={i}
                  pathStr={pathStr}
                  color={edge.color}
                  delay={i * 0.53}
                />
              ))}
            </AnimatePresence>
          )
        })}

        {/* Camera node */}
        <NodeCard
          id="camera"
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
          isActive={activeOp === 'import'}
          op="import"
        />

        <ArrowEdge active={activeOp === 'import'} color={VX.photo.camera} />

        {/* Staging node */}
        <NodeCard
          id="staging"
          nodeRef={stagingRef}
          index={1}
          label="Staging"
          color={VX.photo.staging}
          primaryCount={stagingFiles}
          primaryLabel="awaiting finalize"
          isActive={activeOp === 'finalize'}
          op="finalize"
        />

        <ArrowEdge active={activeOp === 'finalize'} color={VX.photo.staging} />

        {/* Final node */}
        <NodeCard
          id="final"
          nodeRef={finalRef}
          index={2}
          label="Final"
          color={VX.photo.final}
          primaryCount={finalTotal}
          primaryLabel="total photos"
          secondaryCounts={[{ label: 'published', value: publishedCount }]}
          statusDot={undefined}
          isActive={activeOp === 'sync-gallery' || activeOp === 'backup'}
          op={null}
        />

        {/* Branch arrows to Homelab + Gallery */}
        <BranchArrow />

        {/* Publish column: Homelab + Gallery */}
        <div
          style={{
            display: 'flex',
            flexDirection: 'column',
            gap: 8,
            flex: '1 1 148px',
            minWidth: 140,
          }}
        >
          <NodeCard
            id="homelab"
            nodeRef={homelabRef}
            index={3}
            label="Homelab"
            color={VX.photo.published}
            primaryCount={homelabCount}
            primaryLabel="backed up"
            statusDot={{ connected: backupAvail?.connection !== null && backupAvail?.connection !== undefined, label: 'Tailscale' }}
            isActive={activeOp === 'backup'}
            op="backup"
          />

          <NodeCard
            id="gallery"
            nodeRef={galleryRef}
            index={4}
            label="Gallery"
            color={VX.photo.published}
            primaryCount={galleryCount}
            primaryLabel="published"
            isActive={activeOp === 'sync-gallery'}
            op="sync-gallery"
          />
        </div>
      </div>

      {/* SSD indicator — below the hero row */}
      {status !== undefined && (
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ delay: 0.45 }}
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 6,
            marginTop: 4,
            fontSize: 11,
            color: alpha(VX.neutral, 0.5),
          }}
        >
          <div
            style={{
              width: 6,
              height: 6,
              borderRadius: '50%',
              background: status.ssd_connected ? 'var(--vx-goodSolid)' : alpha(VX.neutral, 0.3),
              flexShrink: 0,
            }}
          />
          SSD {status.ssd_connected ? 'connected' : 'not found'}
          {!status.ssd_connected && (
            <span style={{ color: alpha(VX.neutral, 0.4) }}>
              — RAW import and cleanup unavailable
            </span>
          )}
        </motion.div>
      )}
    </div>
  )
}
