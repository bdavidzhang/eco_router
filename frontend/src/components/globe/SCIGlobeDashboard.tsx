import React, { useRef } from 'react'
import { GlobeScene, GlobeSceneHandle } from './GlobeScene'
import { useGlobeStore } from './useGlobeStore'
import { sciToColor, REGIONS, classifyTask, TaskType } from './sciUtils'

// ── WebGL error boundary ──────────────────────────────────────────────────────

class GlobeErrorBoundary extends React.Component<
  { children: React.ReactNode },
  { hasError: boolean }
> {
  state = { hasError: false }

  static getDerivedStateFromError() {
    return { hasError: true }
  }

  componentDidCatch() {
    setTimeout(() => this.setState({ hasError: false }), 2000)
  }

  render() {
    if (this.state.hasError) {
      return (
        <div style={{
          height: 480,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          background: 'var(--color-background-secondary)',
          borderRadius: 'var(--border-radius-lg)',
          border: '0.5px solid var(--color-border-tertiary)',
          color: 'var(--color-text-secondary)',
          fontSize: 14,
        }}>
          Globe unavailable — WebGL not supported. Retrying…
        </div>
      )
    }
    return this.props.children
  }
}

// ── Legend bar ────────────────────────────────────────────────────────────────

function LegendBar() {
  return (
    <div style={{ marginTop: 10, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 3 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 11, color: 'var(--color-text-tertiary)' }}>
        <span>Low</span>
        <div style={{
          width: 120,
          height: 6,
          borderRadius: 3,
          background: 'linear-gradient(to right, #5DB800, #D4860A, #C8470A, #E03030)',
        }} />
        <span>High</span>
      </div>
      <div style={{ fontSize: 10, color: 'var(--color-text-tertiary)' }}>SCI gCO₂eq / 1k tokens</div>
    </div>
  )
}

// ── Metric card ───────────────────────────────────────────────────────────────

interface MetricCardProps {
  label: string
  value: React.ReactNode
  sub: string
}

function MetricCard({ label, value, sub }: MetricCardProps) {
  return (
    <div style={{
      background: 'var(--color-background-secondary)',
      border: '0.5px solid var(--color-border-tertiary)',
      borderRadius: 'var(--border-radius-md)',
      padding: '12px 14px',
    }}>
      <div style={{ fontSize: 12, color: 'var(--color-text-tertiary)', marginBottom: 4 }}>{label}</div>
      <div style={{ fontSize: 20, fontWeight: 500, color: 'var(--color-text-primary)', lineHeight: 1.2 }}>{value}</div>
      <div style={{ fontSize: 11, color: 'var(--color-text-tertiary)', marginTop: 2 }}>{sub}</div>
    </div>
  )
}

// ── Task input card ───────────────────────────────────────────────────────────

const TASK_LABEL: Record<TaskType, { label: string; badgeColor: string }> = {
  code:   { label: 'Code generation', badgeColor: '#00C8A0' },
  reason: { label: 'Reasoning',       badgeColor: '#5DB800' },
  summ:   { label: 'Summarization',   badgeColor: '#D4860A' },
}

const CONFIDENCE_COLOR: Record<'high' | 'medium' | 'low', string> = {
  high:   '#5DB800',
  medium: '#D4860A',
  low:    '#888',
}

interface TaskInputCardProps {
  onTaskChange: () => void
}

function TaskInputCard({ onTaskChange }: TaskInputCardProps) {
  const [inputText, setInputText] = React.useState('')
  const [result, setResult] = React.useState<{
    task: TaskType
    confidence: 'high' | 'medium' | 'low'
    matched: string[]
  } | null>(null)
  const { setActiveTask } = useGlobeStore()

  const handleSubmit = () => {
    if (!inputText.trim()) return
    const classification = classifyTask(inputText)
    setResult(classification)
    setActiveTask(classification.task)
    onTaskChange()
  }

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') handleSubmit()
  }

  const taskInfo = result ? TASK_LABEL[result.task] : null

  return (
    <div style={{
      background: 'var(--color-background-secondary)',
      border: '0.5px solid var(--color-border-tertiary)',
      borderRadius: 'var(--border-radius-lg)',
      padding: 14,
    }}>
      <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--color-text-secondary)', marginBottom: 8 }}>
        Smart task routing
      </div>
      <div style={{ display: 'flex', gap: 6 }}>
        <input
          type="text"
          value={inputText}
          onChange={e => setInputText(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="e.g. write a sorting algorithm…"
          style={{
            flex: 1,
            padding: '7px 10px',
            background: 'var(--color-background-primary)',
            border: '1px solid var(--color-border-tertiary)',
            borderRadius: 'var(--border-radius-md)',
            fontSize: 12,
            color: 'var(--color-text-primary)',
            fontFamily: 'inherit',
            outline: 'none',
          }}
        />
        <button
          onClick={handleSubmit}
          style={{
            padding: '7px 10px',
            background: '#5DB800',
            border: 'none',
            borderRadius: 'var(--border-radius-md)',
            fontSize: 11,
            fontWeight: 600,
            color: '#fff',
            cursor: 'pointer',
            fontFamily: 'inherit',
            whiteSpace: 'nowrap',
          }}
        >
          Route →
        </button>
      </div>

      {result && taskInfo && (
        <div style={{ marginTop: 10, padding: '8px 10px', background: 'rgba(93,184,0,0.06)', border: '1px solid rgba(93,184,0,0.2)', borderRadius: 'var(--border-radius-md)' }}>
          {result.confidence === 'low' ? (
            <div style={{ fontSize: 11, color: 'var(--color-text-tertiary)', fontStyle: 'italic' }}>
              Couldn't determine task type — defaulting to Reasoning
            </div>
          ) : (
            <>
              <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 4 }}>
                <span style={{
                  fontSize: 10, fontWeight: 600, color: '#fff',
                  background: taskInfo.badgeColor,
                  padding: '2px 6px', borderRadius: 3,
                }}>
                  {taskInfo.label}
                </span>
                <span style={{
                  fontSize: 10, fontWeight: 600,
                  color: CONFIDENCE_COLOR[result.confidence],
                  background: `${CONFIDENCE_COLOR[result.confidence]}22`,
                  padding: '2px 6px', borderRadius: 3,
                }}>
                  {result.confidence} confidence
                </span>
              </div>
              <div style={{ fontSize: 11, color: 'var(--color-text-tertiary)' }}>
                → auto-selected above
              </div>
            </>
          )}
        </div>
      )}
    </div>
  )
}

// ── Task selector card ────────────────────────────────────────────────────────

const TASKS: { id: 'code' | 'reason' | 'summ'; label: string; badgeColor: string; badgeText: string }[] = [
  { id: 'code',   label: 'Code generation', badgeColor: '#00C8A0', badgeText: 'High quality' },
  { id: 'reason', label: 'Reasoning',       badgeColor: '#5DB800', badgeText: 'Balanced'     },
  { id: 'summ',   label: 'Summarization',   badgeColor: '#D4860A', badgeText: 'Efficiency'   },
]

interface TaskCardProps {
  onTaskChange: () => void
}

function TaskCard({ onTaskChange }: TaskCardProps) {
  const { activeTask, setActiveTask } = useGlobeStore()

  return (
    <div style={{
      background: 'var(--color-background-secondary)',
      border: '0.5px solid var(--color-border-tertiary)',
      borderRadius: 'var(--border-radius-lg)',
      padding: 14,
    }}>
      <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--color-text-secondary)', marginBottom: 8 }}>
        Task type
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
        {TASKS.map(t => (
          <button
            key={t.id}
            onClick={() => { setActiveTask(t.id); onTaskChange() }}
            style={{
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'center',
              width: '100%',
              padding: '8px 10px',
              background: activeTask === t.id ? 'rgba(93,184,0,0.08)' : 'transparent',
              border: activeTask === t.id ? '1px solid #5DB800' : '1px solid var(--color-border-tertiary)',
              borderRadius: 'var(--border-radius-md)',
              cursor: 'pointer',
              fontSize: 13,
              color: 'var(--color-text-primary)',
              fontFamily: 'inherit',
              textAlign: 'left',
              transition: 'border-color 0.15s, background 0.15s',
            }}
          >
            <span>{t.label}</span>
            <span style={{
              fontSize: 10,
              fontWeight: 600,
              color: '#fff',
              background: t.badgeColor,
              padding: '2px 6px',
              borderRadius: 3,
              letterSpacing: '0.02em',
            }}>
              {t.badgeText}
            </span>
          </button>
        ))}
      </div>
    </div>
  )
}

// ── Model ranking card ────────────────────────────────────────────────────────

function ModelRankingCard() {
  const { activeModels } = useGlobeStore()
  const models = activeModels()

  return (
    <div style={{
      background: 'var(--color-background-secondary)',
      border: '0.5px solid var(--color-border-tertiary)',
      borderRadius: 'var(--border-radius-lg)',
      padding: 14,
    }}>
      <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--color-text-secondary)', marginBottom: 8 }}>
        Model ranking
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 7 }}>
        {models.map(m => (
          <div key={m.name} style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <div style={{
              width: 8, height: 8, borderRadius: '50%', flexShrink: 0,
              background: m.best ? '#5DB800' : 'transparent',
              border: m.best ? 'none' : '1.5px solid var(--color-border-tertiary)',
            }} />
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontSize: 13, color: 'var(--color-text-primary)', fontWeight: m.best ? 600 : 400 }}>
                {m.name}
                {m.best && <span style={{ marginLeft: 4, color: '#76FF03' }}>✦</span>}
              </div>
              <div style={{ fontSize: 11, color: 'var(--color-text-tertiary)', marginTop: 1 }}>{m.note}</div>
            </div>
            <div style={{ fontSize: 13, fontWeight: 600, color: sciToColor(m.sci), flexShrink: 0 }}>
              {m.sci}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

// ── Region list card ──────────────────────────────────────────────────────────

interface RegionListCardProps {
  onRegionClick: (id: string) => void
}

function RegionListCard({ onRegionClick }: RegionListCardProps) {
  const { activeRegionId, setActiveRegion, worstSci } = useGlobeStore()
  const maxSci = worstSci()
  const sorted = [...REGIONS].sort((a, b) => a.sci - b.sci)

  return (
    <div style={{
      background: 'var(--color-background-secondary)',
      border: '0.5px solid var(--color-border-tertiary)',
      borderRadius: 'var(--border-radius-lg)',
      padding: 14,
    }}>
      <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--color-text-secondary)', marginBottom: 8 }}>
        Region SCI scores
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 7 }}>
        {sorted.map(r => {
          const isActive = r.id === activeRegionId
          const color = sciToColor(r.sci)
          return (
            <div
              key={r.id}
              onClick={() => { setActiveRegion(r.id); onRegionClick(r.id) }}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 8,
                cursor: 'pointer',
                padding: '3px 0',
              }}
            >
              <div style={{
                width: 10, height: 10, borderRadius: '50%', flexShrink: 0,
                background: isActive ? color : 'transparent',
                border: `1.5px solid ${color}`,
              }} />
              <div style={{
                fontSize: 12,
                fontWeight: isActive ? 500 : 400,
                color: 'var(--color-text-primary)',
                width: 90,
                flexShrink: 0,
                whiteSpace: 'nowrap',
                overflow: 'hidden',
                textOverflow: 'ellipsis',
              }}>
                {r.name}
              </div>
              <div style={{ flex: 1, height: 4, background: 'var(--color-border-tertiary)', borderRadius: 2, overflow: 'hidden' }}>
                <div style={{
                  width: `${(r.sci / maxSci) * 100}%`,
                  height: '100%',
                  background: color,
                  borderRadius: 2,
                }} />
              </div>
              <div style={{ fontSize: 12, fontWeight: 600, color, flexShrink: 0, minWidth: 28, textAlign: 'right' }}>
                {r.sci}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

// ── SCIGlobeDashboard (top-level export) ──────────────────────────────────────

export function SCIGlobeDashboard() {
  const sceneRef = useRef<GlobeSceneHandle>(null)
  const { activeRegion, activeModels, savedVsWorst } = useGlobeStore()

  const region = activeRegion()
  const models = activeModels()
  const saved  = savedVsWorst()
  const savedColor = saved >= 30 ? '#5DB800' : '#D4860A'

  const flyToActive = () => {
    sceneRef.current?.flyTo(useGlobeStore.getState().activeRegionId)
  }

  return (
    <div style={{ padding: '1rem 0', fontFamily: 'var(--font)' }}>
      {/* Header */}
      <div style={{ marginBottom: 16, padding: '0 1rem' }}>
        <h2 style={{ fontSize: 18, fontWeight: 500, color: 'var(--color-text-primary)', margin: 0 }}>
          SCI world model — inference region routing
        </h2>
        <p style={{ fontSize: 13, color: 'var(--color-text-secondary)', marginTop: 4 }}>
          Real-time Software Carbon Intensity by compute region. System routes inference to the
          lowest-SCI available region for your task type.
        </p>
      </div>

      {/* Metric cards */}
      <div style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(4, 1fr)',
        gap: 10,
        marginBottom: 16,
        padding: '0 1rem',
      }}>
        <MetricCard
          label="Active region"
          value={region.name}
          sub={region.sub}
        />
        <MetricCard
          label="Current SCI"
          value={<span style={{ color: sciToColor(region.sci) }}>{region.sci}</span>}
          sub="gCO₂eq / 1k tokens"
        />
        <MetricCard
          label="Best model"
          value={models[0].name}
          sub={models[0].note}
        />
        <MetricCard
          label="CO₂ saved vs worst"
          value={<span style={{ color: savedColor }}>{saved}%</span>}
          sub="vs highest SCI region"
        />
      </div>

      {/* Main panel: globe + sidebar */}
      <div style={{
        display: 'grid',
        gridTemplateColumns: '1fr 300px',
        gap: 12,
        alignItems: 'start',
        padding: '0 1rem',
      }}>
        {/* Globe column */}
        <div>
          <div style={{
            height: 480,
            background: 'var(--color-background-primary)',
            borderRadius: 'var(--border-radius-lg)',
            border: '0.5px solid var(--color-border-tertiary)',
            overflow: 'hidden',
          }}>
            <GlobeErrorBoundary>
              <GlobeScene ref={sceneRef} />
            </GlobeErrorBoundary>
          </div>
          <LegendBar />
        </div>

        {/* Sidebar */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          <TaskInputCard onTaskChange={flyToActive} />
          <TaskCard onTaskChange={flyToActive} />
          <ModelRankingCard />
          <RegionListCard
            onRegionClick={(id) => sceneRef.current?.flyTo(id)}
          />
        </div>
      </div>
    </div>
  )
}
