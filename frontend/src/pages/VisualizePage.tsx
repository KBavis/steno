import {
  applyNodeChanges,
  Background,
  BackgroundVariant,
  Controls,
  getViewportForBounds,
  ReactFlow,
  ReactFlowProvider,
  useNodesInitialized,
  useReactFlow,
  type Edge,
  type Node,
  type FitViewOptions,
  type NodeChange,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { ArrowLeft, Check, ChevronRight, Network, RotateCcw, Search, SlidersHorizontal } from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, type Crumb, type GraphView, type SearchHit } from '../api/client'
import { DetailsPanel } from '../components/graph/DetailsPanel'
import { collapseFlow, expandAll, OPEN_DEPTH, type Expansion } from '../components/graph/collapse'
import { ExpandContext, FocusContext, InspectContext } from '../components/graph/focus'
import '../components/graph/graph.css'
import { DEFAULT_OPTIONS, filterView, layoutView, type EntityData, type ViewOptions } from '../components/graph/layout'
import { edgeTypes, nodeTypes } from '../components/graph/nodeTypes'
import { play, snapshot, type Move, type Rect } from '../components/graph/transition'
import { EmptyState, ErrorAlert, Logo } from '../components/ui'
import { EDGE_TYPES, kindStyle } from '../lib/graphStyle'

// ------------------------------------------------------------------ routes

type Route =
  | { view: 'organization' }
  | { view: 'space'; id: string }
  | { view: 'application'; id: string; layer: 'architecture' | 'code' }
  | { view: 'flow'; id: string; all: boolean }

// #visualize · #visualize/space/<id> · #visualize/application/<id>/code · #visualize/flow/<id>/all
function parseRoute(): Route {
  const [, view, raw, opt] = window.location.hash.slice(1).split('/')
  const id = raw ? decodeURIComponent(raw) : ''
  if (view === 'space' && id) return { view, id }
  if (view === 'application' && id) return { view, id, layer: opt === 'code' ? 'code' : 'architecture' }
  if (view === 'flow' && id) return { view, id, all: opt === 'all' }
  return { view: 'organization' }
}
function formatRoute(r: Route): string {
  if (r.view === 'space') return `visualize/space/${encodeURIComponent(r.id)}`
  if (r.view === 'application') return `visualize/application/${encodeURIComponent(r.id)}/${r.layer}`
  if (r.view === 'flow') return `visualize/flow/${encodeURIComponent(r.id)}${r.all ? '/all' : ''}`
  return 'visualize'
}
const go = (r: Route) => {
  window.location.hash = formatRoute(r)
}

/** Where a node of this kind is entered, if it can be */
function routeFor(kind: string, id: string): Route | null {
  if (kind === 'organization') return { view: 'organization' }
  if (kind === 'space') return { view: 'space', id }
  if (kind === 'application') return { view: 'application', id, layer: 'architecture' }
  if (kind === 'flow') return { view: 'flow', id, all: false }
  return null
}

const LEVELS = [
  { key: 'organization', label: 'Organization' },
  { key: 'space', label: 'Space' },
  { key: 'application', label: 'Application' },
  { key: 'flow', label: 'Flow' },
] as const
const levelIndex = (r: Route) => ({ organization: 0, space: 1, application: 2, flow: 3 })[r.view]

// ------------------------------------------------------- per-viewer settings

const OPTIONS_KEY = 'steno.visualize.options'
const POSITIONS_KEY = 'steno.visualize.positions:'

function loadOptions(): ViewOptions {
  try {
    return { ...DEFAULT_OPTIONS, ...JSON.parse(localStorage.getItem(OPTIONS_KEY) ?? '{}') }
  } catch {
    return DEFAULT_OPTIONS
  }
}
function saveOptions(o: ViewOptions) {
  try {
    localStorage.setItem(OPTIONS_KEY, JSON.stringify(o))
  } catch {
    // settings are a convenience
  }
}
type Positions = Record<string, { x: number; y: number }>
function loadPositions(key: string): Positions {
  try {
    return JSON.parse(localStorage.getItem(POSITIONS_KEY + key) ?? '{}')
  } catch {
    return {}
  }
}
function savePositions(key: string, p: Positions | null) {
  try {
    if (p) localStorage.setItem(POSITIONS_KEY + key, JSON.stringify(p))
    else localStorage.removeItem(POSITIONS_KEY + key)
  } catch {
    // arrangement is a convenience
  }
}

// Each top-level space keeps one color at every level; remember the ones seen so far
const hues = new Map<string, number>()
function rememberHues(view: GraphView) {
  for (const n of [...view.nodes, ...(view.container ? [view.container] : [])]) {
    if (n.hue !== null && n.hue !== undefined) hues.set(n.id, n.hue)
  }
}
const hueOf = (crumbs: Crumb[]) => crumbs.map((c) => hues.get(c.id)).find((h) => h !== undefined)

const FIT_PADDING: NonNullable<FitViewOptions['padding']> = { top: '96px', left: '84px', right: '40px', bottom: '96px' }

// -------------------------------------------------------------------- page

export function VisualizePage({ onExit }: { onExit: () => void }) {
  return (
    <ReactFlowProvider>
      <Visualizer onExit={onExit} />
    </ReactFlowProvider>
  )
}

type Laid = { view: GraphView; nodes: Node[]; edges: Edge[] }

function fetchView(r: Route): Promise<GraphView> {
  if (r.view === 'space') return api.graph.space(r.id)
  if (r.view === 'application') return api.graph.application(r.id, r.layer)
  if (r.view === 'flow') return api.graph.flow(r.id, !r.all)
  return api.graph.overview()
}

const rectOf = (n: { internals: { positionAbsolute: { x: number; y: number } }; measured: { width?: number; height?: number } }): Rect => ({
  ...n.internals.positionAbsolute,
  width: n.measured.width ?? 200,
  height: n.measured.height ?? 100,
})

// A flow's folding before the viewer changes it: OPEN_DEPTH levels open
const DEFAULT_EXPANSION: Expansion = {}

function Visualizer({ onExit }: { onExit: () => void }) {
  const [route, setRoute] = useState<Route>(parseRoute)
  const [view, setView] = useState<GraphView>()
  const [nodes, setNodes] = useState<Node[]>([])
  const [baseEdges, setBaseEdges] = useState<Edge[]>([])
  const [error, setError] = useState<string>()
  const [loading, setLoading] = useState(true)
  const [selected, setSelected] = useState<string>()
  const [hovered, setHovered] = useState<string>()
  const [options, setOptions] = useState<ViewOptions>(loadOptions)
  const [arrangement, setArrangement] = useState(0)
  // Which flow steps are open, for the route it was set on (another route starts folded)
  const [expansion, setExpansion] = useState<{ route: string; open: Expansion }>({ route: '', open: DEFAULT_EXPANSION })
  const flow = useReactFlow()
  const initialized = useNodesInitialized()
  const stageRef = useRef<HTMLDivElement>(null)
  const ghostHost = useRef<HTMLDivElement>(null)
  // The move to play once the new view has rendered, and the one playing now
  const pending = useRef<{ move: Move; ghost: HTMLElement | null; anchorOut?: string; from?: Rect } | null>(null)
  const cancel = useRef<() => void>(() => {})
  // Laid-out views by route and settings; hovering a tile fills this so a click starts at once
  const cache = useRef(new Map<string, { at: number; laid: Promise<Laid> }>())
  // Fetched views by route, so folding a flow lays it out again without fetching it again
  const fetched = useRef(new Map<string, { at: number; view: Promise<GraphView> }>())

  useEffect(() => {
    const onHash = () => setRoute(parseRoute())
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])

  const routeKey = formatRoute(route)
  const open = expansion.route === routeKey ? expansion.open : DEFAULT_EXPANSION

  const fetchOnce = useCallback((r: Route): Promise<GraphView> => {
    const key = formatRoute(r)
    const hit = fetched.current.get(key)
    if (hit && Date.now() - hit.at < 30_000) return hit.view
    const view = fetchView(r)
    view.catch(() => fetched.current.delete(key))
    fetched.current.set(key, { at: Date.now(), view })
    return view
  }, [])

  const prepare = useCallback(
    (r: Route, folding: Expansion = DEFAULT_EXPANSION): Promise<Laid> => {
      const key = `${formatRoute(r)}|${JSON.stringify(options)}|${r.view === 'flow' ? JSON.stringify(folding) : ''}`
      const hit = cache.current.get(key)
      if (hit && Date.now() - hit.at < 30_000) return hit.laid
      const laid = fetchOnce(r).then(async (v) => {
        rememberHues(v)
        const stage = stageRef.current
        const room = { width: (stage?.clientWidth ?? 1400) - 124, height: (stage?.clientHeight ?? 900) - 160 }
        // Only the organization and space levels choose a direction; other views have their own
        const direction = v.view === 'flow' ? 'DOWN' : v.view === 'organization' || v.view === 'space' ? options.direction : 'RIGHT'
        const out = await layoutView(filterView(collapseFlow(v, folding), options), direction, room)
        return { view: v, ...out }
      })
      laid.catch(() => cache.current.delete(key))
      cache.current.set(key, { at: Date.now(), laid })
      return laid
    },
    [options, fetchOnce],
  )

  const toggleStep = useCallback(
    (id: string, isOpen: boolean) =>
      setExpansion((e) => ({ route: routeKey, open: { ...(e.route === routeKey ? e.open : {}), [id]: isOpen } })),
    [routeKey],
  )

  const shownKey = useRef<string>(undefined)
  useEffect(() => {
    let live = true
    setError(undefined)
    setLoading(true)
    prepare(route, open)
      .then(({ view: next, nodes: laidNodes, edges: laidEdges }) => {
        if (!live) return
        const stage = stageRef.current
        const moving = shownKey.current !== routeKey
        shownKey.current = routeKey
        if (moving && stage && ghostHost.current) {
          // Work out how the two views relate before the old one goes away
          cancel.current()
          const vp = flow.getViewport()
          const toScreen = (r: Rect): Rect => ({ x: r.x * vp.zoom + vp.x, y: r.y * vp.zoom + vp.y, width: r.width * vp.zoom, height: r.height * vp.zoom })
          const old = flow.getNodes()
          const into = next.focus ? flow.getInternalNode(next.focus) : undefined
          let move: Move = { kind: 'fade' }
          let anchorOut: string | undefined
          if (into && into.type !== 'cluster') move = { kind: 'in', tile: toScreen(rectOf(into)) }
          else if (view?.focus && laidNodes.some((n) => n.id === view.focus)) anchorOut = view.focus
          const ghost = old.length ? snapshot(stage, ghostHost.current) : null
          pending.current = { move, ghost, anchorOut, from: old.length ? toScreen(flow.getNodesBounds(old)) : undefined }
          stage.style.opacity = '0'
          setSelected(undefined)
          setHovered(undefined)
        }
        // A flow's rows move as it folds, so positions dragged earlier aren't reused there
        const saved = next.view === 'flow' ? {} : loadPositions(routeKey)
        setView(next)
        setNodes(laidNodes.map((n) => (saved[n.id] ? { ...n, position: saved[n.id] } : n)))
        setBaseEdges(laidEdges)
        setLoading(false)
      })
      .catch((e: Error) => {
        if (!live) return
        setError(e.message)
        setLoading(false)
        if (stageRef.current) stageRef.current.style.opacity = ''
      })
    return () => {
      live = false
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [routeKey, options, arrangement, open])

  // Once the new view has rendered, play the move into it
  useEffect(() => {
    const p = pending.current
    const stage = stageRef.current
    if (!initialized || !p || !stage) return
    pending.current = null
    const all = flow.getNodes()
    const world = flow.getNodesBounds(all)
    // Computed rather than read back: fitView applies asynchronously
    const fit = getViewportForBounds(world, stage.clientWidth, stage.clientHeight, 0.06, 1.1, FIT_PADDING)
    let move = p.move
    if (move.kind === 'fade' && p.anchorOut && p.from) {
      const tile = flow.getInternalNode(p.anchorOut)
      if (tile) move = { kind: 'out', from: p.from, tile: rectOf(tile) }
    }
    cancel.current = play(move, {
      world,
      fit,
      stage,
      ghost: p.ghost,
      setViewport: (v) => void flow.setViewport(v),
      maxZoom: 4,
      onDone: () => {},
    })
  }, [initialized, nodes, flow])

  const crumbs = useMemo(() => view?.breadcrumbs ?? [], [view])
  const parentRoute = useMemo((): Route | null => {
    for (let i = crumbs.length - 2; i >= 0; i--) {
      const r = routeFor(crumbs[i].kind, crumbs[i].id)
      if (r) return r
    }
    return route.view === 'organization' ? null : { view: 'organization' }
  }, [crumbs, route.view])

  // Escape closes the details, then goes up a level
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape' || (e.target as HTMLElement)?.closest?.('input, [role="dialog"]')) return
      if (selected) setSelected(undefined)
      else if (parentRoute) go(parentRoute)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [selected, parentRoute])

  // Hover or selection lights up a node's connections and dims the rest. Nodes read the focus
  // from context so they keep their identity; rebuilding them mid-hover swallows clicks.
  // Live edges stay at their own depth so their labels remain on top of every line.
  const { near, edges } = useMemo(() => {
    const focus = hovered ?? selected
    const plain = baseEdges.map((e) => (options.labels ? e : { ...e, label: undefined }))
    if (!focus) return { near: null, edges: plain }
    const near = new Set([focus])
    const live = new Set<string>()
    for (const e of baseEdges) {
      if (e.source === focus || e.target === focus) {
        near.add(e.source)
        near.add(e.target)
        live.add(e.id)
      }
    }
    if (near.size === 1) return { near: null, edges: plain }
    return {
      near,
      edges: baseEdges.map((e) => ({
        ...e,
        label: live.has(e.id) ? e.label : options.labels ? e.label : undefined,
        className: `${e.className ?? ''} ${live.has(e.id) ? 'is-live' : 'is-dimmed'}`,
        data: { ...e.data, state: live.has(e.id) ? 'live' : 'dimmed' },
      })),
    }
  }, [baseEdges, hovered, selected, options.labels])

  const onNodesChange = useCallback(
    (changes: NodeChange[]) => setNodes((current) => applyNodeChanges(changes, current)),
    [],
  )
  const onDragStop = useCallback(() => {
    const p: Positions = {}
    for (const n of flow.getNodes()) p[n.id] = n.position
    savePositions(routeKey, p)
  }, [flow, routeKey])

  const onNodeClick = useCallback(
    (_: unknown, n: Node) => {
      if (n.type === 'cluster') return
      const node = (n.data as EntityData).node
      const target = node.drill || node.outside ? routeFor(node.kind, node.id) : null
      if (target && formatRoute(target) !== routeKey) go(target)
      else setSelected(n.id)
    },
    [routeKey],
  )

  // Start loading a level while the pointer is over its tile
  const onNodeMouseEnter = useCallback(
    (_: unknown, n: Node) => {
      if (n.type === 'cluster') return
      setHovered(n.id)
      const node = (n.data as EntityData).node
      const target = node.drill || node.outside ? routeFor(node.kind, node.id) : null
      if (target) prepare(target).catch(() => {})
    },
    [prepare],
  )

  const updateOptions = (o: ViewOptions) => {
    setOptions(o)
    saveOptions(o)
  }

  // Every kind of link on screen, including the ones that share a line with another
  const legend = useMemo(() => {
    const types = baseEdges.flatMap((e) => {
      const d = e.data as { type: string; parts?: { type: string }[] }
      return d.parts?.map((p) => p.type) ?? [d.type]
    })
    return [...new Set(types)].filter((t) => EDGE_TYPES[t])
  }, [baseEdges])
  const empty = !loading && view && (view.empty || view.nodes.length === 0) && route.view === 'organization'
  const hue = view ? (view.container?.hue ?? hueOf([...crumbs].reverse())) : undefined
  const dense = baseEdges.length > 40 && view?.view !== 'application'

  return (
    <div className={`viz-full hue-${hue ?? 'none'}`}>
      <TopBar
        route={route}
        view={view}
        crumbs={crumbs}
        onExit={onExit}
        onCrumb={(c, i) => {
          const r = routeFor(c.kind, c.id)
          if (!r || i === crumbs.length - 1) return
          go(r)
        }}
        onPick={(hit) => pick(hit, setSelected)}
        options={options}
        onOptions={updateOptions}
        onResetArrangement={() => {
          savePositions(routeKey, null)
          setArrangement((n) => n + 1)
        }}
        onFold={(all) => setExpansion({ route: routeKey, open: all ? expandAll(view, true) : DEFAULT_EXPANSION })}
      />

      <DepthRail
        current={levelIndex(route)}
        crumbs={crumbs}
        onGo={(r) => {
          if (formatRoute(r) !== routeKey) go(r)
        }}
      />

      <div ref={stageRef} className={`viz-stage ${dense ? 'is-dense' : ''} view-${view?.view ?? 'loading'}`}>
        <InspectContext.Provider value={setSelected}>
          <ExpandContext.Provider value={toggleStep}>
          <FocusContext.Provider value={near}>
            <ReactFlow
              nodes={nodes}
              edges={edges}
              nodeTypes={nodeTypes}
              edgeTypes={edgeTypes}
              onNodesChange={onNodesChange}
              onNodeDragStop={onDragStop}
              colorMode="system"
              minZoom={0.02}
              maxZoom={4}
              nodesConnectable={false}
              nodesDraggable
              elementsSelectable
              onNodeClick={onNodeClick}
              onNodeMouseEnter={onNodeMouseEnter}
              onNodeMouseLeave={() => setHovered(undefined)}
              onPaneClick={() => setSelected(undefined)}
              zoomOnDoubleClick={false}
            >
              <Background variant={BackgroundVariant.Dots} gap={24} size={1.3} />
              <Controls showInteractive={false} position="bottom-right" fitViewOptions={{ padding: FIT_PADDING, maxZoom: 1.1, duration: 500 }} />
            </ReactFlow>
          </FocusContext.Provider>
          </ExpandContext.Provider>
        </InspectContext.Provider>
      </div>

      <div ref={ghostHost} className="viz-ghost-host" aria-hidden="true" />

      {loading && !view && <div className="viz-loading">Laying out the graph…</div>}
      {error && (
        <div className="viz-error">
          <ErrorAlert message={error} />
        </div>
      )}
      {empty && (
        <div className="viz-empty card">
          <EmptyState icon={Network} title="Nothing ingested yet" action={<button className="btn btn-primary" onClick={onExit}>Go to ingestion</button>}>
            Run a dry run on a repository and its graph appears here.
          </EmptyState>
        </div>
      )}

      {view && !empty && !loading && (
        <div className="viz-legend glass" aria-label="Legend">
          <span className="viz-hint">{hint(view)}</span>
          {legend.length > 0 && (
            <div className="viz-legend-edges">
              {legend.map((t) => (
                <span key={t} className="legend-edge">
                  <span className={`legend-line e-${t.toLowerCase()}`} />
                  {LEGEND[t] ?? EDGE_TYPES[t].label}
                </span>
              ))}
            </div>
          )}
        </div>
      )}

      {selected && (
        <DetailsPanel
          id={selected}
          onClose={() => setSelected(undefined)}
          onSelect={(id) => {
            setSelected(id)
            const n = flow.getInternalNode(id)
            if (n) {
              const { x, y } = n.internals.positionAbsolute
              flow.setCenter(x + (n.measured.width ?? 0) / 2, y + (n.measured.height ?? 0) / 2, { zoom: Math.max(flow.getZoom(), 0.9), duration: 450 })
            }
          }}
          onOpen={(kind, id) => {
            const r = routeFor(kind, id)
            if (r) go(r)
          }}
        />
      )}
    </div>
  )
}

// What each kind of line means, in the legend
const LEGEND: Record<string, string> = {
  CALLS: 'calls',
  READS_FROM: 'reads tables',
  WRITES_TO: 'writes tables',
  INVOKES: 'calls between files',
  STARTS: 'starts the flow',
}

function hint(view: GraphView): string {
  const s = view.summary ?? {}
  switch (view.view) {
    case 'organization':
      return 'Each space and how it talks to the others. Hover a label for the full story; click a space to zoom in.'
    case 'space':
      return 'What lives in this space, and what outside it talks to it. Click a tile to zoom in; Esc to go back up.'
    case 'application':
      return 'Each flow shows what it writes, reads, and calls. Hover a flow or table to draw its lines; click a flow to follow it.'
    case 'code':
      return 'Files grouped by folder; lines are calls between files.'
    default:
      return `Steps in the order they run (${s.shown} of ${s.functions} functions${s.significant_only ? ', the ones that touch data' : ''}); indented steps are called by the one above. A folded step shows what the steps inside it touch, and their lines start from it. Writes point into tables, reads point back into the step.`
  }
}

// ------------------------------------------------------------------ top bar

function TopBar({
  route,
  view,
  crumbs,
  onExit,
  onCrumb,
  onPick,
  options,
  onOptions,
  onResetArrangement,
  onFold,
}: {
  route: Route
  view?: GraphView
  crumbs: Crumb[]
  onExit: () => void
  onCrumb: (c: Crumb, index: number) => void
  onPick: (hit: SearchHit) => void
  options: ViewOptions
  onOptions: (o: ViewOptions) => void
  onResetArrangement: () => void
  /** Flow view: open every step (true), or go back to the outline (false) */
  onFold: (all: boolean) => void
}) {
  const current = crumbs[crumbs.length - 1]
  const kind = route.view === 'organization' ? 'organization' : route.view
  const Icon = kindStyle(kind).icon
  const title = route.view === 'flow' || route.view === 'application' ? (current?.label ?? '') : (view?.container?.label ?? current?.label ?? '')
  const [method, ...rest] = route.view === 'flow' ? title.split(' ') : ['']
  return (
    <header className="viz-top glass">
      <button className="viz-exit" onClick={onExit} title="Back to Steno admin" aria-label="Back to Steno admin">
        <ArrowLeft size={15} />
        <Logo size={22} />
      </button>
      <div className="viz-title">
        <nav className="crumbs" aria-label="Location">
          {crumbs.slice(0, -1).map((c, i) => (
            <span key={c.id} className="crumb">
              <button onClick={() => onCrumb(c, i)}>{c.label}</button>
              <ChevronRight size={12} className="crumb-sep" />
            </span>
          ))}
        </nav>
        <div className="viz-heading">
          <span className="viz-heading-icon"><Icon size={16} /></span>
          <span className="viz-kicker">{LEVELS[levelIndex(route)].label}</span>
          <h1>
            {method && route.view === 'flow' ? <span className={`method m-${method.toLowerCase()}`}>{method}</span> : null}
            {route.view === 'flow' ? rest.join(' ') : title}
          </h1>
          <span className="viz-facts">{facts(view)}</span>
        </div>
      </div>
      <div className="viz-tools">
        {route.view === 'application' && (
          <div className="seg" role="tablist" aria-label="Layer">
            {(['architecture', 'code'] as const).map((layer) => (
              <button key={layer} role="tab" aria-selected={route.layer === layer} className={route.layer === layer ? 'on' : ''} onClick={() => go({ ...route, layer })}>
                {layer === 'architecture' ? 'Architecture' : 'Code'}
              </button>
            ))}
          </div>
        )}
        {route.view === 'flow' && (
          <div className="seg" role="tablist" aria-label="Functions shown">
            <button role="tab" aria-selected={!route.all} className={!route.all ? 'on' : ''} onClick={() => go({ ...route, all: false })}>
              With effects
            </button>
            <button role="tab" aria-selected={route.all} className={route.all ? 'on' : ''} onClick={() => go({ ...route, all: true })}>
              All functions
            </button>
          </div>
        )}
        {route.view === 'flow' && (
          <div className="seg" aria-label="Folding">
            <button onClick={() => onFold(false)} title={`Show the first ${OPEN_DEPTH + 1} levels; expand a step to see inside it`}>
              Outline
            </button>
            <button onClick={() => onFold(true)} title="Show every step">
              Expand all
            </button>
          </div>
        )}
        <GraphSearch onPick={onPick} />
        <ViewMenu route={route} options={options} onChange={onOptions} onReset={onResetArrangement} />
      </div>
    </header>
  )
}

function facts(view?: GraphView): string {
  if (!view) return ''
  const plural = (n: number | undefined, w: string) => (n ? `${n} ${w}${n === 1 ? '' : 's'}` : null)
  const s = view.container?.stats
  if ((view.view === 'organization' || view.view === 'space') && s) {
    return [plural(s.spaces, 'space'), plural(s.applications, 'application'), plural(s.flows, 'flow'), plural(s.datastores, 'data store')]
      .filter(Boolean)
      .join(' · ')
  }
  const sum = view.summary ?? {}
  if (view.view === 'application') return [plural(sum.flows as number, 'flow'), sum.touches ? `touches ${sum.touches}` : null].filter(Boolean).join(' · ')
  if (view.view === 'flow') return plural(sum.functions as number, 'function') ? `${plural(sum.functions as number, 'function')} reached` : ''
  if (view.view === 'code') return plural(view.nodes.length, 'file') ?? ''
  return ''
}

// ---------------------------------------------------------------- depth rail

function DepthRail({ current, crumbs, onGo }: { current: number; crumbs: Crumb[]; onGo: (r: Route) => void }) {
  // The deepest crumb of each level is where its step leads
  const target = (key: string): Route | null => {
    const c = [...crumbs].reverse().find((x) => x.kind === key)
    return c ? routeFor(c.kind, c.id) : key === 'organization' ? { view: 'organization' } : null
  }
  return (
    <nav className="depth-rail glass" aria-label="Levels">
      {LEVELS.map((l, i) => {
        const to = i < current ? target(l.key) : null
        const Icon = kindStyle(l.key).icon
        return (
          <button
            key={l.key}
            className={['depth-step', i === current ? 'is-current' : '', i < current ? 'is-above' : '', i > current ? 'is-below' : ''].join(' ')}
            disabled={!to}
            onClick={() => to && onGo(to)}
            title={l.label}
            aria-current={i === current ? 'step' : undefined}
          >
            <span className="depth-dot"><Icon size={14} /></span>
            <span className="depth-label">{l.label}</span>
          </button>
        )
      })}
    </nav>
  )
}

// ----------------------------------------------------------------- view menu

function ViewMenu({ route, options, onChange, onReset }: { route: Route; options: ViewOptions; onChange: (o: ViewOptions) => void; onReset: () => void }) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    const close = (e: MouseEvent) => !ref.current?.contains(e.target as globalThis.Node) && setOpen(false)
    window.addEventListener('mousedown', close)
    return () => window.removeEventListener('mousedown', close)
  }, [open])
  const toggle = (key: 'externals' | 'data' | 'calls' | 'labels', label: string, note: string) => (
    <button role="menuitemcheckbox" aria-checked={options[key]} className="vmenu-item" onClick={() => onChange({ ...options, [key]: !options[key] })}>
      <span className={`vmenu-check ${options[key] ? 'on' : ''}`}>{options[key] && <Check size={12} />}</span>
      <span>
        <span className="vmenu-label">{label}</span>
        <span className="vmenu-note">{note}</span>
      </span>
    </button>
  )
  return (
    <div className="vmenu" ref={ref}>
      <button className={`btn btn-secondary btn-sm vmenu-button ${open ? 'is-open' : ''}`} onClick={() => setOpen(!open)} aria-expanded={open} aria-haspopup="menu">
        <SlidersHorizontal size={14} /> View
      </button>
      {open && (
        <div className="vmenu-pop glass" role="menu">
          <div className="vmenu-section">Show</div>
          {toggle('calls', 'Calls', 'Between applications and to outside systems')}
          {toggle('data', 'Data', 'Data stores, tables, reads and writes')}
          {toggle('externals', 'External systems', 'Vendors and hosts outside the organization')}
          {toggle('labels', 'Labels on lines', 'Counts like "4 calls"; always shown on hover')}
          {(route.view === 'organization' || route.view === 'space') && (
            <>
              <div className="vmenu-section">Direction</div>
              <div className="seg seg-full">
                {(
                  [
                    ['AUTO', 'Best fit'],
                    ['RIGHT', 'Left to right'],
                    ['DOWN', 'Top to bottom'],
                  ] as const
                ).map(([value, label]) => (
                  <button key={value} className={options.direction === value ? 'on' : ''} onClick={() => onChange({ ...options, direction: value })}>
                    {label}
                  </button>
                ))}
              </div>
            </>
          )}
          <div className="vmenu-section">Arrangement</div>
          <p className="vmenu-note vmenu-pad">Drag tiles to rearrange this view; it's remembered in this browser.</p>
          <button className="vmenu-item" onClick={onReset}>
            <span className="vmenu-check"><RotateCcw size={12} /></span>
            <span className="vmenu-label">Reset arrangement</span>
          </button>
        </div>
      )}
    </div>
  )
}

// -------------------------------------------------------------------- search

function GraphSearch({ onPick }: { onPick: (hit: SearchHit) => void }) {
  const [text, setText] = useState('')
  const [hits, setHits] = useState<SearchHit[]>([])
  const [open, setOpen] = useState(false)
  const timer = useRef<number>(undefined)

  useEffect(() => {
    window.clearTimeout(timer.current)
    if (!text.trim()) return
    timer.current = window.setTimeout(() => {
      api.graph.search(text).then(setHits).catch(() => setHits([]))
    }, 180)
  }, [text])

  return (
    <div className="gsearch">
      <Search size={14} className="gsearch-icon" aria-hidden="true" />
      <input
        id="graph-search"
        type="search"
        placeholder="Search flows, endpoints, tables…"
        value={text}
        onChange={(e) => {
          setText(e.target.value)
          setOpen(true)
        }}
        onFocus={() => setOpen(true)}
        onBlur={() => window.setTimeout(() => setOpen(false), 150)}
        onKeyDown={(e) => e.key === 'Escape' && (e.currentTarget.blur(), setOpen(false))}
        aria-label="Search the graph"
      />
      {open && text.trim() && hits.length > 0 && (
        <ul className="gsearch-results glass" role="listbox">
          {hits.map((h) => {
            const s = kindStyle(h.kind)
            const Icon = s.icon
            return (
              <li key={h.id}>
                <button
                  onMouseDown={(e) => e.preventDefault()}
                  onClick={() => {
                    onPick(h)
                    setOpen(false)
                  }}
                >
                  <Icon size={14} className={`tone-text-${s.tone}`} />
                  <span className="gsearch-label">{h.label}</span>
                  <span className="muted small">{h.sub ?? s.label}</span>
                </button>
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}

/** Open the natural level for a search hit: its own if it has one, else its application's. */
async function pick(hit: SearchHit, select: (id: string) => void) {
  const own = routeFor(hit.kind, hit.id)
  if (own) return go(own)
  try {
    const detail = await api.graph.node(hit.id)
    const app = detail.breadcrumbs.find((c) => c.kind === 'application')
    if (app && hit.kind !== 'function') {
      go({ view: 'application', id: app.id, layer: 'architecture' })
      window.setTimeout(() => select(hit.id), 1200)
      return
    }
  } catch {
    // fall through to showing the details
  }
  select(hit.id)
}
