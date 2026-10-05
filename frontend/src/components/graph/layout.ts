import ELK from 'elkjs/lib/elk.bundled.js'
import type { ElkExtendedEdge, ElkNode } from 'elkjs/lib/elk-api'
import type { Edge, Node } from '@xyflow/react'
import { MarkerType } from '@xyflow/react'
import type { GraphEdge, GraphNode, GraphView } from '../../api/client'
import type { Point, RoutedData } from './edges'
import { partText, type LabelPart } from './labels'

const elk = new ELK()

export type Direction = 'RIGHT' | 'DOWN'

export interface EntityData extends Record<string, unknown> {
  node: GraphNode
  direction: Direction
}
export interface ClusterData extends Record<string, unknown> {
  label: string
  kind: string
  stub?: boolean
  count: number
  hue?: number | null
  description?: string | null
}

/** What the viewer chose to see (the View menu) */
export interface ViewOptions {
  externals: boolean
  data: boolean
  calls: boolean
  labels: boolean
  /** AUTO tries both directions and keeps the one that shows largest in the window */
  direction: Direction | 'AUTO'
}
export const DEFAULT_OPTIONS: ViewOptions = { externals: true, data: true, calls: true, labels: true, direction: 'AUTO' }

const DATA_EDGES = new Set(['READS_FROM', 'WRITES_TO'])
const DATA_KINDS = new Set(['datastore', 'table'])

/** Drop what the viewer turned off, and anything left unconnected because of it. */
export function filterView(view: GraphView, o: ViewOptions): GraphView {
  const dropNode = (n: GraphNode) => (!o.externals && n.kind === 'external') || (!o.data && DATA_KINDS.has(n.kind))
  const nodes = view.nodes.filter((n) => !dropNode(n))
  const ids = new Set(nodes.map((n) => n.id))
  const edges = view.edges.filter(
    (e) => ids.has(e.src) && ids.has(e.dst) && (o.data || !DATA_EDGES.has(e.type)) && (o.calls || e.type !== 'CALLS'),
  )
  return { ...view, nodes, edges }
}

const isLevel = (view: GraphView) => view.view === 'organization' || view.view === 'space'

const SIZE: Record<string, [number, number]> = {
  application: [250, 64],
  flow: [270, 60],
  endpoint: [270, 52],
  function: [230, 56],
  file: [210, 56],
  table: [190, 54],
  datastore: [220, 58],
  external: [220, 58],
}
const sizeOf = (kind: string): [number, number] => SIZE[kind] ?? [210, 56]

/** Node type and size at the organization and space levels: tiles for spaces and applications */
function levelShape(n: GraphNode, view: GraphView): { type: string; size: [number, number] } {
  if (n.outside && n.kind !== 'external') return { type: 'ghost', size: [250, 84] }
  if (n.kind === 'space') return { type: 'space', size: view.view === 'organization' ? [330, 212] : [300, 196] }
  if (n.kind === 'application') return { type: 'app', size: [272, 112] }
  return { type: 'entity', size: n.kind === 'datastore' ? [250, 64] : sizeOf(n.kind) }
}

/** Application view: flows and what they touch are compact rows in cards */
function rowShape(n: GraphNode): { type: string; size: [number, number] } {
  return n.kind === 'flow' ? { type: 'flowrow', size: [340, 50] } : { type: 'targetrow', size: [270, 38] }
}

const PADDING = '[top=44,left=18,bottom=18,right=18]'
const GROUP_OPTIONS = { 'elk.padding': PADDING }
// Organization and space levels: room between tiles for curves and their labels. ELK takes
// spacing from each node's parent, so groups (the frame) need it as well as the root.
const LEVEL_SPACING = {
  'elk.layered.spacing.nodeNodeBetweenLayers': '130',
  'elk.spacing.nodeNode': '48',
  'elk.layered.spacing.edgeNodeBetweenLayers': '22',
  'elk.layered.spacing.edgeEdgeBetweenLayers': '14',
  'elk.spacing.edgeLabel': '6',
}

/** Packed layout: lanes pack their groups, resource groups are one column, big stores wrap into columns. */
function packedOptions(kind: string, size: number): Record<string, string> {
  if (kind === 'lane') {
    return {
      'elk.algorithm': 'rectpacking',
      'elk.aspectRatio': '1.1',
      'elk.spacing.nodeNode': '24',
      'elk.padding': '[top=40,left=0,bottom=0,right=0]',
    }
  }
  if (kind !== 'resource' && size > 30) {
    return { 'elk.algorithm': 'box', 'elk.aspectRatio': '1.2', 'elk.spacing.nodeNode': '6', 'elk.padding': CARD_PADDING }
  }
  return {
    'elk.algorithm': 'layered',
    'elk.direction': 'RIGHT',
    'elk.spacing.nodeNode': '6',
    'elk.layered.considerModelOrder.strategy': 'NODES_AND_EDGES',
    // Rows have no lines between them; without this ELK packs them side by side as separate pieces
    'elk.separateConnectedComponents': 'false',
    'elk.padding': CARD_PADDING,
  }
}
const CARD_PADDING = '[top=46,left=10,bottom=10,right=10]'

/** Split an application view into two lanes: its flows, and everything they touch. */
function withLanes(view: GraphView): GraphView {
  const top = (parent?: string | null) => !parent || !view.groups.some((g) => g.id === parent)
  const isFlowSide = (kind: string) => kind === 'resource' || kind === 'flow'
  const lane = (kind: string) => (isFlowSide(kind) ? 'lane:flows' : 'lane:touches')
  return {
    ...view,
    groups: [
      { id: 'lane:flows', kind: 'lane', label: 'Flows', parent: null },
      { id: 'lane:touches', kind: 'lane', label: 'Data and systems', parent: null },
      ...view.groups.map((g) => (top(g.parent) ? { ...g, parent: lane(g.kind) } : g)),
    ],
    nodes: view.nodes.map((n) => (top(n.parent) ? { ...n, parent: lane(n.kind) } : n)),
  }
}

/** A space level draws the space itself as a frame around what's inside it; neighbors sit outside. */
function withFrame(view: GraphView): GraphView {
  const c = view.container
  if (view.view !== 'space' || !c) return view
  return {
    ...view,
    groups: [{ id: c.id, kind: 'frame', label: c.label, parent: null }, ...view.groups],
    nodes: view.nodes.map((n) => (!n.outside && !n.parent && n.kind !== 'external' ? { ...n, parent: c.id } : n)),
  }
}

/**
 * At the organization and space levels, every connection between the same two tiles becomes one
 * edge: "4 calls · reads 2 tables". Its color follows the strongest kind (calls, then writes, then reads).
 */
function mergePairs(edges: GraphEdge[]): (GraphEdge & { parts: LabelPart[] })[] {
  const rank = ['CALLS', 'WRITES_TO', 'READS_FROM', 'PRODUCES', 'CONSUMES']
  const byPair = new Map<string, GraphEdge[]>()
  for (const e of edges) byPair.set(`${e.src}|${e.dst}`, [...(byPair.get(`${e.src}|${e.dst}`) ?? []), e])
  return [...byPair.entries()].map(([pair, list]) => {
    list.sort((a, b) => rank.indexOf(a.type) - rank.indexOf(b.type))
    return {
      id: `pair|${pair}`,
      src: list[0].src,
      dst: list[0].dst,
      type: list[0].type,
      n: list.reduce((sum, e) => sum + e.n, 0),
      // The label itself is drawn from the parts (edges.tsx); this text only reserves room
      label: list.map((e) => partText(e)).sort((a, b) => b.length - a.length)[0],
      parts: list.map((e) => ({ type: e.type, n: e.n })),
    }
  })
}

/**
 * Lay a view out with ELK (groups as nested boxes) and convert to React Flow.
 *
 * Most views are layered: edges decide the columns. The application view is packed instead:
 * its flows all sit in one layer, so layering would stack them in one tall column.
 */
export async function layoutView(
  view: GraphView,
  direction: Direction | 'AUTO',
  window: { width: number; height: number },
): Promise<{ nodes: Node[]; edges: Edge[] }> {
  if (view.view === 'flow') return layoutFlow(view)
  if (direction !== 'AUTO') return (await layoutOnce(view, direction)).result
  const [right, down] = await Promise.all([layoutOnce(view, 'RIGHT'), layoutOnce(view, 'DOWN')])
  const scale = (size: { width: number; height: number }) => Math.min(window.width / size.width, window.height / size.height)
  return scale(down.size) > scale(right.size) * 1.05 ? down.result : right.result
}

async function layoutOnce(
  view: GraphView,
  direction: Direction,
): Promise<{ result: { nodes: Node[]; edges: Edge[] }; size: { width: number; height: number } }> {
  const packed = view.view === 'application'
  const level = isLevel(view)
  if (packed) view = withLanes(view)
  if (level) view = { ...withFrame(view), edges: mergePairs(view.edges) }
  const ids = new Set(view.nodes.map((n) => n.id))
  const groupIds = new Set(view.groups.map((g) => g.id))
  const groupKind = new Map(view.groups.map((g) => [g.id, g.kind]))
  const shapes = new Map(
    view.nodes.map((n) => [n.id, level ? levelShape(n, view) : packed ? rowShape(n) : { type: 'entity', size: sizeOf(n.kind) }]),
  )
  const children = new Map<string | null, ElkNode[]>()
  const push = (parent: string | null, child: ElkNode) => {
    const key = parent && groupIds.has(parent) ? parent : null
    children.set(key, [...(children.get(key) ?? []), child])
  }

  for (const n of view.nodes) {
    const [width, height] = shapes.get(n.id)!.size
    push(n.parent ?? null, { id: n.id, width, height })
  }
  // Level labels sit midway between columns, so each gap is as wide as the longest label
  const longest = Math.max(0, ...view.edges.map((e) => (e.label ?? '').length))
  const levelSpacing = { ...LEVEL_SPACING, 'elk.layered.spacing.nodeNodeBetweenLayers': String(Math.max(130, longest * 7 + 70)) }
  const groupNode = (id: string): ElkNode => {
    const kids = (children.get(id) ?? []).map((c) => (groupIds.has(c.id) ? groupNode(c.id) : c))
    const kind = groupKind.get(id)!
    const options = packed
      ? packedOptions(kind, kids.length)
      : level
        ? { ...levelSpacing, 'elk.padding': kind === 'frame' ? '[top=100,left=44,bottom=44,right=44]' : PADDING }
        : GROUP_OPTIONS
    return { id, children: kids, layoutOptions: options }
  }
  for (const g of view.groups) push(g.parent, { id: g.id })
  const prune = (node: ElkNode): ElkNode | null => {
    if (!groupIds.has(node.id)) return node
    const built = groupNode(node.id)
    built.children = (built.children ?? []).map(prune).filter((c): c is ElkNode => c !== null)
    return built.children.length || groupKind.get(node.id) === 'frame' ? built : null
  }
  const roots = (children.get(null) ?? []).map(prune).filter((c): c is ElkNode => c !== null)

  const edges: ElkExtendedEdge[] = packed
    ? roots.length === 2
      ? [{ id: 'lanes', sources: [roots[0].id], targets: [roots[1].id] }]
      : []
    : view.edges
        .filter((e) => ids.has(e.src) && ids.has(e.dst) && e.src !== e.dst)
        .map((e) => {
          // Flow views give ELK each label's size so lines route around them. Levels place
          // labels themselves: ELK puts labels in columns of their own, which spreads tiles apart.
          const text = e.label ?? e.op
          const labels = text && !level ? [{ id: `${e.id}#label`, text, width: text.length * 6.6 + 18, height: 22 }] : undefined
          return { id: e.id, sources: [e.src], targets: [e.dst], labels }
        })

  const graph: ElkNode = {
    id: 'root',
    layoutOptions: packed
      ? {
          'elk.algorithm': 'layered',
          'elk.direction': 'RIGHT',
          'elk.layered.spacing.nodeNodeBetweenLayers': '200',
        }
      : {
          'elk.algorithm': 'layered',
          'elk.direction': direction,
          'elk.hierarchyHandling': 'INCLUDE_CHILDREN',
          ...(level
            ? levelSpacing
            : {
                'elk.layered.spacing.nodeNodeBetweenLayers': direction === 'RIGHT' ? '130' : '70',
                'elk.spacing.nodeNode': '22',
                'elk.layered.spacing.edgeNodeBetweenLayers': '24',
              }),
          'elk.layered.nodePlacement.strategy': 'BRANDES_KOEPF',
          'elk.layered.crossingMinimization.strategy': 'LAYER_SWEEP',
          'elk.spacing.componentComponent': level ? '80' : '48',
          // Routes come back in absolute coordinates, and are drawn as computed (edges.tsx)
          'elk.json.edgeCoords': 'ROOT',
          'elk.edgeRouting': level ? 'SPLINES' : 'ORTHOGONAL',
          'elk.layered.edgeRouting.splines.mode': 'CONSERVATIVE',
          'elk.edgeLabels.placement': 'CENTER',
          'elk.aspectRatio': '1.7',
        },
    children: roots,
    edges,
  }
  const laid = await elk.layout(graph)
  const routes = packed ? new Map<string, Route>() : collectRoutes(laid)

  const byId = new Map(view.nodes.map((n) => [n.id, n]))
  const groupById = new Map(view.groups.map((g) => [g.id, g]))
  const rfNodes: Node[] = []
  const absolute = new Map<string, Point>()
  const walk = (node: ElkNode, parent?: string, offset: Point = { x: 0, y: 0 }) => {
    for (const child of node.children ?? []) {
      const position = { x: child.x ?? 0, y: child.y ?? 0 }
      const abs = { x: offset.x + position.x, y: offset.y + position.y }
      absolute.set(child.id, abs)
      const common = {
        id: child.id,
        position,
        parentId: parent,
        extent: parent ? ('parent' as const) : undefined,
        style: { width: child.width, height: child.height },
        width: child.width,
        height: child.height,
      }
      const group = groupById.get(child.id)
      if (group) {
        const isFrame = group.kind === 'frame'
        rfNodes.push({
          ...common,
          type: 'cluster',
          data: {
            label: group.label,
            kind: group.kind,
            stub: group.stub,
            count: countLeaves(child),
            hue: isFrame ? view.container?.hue : undefined,
            description: isFrame ? view.container?.description : undefined,
          } satisfies ClusterData,
          selectable: false,
          zIndex: -1,
        })
        walk(child, child.id, abs)
      } else {
        rfNodes.push({
          ...common,
          type: shapes.get(child.id)!.type,
          data: { node: byId.get(child.id)!, direction } satisfies EntityData,
        })
      }
    }
  }
  walk(laid)

  const widest = Math.max(1, ...view.edges.map((e) => e.n))
  const rfEdges: Edge[] = view.edges
    .filter((e) => ids.has(e.src) && ids.has(e.dst) && e.src !== e.dst)
    .map((e) => toEdge(e, level, widest, routes.get(e.id), absolute))
  return { result: { nodes: rfNodes, edges: rfEdges }, size: { width: laid.width ?? 1, height: laid.height ?? 1 } }
}

interface Route {
  points: Point[]
  labelAt?: Point
}

/** Every edge's route, wherever ELK put it in the hierarchy */
function collectRoutes(root: ElkNode): Map<string, Route> {
  const out = new Map<string, Route>()
  const visit = (node: ElkNode) => {
    for (const e of node.edges ?? []) {
      const sections = (e as ElkExtendedEdge).sections ?? []
      if (!sections.length) continue
      const points: Point[] = []
      for (const sec of sections) {
        if (!points.length) points.push(sec.startPoint)
        points.push(...(sec.bendPoints ?? []), sec.endPoint)
      }
      const l = e.labels?.[0]
      const labelAt = l?.x !== undefined && l.y !== undefined ? { x: l.x + (l.width ?? 0) / 2, y: l.y + (l.height ?? 0) / 2 } : undefined
      out.set(e.id, { points, labelAt })
    }
    for (const c of node.children ?? []) visit(c)
  }
  visit(root)
  return out
}

function toEdge(e: GraphEdge, level: boolean, widest: number, route: Route | undefined, absolute: Map<string, Point>): Edge {
  const type = e.type.toLowerCase()
  const color = `var(--e-${type}, var(--muted))`
  const routed = route && absolute.has(e.src) && absolute.has(e.dst)
  return {
    id: e.id,
    source: e.src,
    target: e.dst,
    // Drawn along ELK's route; levels are splines whose width grows with how much flows between two tiles
    type: routed ? 'routed' : level ? 'default' : 'smoothstep',
    className: ['edge', `edge-${type}`, level ? 'edge-level' : '', e.async ? 'edge-async' : '', e.conditional ? 'edge-cond' : '']
      .filter(Boolean)
      .join(' '),
    label: e.label ?? (e.op ? (e.type === 'CALLS' ? `calls ${e.op}` : e.op) : undefined),
    labelClassName: 'edge-label',
    pathOptions: level ? undefined : { borderRadius: 10 },
    style: level ? { strokeWidth: 1.6 + 3.4 * (Math.log1p(e.n) / Math.log1p(widest)) } : undefined,
    markerEnd: level
      ? { type: MarkerType.ArrowClosed, width: 18, height: 18, color, markerUnits: 'userSpaceOnUse' }
      : { type: MarkerType.ArrowClosed, width: 14, height: 14, color },
    data: {
      type: e.type,
      n: e.n,
      parts: (e as { parts?: LabelPart[] }).parts,
      points: route?.points,
      spline: level,
      labelAt: route?.labelAt,
      at: routed ? { src: absolute.get(e.src)!, dst: absolute.get(e.dst)! } : undefined,
    } satisfies RoutedData,
  } as Edge
}

function countLeaves(node: ElkNode): number {
  return (node.children ?? []).reduce((sum, c) => sum + (c.children?.length ? countLeaves(c) : 1), 0)
}

// ------------------------------------------------------------------ flow view

const HEAD = { w: 600, h: 150 }
const STEP = { w: 540, h: 54, gap: 10, indent: 30 }
const CARD = { w: 290, head: 42, row: 38, gap: 6, pad: 10 }

/**
 * A flow reads top to bottom: its header, then its steps in the order they run (indented by
 * call depth), with the data it touches to the right. Each card sits beside the first step
 * that uses it, so lines run mostly across, not up and down.
 */
function layoutFlow(view: GraphView): { nodes: Node[]; edges: Edge[] } {
  const head = view.nodes.find((n) => n.kind === 'flow')
  const steps = view.nodes.filter((n) => n.kind === 'function').sort((a, b) => (a.order ?? 0) - (b.order ?? 0))
  const targets = view.nodes.filter((n) => n.kind !== 'flow' && n.kind !== 'function')
  const stepTop = HEAD.h + 48
  const stepY = (order: number) => stepTop + (order - 1) * (STEP.h + STEP.gap)
  const nodes: Node[] = []
  const at = (id: string, type: string, x: number, y: number, w: number, h: number, node: GraphNode, parentId?: string): Node => ({
    id,
    type,
    position: { x, y },
    parentId,
    extent: parentId ? 'parent' : undefined,
    style: { width: w, height: h },
    width: w,
    height: h,
    data: { node, direction: 'RIGHT' } satisfies EntityData,
  })

  if (head) nodes.push(at(head.id, 'flowhead', 0, 0, HEAD.w, HEAD.h, head))
  let right = HEAD.w
  for (const st of steps) {
    const x = (st.depth ?? 0) * STEP.indent
    nodes.push(at(st.id, 'steprow', x, stepY(st.order ?? 1), STEP.w, STEP.h, st))
    right = Math.max(right, x + STEP.w)
  }

  // Cards in order of first use, each as close as it can get to the step that first uses it
  const cardX = right + 170
  const byGroup = new Map<string, GraphNode[]>()
  for (const t of targets) byGroup.set(t.parent ?? 'group:other', [...(byGroup.get(t.parent ?? 'group:other') ?? []), t])
  const firstOf = (list: GraphNode[]) => Math.min(...list.map((t) => t.first_use ?? 1))
  const cards = [...byGroup.entries()].sort((a, b) => firstOf(a[1]) - firstOf(b[1]))
  let floor = stepTop - CARD.head
  for (const [gid, list] of cards) {
    list.sort((a, b) => (a.first_use ?? 0) - (b.first_use ?? 0))
    const group = view.groups.find((g) => g.id === gid)
    const height = CARD.head + list.length * (CARD.row + CARD.gap) - CARD.gap + CARD.pad
    const y = Math.max(floor, stepY(firstOf(list)) - CARD.head + (STEP.h - CARD.row) / 2)
    nodes.push({
      id: gid,
      type: 'cluster',
      position: { x: cardX, y },
      style: { width: CARD.w, height },
      width: CARD.w,
      height,
      selectable: false,
      zIndex: -1,
      data: { label: group?.label ?? 'Other', kind: group?.kind ?? 'externals', stub: group?.stub, count: list.length } satisfies ClusterData,
    })
    list.forEach((t, i) => nodes.push(at(t.id, 'targetrow', CARD.pad, CARD.head + i * (CARD.row + CARD.gap), CARD.w - 2 * CARD.pad, CARD.row, t, gid)))
    floor = y + height + 22
  }

  const edges: Edge[] = view.edges.map((e) => {
    const type = e.type.toLowerCase()
    const color = `var(--e-${type}, var(--muted))`
    const marker = { type: MarkerType.ArrowClosed, width: 14, height: 14, color }
    // Arrows follow the data: a write points into the table, a read points back into the step
    const reads = e.type === 'READS_FROM'
    return {
      id: e.id,
      source: e.src,
      target: e.dst,
      type: 'routed',
      className: ['edge', `edge-${type}`].join(' '),
      label: e.op ? (e.type === 'CALLS' ? `calls ${e.op}` : e.op) : undefined,
      markerEnd: reads ? undefined : marker,
      markerStart: reads ? marker : undefined,
      data: { type: e.type, n: e.n } satisfies RoutedData,
    } as Edge
  })
  return { nodes, edges }
}
