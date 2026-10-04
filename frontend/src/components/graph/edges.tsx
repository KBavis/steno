import { BaseEdge, EdgeLabelRenderer, getBezierPath, useInternalNode, type Edge, type EdgeProps } from '@xyflow/react'
import { ArrowUpRight } from 'lucide-react'
import { PARTS, partText, sentence, type LabelPart } from './labels'
import type { EntityData } from './layout'

export interface Point {
  x: number
  y: number
}

export interface RoutedData extends Record<string, unknown> {
  type: string
  n: number
  /** The route ELK computed, in absolute coordinates */
  points?: Point[]
  spline?: boolean
  /** Where ELK placed the label (its center) */
  labelAt?: Point
  /** Where both ends sat when routed; if either moved (dragged), fall back to a plain curve */
  at?: { src: Point; dst: Point }
  state?: 'live' | 'dimmed'
  /** Organization and space levels: the kinds of link this line stands for */
  parts?: LabelPart[]
}

const moved = (a: Point | undefined, b: Point | undefined) => !a || !b || Math.abs(a.x - b.x) > 1 || Math.abs(a.y - b.y) > 1

/** An edge drawn along ELK's route, so lines go around tiles instead of through them */
export function RoutedEdge(props: EdgeProps<Edge<RoutedData>>) {
  const { id, source, target, data, markerEnd, style, label } = props
  const s = useInternalNode(source)
  const t = useInternalNode(target)
  const routed =
    data?.points && data.points.length > 1 && !moved(s?.internals.positionAbsolute, data.at?.src) && !moved(t?.internals.positionAbsolute, data.at?.dst)

  let path: string
  let lx: number
  let ly: number
  if (routed) {
    const points = data!.points!
    path = data!.spline ? splinePath(points) : roundedPath(points, 10)
    const mid = data!.labelAt ?? halfway(points)
    lx = mid.x
    ly = mid.y
  } else {
    ;[path, lx, ly] = getBezierPath(props)
  }
  const type = (data?.type ?? '').toLowerCase()
  const state = data?.state ? `is-${data.state}` : ''
  const parts = data?.parts
  const srcNode = (s?.data as EntityData | undefined)?.node
  const dstNode = (t?.data as EntityData | undefined)?.node
  return (
    <>
      <BaseEdge id={id} path={path} markerEnd={markerEnd} style={style} interactionWidth={18} />
      {label && parts?.length ? (
        <EdgeLabelRenderer>
          <div className={['elabel', 'elabel-parts', 'nopan', state].join(' ')} style={{ transform: `translate(-50%, -50%) translate(${lx}px, ${ly}px)` }}>
            {parts.map((p) => {
              const Icon = PARTS[p.type]?.icon ?? ArrowUpRight
              return (
                <span key={p.type} className={`epart e-${p.type.toLowerCase()}`}>
                  <Icon size={11} strokeWidth={2.4} aria-hidden="true" />
                  {partText(p)}
                </span>
              )
            })}
            <span className="elabel-sentence" role="tooltip">
              {parts.map((p) => sentence(p, srcNode?.label ?? 'This', dstNode?.label ?? 'that', dstNode?.kind ?? '')).join(' ')}
            </span>
          </div>
        </EdgeLabelRenderer>
      ) : label ? (
        <EdgeLabelRenderer>
          <div
            className={['elabel', `e-${type}`, state].join(' ')}
            style={{ transform: `translate(-50%, -50%) translate(${lx}px, ${ly}px)` }}
          >
            {label}
          </div>
        </EdgeLabelRenderer>
      ) : null}
    </>
  )
}

/** ELK splines: a start point, then cubic segments of (control, control, end) */
function splinePath(p: Point[]): string {
  if ((p.length - 1) % 3 !== 0) return roundedPath(p, 16)
  let d = `M ${p[0].x} ${p[0].y}`
  for (let i = 1; i < p.length; i += 3) d += ` C ${p[i].x} ${p[i].y}, ${p[i + 1].x} ${p[i + 1].y}, ${p[i + 2].x} ${p[i + 2].y}`
  return d
}

/** Orthogonal routes with rounded corners */
function roundedPath(p: Point[], radius: number): string {
  let d = `M ${p[0].x} ${p[0].y}`
  for (let i = 1; i < p.length - 1; i++) {
    const [a, b, c] = [p[i - 1], p[i], p[i + 1]]
    const r = Math.min(radius, dist(a, b) / 2, dist(b, c) / 2)
    const p1 = toward(b, a, r)
    const p2 = toward(b, c, r)
    d += ` L ${p1.x} ${p1.y} Q ${b.x} ${b.y} ${p2.x} ${p2.y}`
  }
  const last = p[p.length - 1]
  return `${d} L ${last.x} ${last.y}`
}

/** The point halfway along a route, measured along its control polygon */
function halfway(p: Point[]): Point {
  const lengths = p.slice(1).map((q, i) => dist(p[i], q))
  let left = lengths.reduce((a, b) => a + b, 0) / 2
  for (let i = 0; i < lengths.length; i++) {
    if (left <= lengths[i]) return toward(p[i], p[i + 1], left)
    left -= lengths[i]
  }
  return p[p.length - 1]
}

const dist = (a: Point, b: Point) => Math.hypot(a.x - b.x, a.y - b.y)
function toward(from: Point, to: Point, r: number): Point {
  const len = dist(from, to) || 1
  return { x: from.x + ((to.x - from.x) / len) * r, y: from.y + ((to.y - from.y) / len) * r }
}

