import { Handle, Position, type Node, type NodeProps } from '@xyflow/react'
import { ArrowUpRight, Info, Maximize2 } from 'lucide-react'
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
