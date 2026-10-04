import { Handle, Position, type Node, type NodeProps } from '@xyflow/react'
import { ArrowUpRight, BookOpen, ChevronRight, Info, Maximize2, PenLine } from 'lucide-react'
import { useContext } from 'react'
import { kindStyle } from '../../lib/graphStyle'
import { FocusContext, InspectContext } from './focus'
import type { ClusterData, EntityData } from './layout'

const hueClass = (hue?: number | null) => (hue === null || hue === undefined ? 'hue-none' : `hue-${hue % 8}`)

function useDimmed(id: string) {
  const near = useContext(FocusContext)
  return near !== null && !near.has(id)
}

function Handles({ direction }: { direction: EntityData['direction'] }) {
  const horizontal = direction === 'RIGHT'
  return (
    <>
      <Handle type="target" position={horizontal ? Position.Left : Position.Top} className="ghandle" />
      <Handle type="source" position={horizontal ? Position.Right : Position.Bottom} className="ghandle" />
    </>
  )
}

/** The small (i) on a tile: open its details without zooming in */
function InspectButton({ id }: { id: string }) {
  const inspect = useContext(InspectContext)
  return (
    <button
      className="tile-info nodrag"
      title="Details"
      aria-label="Details"
      onClick={(e) => {
        e.stopPropagation()
        inspect(id)
      }}
    >
      <Info size={14} />
    </button>
  )
}

export function EntityNode({ id, data, selected }: NodeProps<Node<EntityData>>) {
  const { node, direction } = data
  const style = kindStyle(node.kind)
  const Icon = style.icon
  const dimmed = useDimmed(id)
  const isFlow = node.kind === 'flow' || node.kind === 'endpoint'
  const [method, ...rest] = isFlow ? node.label.split(' ') : ['']
  return (
    <div
      className={[
        'gnode',
        `tone-${style.tone}`,
        selected ? 'is-selected' : '',
        dimmed ? 'is-dimmed' : '',
        node.stub ? 'is-stub' : '',
        node.kind === 'function' && !node.significant ? 'is-quiet' : '',
      ].join(' ')}
    >
      <span className="gnode-icon" aria-hidden="true">
        <Icon size={15} strokeWidth={2} />
      </span>
      <span className="gnode-text">
        <span className="gnode-title">
          {isFlow && method ? <span className={`method m-${method.toLowerCase()}`}>{method}</span> : null}
          <span className="gnode-label">{isFlow && method ? rest.join(' ') : node.label}</span>
        </span>
        <span className="gnode-sub">
          {node.kind === 'function' && node.is_async ? 'async · ' : ''}
          {node.sub ?? style.label}
        </span>
      </span>
      {node.drill ? <span className="gnode-drill" title="Double-click to open">›</span> : null}
      <Handles direction={direction} />
    </div>
  )
}

const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? '' : 's'}`

/** A space at the organization or space level: what's in it, without its inner workings */
export function SpaceNode({ id, data, selected }: NodeProps<Node<EntityData>>) {
  const { node } = data
  const s = node.stats ?? {}
  const Icon = kindStyle('space').icon
  const dimmed = useDimmed(id)
  const facts = [
    s.applications ? plural(s.applications, 'application') : null,
    s.flows ? plural(s.flows, 'flow') : null,
    s.datastores ? plural(s.datastores, 'data store') : null,
    s.spaces ? plural(s.spaces, 'sub-space') : null,
  ].filter(Boolean)
  const preview = node.preview ?? []
  const more = (s.applications ?? 0) - preview.length
  return (
    <div className={['tile', 'tile-space', hueClass(node.hue), selected ? 'is-selected' : '', dimmed ? 'is-dimmed' : ''].join(' ')}>
      <div className="tile-head">
        <span className="tile-icon"><Icon size={18} /></span>
        <div className="tile-titles">
          <span className="tile-kicker">Space</span>
          <span className="tile-title">{node.label}</span>
        </div>
        <span className="tile-enter" title="Click to zoom in" aria-hidden="true"><Maximize2 size={13} /></span>
        <InspectButton id={id} />
      </div>
      {node.description ? <p className="tile-desc">{node.description}</p> : null}
      <div className="tile-facts">{facts.length ? facts.join(' · ') : 'Nothing ingested yet'}</div>
      {preview.length > 0 && (
        <div className="tile-preview">
          {preview.slice(0, 4).map((name) => (
            <span key={name} className="tile-chip">{name}</span>
          ))}
          {more + Math.max(0, preview.length - 4) > 0 && <span className="tile-chip more">+{more + Math.max(0, preview.length - 4)}</span>}
        </div>
      )}
      <Handles direction={data.direction} />
    </div>
  )
}

/** An application at the space level */
export function AppNode({ id, data, selected }: NodeProps<Node<EntityData>>) {
  const { node } = data
  const s = node.stats ?? {}
  const Icon = kindStyle('application').icon
  const dimmed = useDimmed(id)
  return (
    <div className={['tile', 'tile-app', hueClass(node.hue), selected ? 'is-selected' : '', dimmed ? 'is-dimmed' : '', node.stub ? 'is-stub' : ''].join(' ')}>
      <div className="tile-head">
        <span className="tile-icon"><Icon size={17} /></span>
        <div className="tile-titles">
          <span className="tile-kicker">Application</span>
          <span className="tile-title">{node.label}</span>
        </div>
        <span className="tile-enter" title="Click to zoom in" aria-hidden="true"><Maximize2 size={13} /></span>
        <InspectButton id={id} />
      </div>
      <div className="tile-facts">
        {plural(s.interfaces ?? 0, 'endpoint')} · {plural(s.flows ?? 0, 'flow')}
      </div>
      <Handles direction={data.direction} />
    </div>
  )
}

/** Something outside the current space that talks to it */
export function GhostNode({ id, data, selected }: NodeProps<Node<EntityData>>) {
  const { node } = data
  const Icon = kindStyle(node.kind).icon
  const dimmed = useDimmed(id)
  return (
    <div className={['tile', 'tile-ghost', hueClass(node.hue), selected ? 'is-selected' : '', dimmed ? 'is-dimmed' : ''].join(' ')}>
      <div className="tile-head">
        <span className="tile-icon"><Icon size={15} /></span>
        <div className="tile-titles">
          <span className="tile-kicker">Outside{node.sub ? ` · ${node.sub}` : ''}</span>
          <span className="tile-title">{node.label}</span>
        </div>
        <ArrowUpRight size={14} className="tile-ghost-go" aria-hidden="true" />
      </div>
      <Handles direction={data.direction} />
    </div>
  )
}

/** Application view: a flow as one row — method, path, its handler, and what it does */
export function FlowRowNode({ id, data, selected }: NodeProps<Node<EntityData>>) {
  const { node } = data
  const dimmed = useDimmed(id)
  const [method, ...rest] = node.label.split(' ')
  const e = node.effects ?? {}
  return (
    <div className={['row', 'row-flow', selected ? 'is-selected' : '', dimmed ? 'is-dimmed' : ''].join(' ')}>
      <span className={`method m-${method.toLowerCase()}`}>{method}</span>
      <span className="row-text">
        <span className="row-title">{rest.join(' ')}</span>
        {node.sub ? <span className="row-sub">{node.sub}</span> : null}
      </span>
      <span className="row-badges">
        {e.writes ? <Badge kind="writes" n={e.writes} title={`Writes ${plural(e.writes, 'table')}`} /> : null}
        {e.reads ? <Badge kind="reads" n={e.reads} title={`Reads ${plural(e.reads, 'table')}`} /> : null}
        {e.calls ? <Badge kind="calls" n={e.calls} title={`Makes ${plural(e.calls, 'call')} to other systems`} /> : null}
      </span>
      <ChevronRight size={15} className="row-go" aria-hidden="true" />
      <Handles direction={data.direction} />
    </div>
  )
}

/** Application view: a table, another application's endpoint, or an outside system */
export function TargetRowNode({ id, data, selected }: NodeProps<Node<EntityData>>) {
  const { node } = data
  const style = kindStyle(node.kind)
  const Icon = style.icon
  const dimmed = useDimmed(id)
  const u = node.usage ?? {}
  const isCall = node.kind !== 'table' && node.kind !== 'datastore'
  const [method, ...rest] = node.kind === 'endpoint' || node.kind === 'interface' ? node.label.split(' ') : ['']
  return (
    <div className={['row', 'row-target', `tone-${style.tone}`, selected ? 'is-selected' : '', dimmed ? 'is-dimmed' : '', node.stub ? 'is-stub' : ''].join(' ')}>
      {method && rest.length ? <span className={`method m-${method.toLowerCase()}`}>{method}</span> : <Icon size={14} className="row-icon" aria-hidden="true" />}
      <span className="row-text">
        <span className="row-title">{method && rest.length ? rest.join(' ') : node.label}</span>
        {node.sub ? <span className="row-sub">{node.sub}</span> : null}
      </span>
      <span className="row-badges">
        {u.writes ? <Badge kind="writes" n={u.writes} title={`Written by ${plural(u.writes, 'flow')}`} /> : null}
        {u.reads ? <Badge kind="reads" n={u.reads} title={`Read by ${plural(u.reads, 'flow')}`} /> : null}
        {isCall && u.calls ? <Badge kind="calls" n={u.calls} title={`Called by ${plural(u.calls, 'flow')}`} /> : null}
      </span>
      <Handles direction={data.direction} />
    </div>
  )
}

const BADGE_ICONS = { writes: PenLine, reads: BookOpen, calls: ArrowUpRight }
const KIND_OF_EFFECT: Record<string, 'writes' | 'reads' | 'calls'> = { WRITES_TO: 'writes', READS_FROM: 'reads', CALLS: 'calls' }

/** Flow view: what the flow is — its trigger, its handler, and where its data goes */
export function FlowHeadNode({ data }: NodeProps<Node<EntityData>>) {
  const { node } = data
  const e = node.effects ?? {}
  const trigger = node.trigger === 'endpoint' ? 'HTTP endpoint' : node.trigger ? kindStyle(node.trigger).label : 'Entry point'
  const facts = [
    e.writes ? { kind: 'writes' as const, text: `writes ${plural(e.writes, 'table')}` } : null,
    e.reads ? { kind: 'reads' as const, text: `reads ${plural(e.reads, 'table')}` } : null,
    e.calls ? { kind: 'calls' as const, text: `calls ${plural(e.calls, 'system')}` } : null,
  ].filter((f) => f !== null)
  return (
    <div className="fhead">
      <span className="tile-kicker">Flow · started by {trigger}</span>
      <div className="fhead-title">
        {node.method ? <span className={`method m-${node.method.toLowerCase()}`}>{node.method}</span> : null}
        <span>{node.path || node.label}</span>
      </div>
      <div className="fhead-sub">
        Handled by <code>{node.sub}</code>
      </div>
      <div className="fhead-effects">
        {facts.length ? (
          facts.map((f) => {
            const Icon = BADGE_ICONS[f.kind]
            return (
              <span key={f.kind} className={`badge badge-${f.kind}`}>
                <Icon size={12} strokeWidth={2.4} aria-hidden="true" />
                {f.text}
              </span>
            )
          })
        ) : (
          <span className="muted small">No reads, writes, or calls found</span>
        )}
      </div>
      <p className="fhead-purpose">{node.purpose ?? 'Purpose: written by the flow card once cards are generated.'}</p>
      <Handles direction="DOWN" />
    </div>
  )
}

/** Flow view: one step, numbered in execution order, with what it does */
export function StepRowNode({ id, data, selected }: NodeProps<Node<EntityData>>) {
  const { node } = data
  const dimmed = useDimmed(id)
  const does = node.does ?? []
  const shown = does.slice(0, 2)
  // The class or module it lives in, without the package path; the file is shown below
  const owner = node.container?.split('.').pop()
  const name = owner ? `${owner}.${node.label}` : node.label
  return (
    <div
      className={[
        'row',
        'step',
        (node.depth ?? 0) > 0 ? 'is-nested' : '',
        does.length ? '' : 'is-quiet',
        selected ? 'is-selected' : '',
        dimmed ? 'is-dimmed' : '',
      ].join(' ')}
    >
      <span className="step-num">{node.order}</span>
      <span className="row-text">
        <span className="row-title step-name" title={name}>
          {name}
        </span>
        <span className="row-sub">
          {node.conditional ? <span className="step-tag">if</span> : null}
          {node.is_async ? <span className="step-tag">async</span> : null}
          {node.sub}
        </span>
      </span>
      <span className="row-badges">
        {shown.map((d, i) => {
          const kind = KIND_OF_EFFECT[d.type] ?? 'calls'
          const Icon = BADGE_ICONS[kind]
          const text = `${d.op ?? kind} ${d.target}`
          return (
            <span key={i} className={`badge badge-${kind} badge-wide`} title={text}>
              <Icon size={11} strokeWidth={2.4} aria-hidden="true" />
              {text}
            </span>
          )
        })}
        {does.length > shown.length ? <span className="badge">+{does.length - shown.length}</span> : null}
      </span>
      <Handles direction="RIGHT" />
    </div>
  )
}

function Badge({ kind, n, title }: { kind: 'writes' | 'reads' | 'calls'; n: number; title: string }) {
  const Icon = BADGE_ICONS[kind]
  return (
    <span className={`badge badge-${kind}`} title={title} aria-label={title}>
      <Icon size={11} strokeWidth={2.4} aria-hidden="true" />
      {n}
    </span>
  )
}

export function ClusterNode({ data }: NodeProps<Node<ClusterData>>) {
  const style = kindStyle(data.kind === 'frame' ? 'space' : data.kind)
  const Icon = style.icon
  if (data.kind === 'frame') {
    return (
      <div className={['gframe', hueClass(data.hue)].join(' ')}>
        <div className="gframe-head">
          <span className="tile-icon"><Icon size={18} /></span>
          <div className="tile-titles">
            <span className="tile-kicker">You are in</span>
            <span className="gframe-title">{data.label}</span>
          </div>
        </div>
      </div>
    )
  }
  return (
    <div className={['gcluster', `tone-${style.tone}`, `kind-${data.kind}`, data.stub ? 'is-stub' : ''].join(' ')}>
      <div className="gcluster-head">
        <Icon size={13} strokeWidth={2} aria-hidden="true" />
        <span className="gcluster-label">{data.label}</span>
        <span className="gcluster-count">{data.count}</span>
      </div>
    </div>
  )
}
