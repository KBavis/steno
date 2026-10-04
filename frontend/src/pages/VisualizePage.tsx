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
  type Viewport,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { ArrowLeft, Check, ChevronRight, Network, RotateCcw, Search, SlidersHorizontal } from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, type Crumb, type GraphView, type SearchHit } from '../api/client'
import { DetailsPanel } from '../components/graph/DetailsPanel'
import { FocusContext, InspectContext } from '../components/graph/focus'
import '../components/graph/graph.css'
import { DEFAULT_OPTIONS, filterView, layoutView, type EntityData, type ViewOptions } from '../components/graph/layout'
import { edgeTypes, nodeTypes } from '../components/graph/nodeTypes'
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

const reducedMotion = () => window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false
const wait = (ms: number) => new Promise((resolve) => window.setTimeout(resolve, ms))
const FIT_PADDING: NonNullable<FitViewOptions['padding']> = { top: '96px', left: '84px', right: '40px', bottom: '64px' }

// -------------------------------------------------------------------- page

export function VisualizePage({ onExit }: { onExit: () => void }) {
  return (
    <ReactFlowProvider>
      <Visualizer onExit={onExit} />
    </ReactFlowProvider>
  )
}

function Visualizer({ onExit }: { onExit: () => void }) {
  const [route, setRoute] = useState<Route>(parseRoute)
  const [view, setView] = useState<GraphView>()
  const [nodes, setNodes] = useState<Node[]>([])
  const [baseEdges, setBaseEdges] = useState<Edge[]>([])
  const [error, setError] = useState<string>()
  const [loading, setLoading] = useState(true)
  const [hidden, setHidden] = useState(true)
  const [selected, setSelected] = useState<string>()
  const [hovered, setHovered] = useState<string>()
  const [options, setOptions] = useState<ViewOptions>(loadOptions)
  const [arrangement, setArrangement] = useState(0)
  const flow = useReactFlow()
  const initialized = useNodesInitialized()
  // How to bring the next view in: the previous view's depth and focus decide in or out
  const previous = useRef<{ depth: number; focus?: string } | null>(null)
  const entering = useRef(false)

  useEffect(() => {
    const onHash = () => setRoute(parseRoute())
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])

  const routeKey = formatRoute(route)

  const loadedKey = useRef<string>(undefined)
  useEffect(() => {
    let live = true
    // A new place fades through; a changed setting on the same view swaps in place
    if (loadedKey.current !== routeKey) setHidden(true)
    loadedKey.current = routeKey
    setError(undefined)
    setSelected(undefined)
    setHovered(undefined)
    setLoading(true)
    const load =
      route.view === 'space'
        ? api.graph.space(route.id)
        : route.view === 'application'
          ? api.graph.application(route.id, route.layer)
          : route.view === 'flow'
            ? api.graph.flow(route.id, !route.all)
            : api.graph.overview()
    load
      .then(async (v) => {
        rememberHues(v)
        const stage = document.querySelector('.viz-stage')
        const room = { width: (stage?.clientWidth ?? 1400) - 124, height: (stage?.clientHeight ?? 900) - 160 }
        // Only the organization and space levels choose a direction; other views have their own
        const direction = v.view === 'flow' ? 'DOWN' : v.view === 'organization' || v.view === 'space' ? options.direction : 'RIGHT'
        const laid = await layoutView(filterView(v, options), direction, room)
        if (!live) return
        const saved = loadPositions(routeKey)
        setView(v)
        setNodes(laid.nodes.map((n) => (saved[n.id] ? { ...n, position: saved[n.id] } : n)))
        setBaseEdges(laid.edges)
        setLoading(false)
        entering.current = true
      })
      .catch((e: Error) => {
        if (!live) return
        setError(e.message)
        setLoading(false)
        setHidden(false)
      })
    return () => {
      live = false
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [routeKey, options, arrangement])

  // Bring a freshly laid-out view in: from slightly far when going deeper, from the tile we
  // came out of when going up, so moving between levels feels like one continuous canvas.
  useEffect(() => {
    if (!initialized || !entering.current || !view) return
    entering.current = false
    const depth = view.breadcrumbs.length
    const before = previous.current
    previous.current = { depth, focus: view.focus }
    // Computed rather than read back: fitView applies asynchronously
    const stage = document.querySelector('.viz-stage')
    const width = stage?.clientWidth ?? 1200
    const height = stage?.clientHeight ?? 800
    const fit = getViewportForBounds(flow.getNodesBounds(flow.getNodes()), width, height, 0.06, 1.1, FIT_PADDING)
    let start: Viewport = fit
    if (before && !reducedMotion()) {
      const focus = before.focus ? flow.getInternalNode(before.focus) : undefined
      if (depth < before.depth && focus) {
        const { x, y } = focus.internals.positionAbsolute
        const box = { x, y, width: focus.measured.width ?? 200, height: focus.measured.height ?? 100 }
        start = getViewportForBounds(box, width, height, 0.06, 4, 0.05)
      } else {
        const cx = width / 2
        const cy = height / 2
        const k = depth > before.depth ? 0.82 : depth < before.depth ? 1.18 : before.focus === view.focus ? 1 : 0.94
        start = { zoom: fit.zoom * k, x: cx - (cx - fit.x) * k, y: cy - (cy - fit.y) * k }
      }
    }
    flow.setViewport(start)
    setHidden(false)
    requestAnimationFrame(() => flow.setViewport(fit, { duration: reducedMotion() ? 0 : 700 }))
  }, [initialized, view, flow])

  /** Zoom the camera into a tile, then open the level inside it */
  const enter = useCallback(
    async (target: Route, nodeId?: string) => {
      setSelected(undefined)
      if (formatRoute(target) === window.location.hash.slice(1)) return
      const n = nodeId ? flow.getInternalNode(nodeId) : undefined
      if (n && !reducedMotion()) {
        const { x, y } = n.internals.positionAbsolute
        flow.fitBounds({ x, y, width: n.measured.width ?? 200, height: n.measured.height ?? 100 }, { duration: 480, padding: 0.04 })
        await wait(300)
        setHidden(true)
        await wait(160)
      } else {
        setHidden(true)
      }
      go(target)
    },
    [flow],
  )

  /** Pull the camera back, then open the level above */
  const ascend = useCallback(
    async (target: Route) => {
      setSelected(undefined)
      if (formatRoute(target) === window.location.hash.slice(1)) return
      if (!reducedMotion()) {
        flow.zoomTo(flow.getZoom() * 0.5, { duration: 360 })
        await wait(200)
        setHidden(true)
        await wait(160)
      } else {
        setHidden(true)
      }
      go(target)
    },
    [flow],
  )

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
      else if (parentRoute) void ascend(parentRoute)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [selected, parentRoute, ascend])

  // Hover or selection lights up a node's connections and dims the rest. Nodes read the focus
  // from context so they keep their identity; rebuilding them mid-hover swallows clicks.
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
        zIndex: live.has(e.id) ? 10 : 0,
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
      if (target && formatRoute(target) !== routeKey) void enter(target, n.id)
      else setSelected(n.id)
    },
    [enter, routeKey],
  )

  const updateOptions = (o: ViewOptions) => {
    setOptions(o)
    saveOptions(o)
  }

  const legend = useMemo(() => [...new Set(baseEdges.map((e) => (e.data as { type: string }).type))].filter((t) => EDGE_TYPES[t]), [baseEdges])
  const empty = !loading && view && (view.empty || view.nodes.length === 0) && route.view === 'organization'
  const hue = view ? (view.container?.hue ?? hueOf([...crumbs].reverse())) : undefined
  const dense = baseEdges.length > 40

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
          void ascend(r)
        }}
        onPick={(hit) => pick(hit, enter, setSelected)}
        options={options}
        onOptions={updateOptions}
        onResetArrangement={() => {
          savePositions(routeKey, null)
          setArrangement((n) => n + 1)
        }}
      />

      <DepthRail
        current={levelIndex(route)}
        crumbs={crumbs}
        onGo={(r) => {
          if (formatRoute(r) !== routeKey) void ascend(r)
        }}
      />

      <div className={`viz-stage ${hidden ? 'is-hidden' : ''} ${dense ? 'is-dense' : ''} view-${view?.view ?? 'loading'}`}>
        <InspectContext.Provider value={setSelected}>
          <FocusContext.Provider value={near}>
            <ReactFlow
              nodes={nodes}
              edges={edges}
              nodeTypes={nodeTypes}
              edgeTypes={edgeTypes}
              onNodesChange={onNodesChange}
              onNodeDragStop={onDragStop}
              colorMode="system"
              minZoom={0.06}
              maxZoom={2.5}
              nodesConnectable={false}
              nodesDraggable
              elementsSelectable
              onNodeClick={onNodeClick}
              onNodeMouseEnter={(_, n) => n.type !== 'cluster' && setHovered(n.id)}
              onNodeMouseLeave={() => setHovered(undefined)}
              onPaneClick={() => setSelected(undefined)}
              zoomOnDoubleClick={false}
            >
              <Background variant={BackgroundVariant.Dots} gap={24} size={1.3} />
              <Controls showInteractive={false} position="bottom-right" fitViewOptions={{ padding: FIT_PADDING, maxZoom: 1.1, duration: 500 }} />
            </ReactFlow>
          </FocusContext.Provider>
        </InspectContext.Provider>
      </div>

      {loading && <div className="viz-loading">Laying out the graph…</div>}
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
                  {EDGE_TYPES[t].label}
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
            if (r) void enter(r, flow.getInternalNode(id) ? id : undefined)
          }}
        />
      )}
    </div>
  )
}

function hint(view: GraphView): string {
  const s = view.summary ?? {}
  switch (view.view) {
    case 'organization':
      return 'Each space and how it talks to the others. Click a space to zoom in.'
    case 'space':
      return 'What lives in this space, and what outside it talks to it. Click a tile to zoom in; Esc to go back up.'
    case 'application':
      return 'Flows grouped by resource, and what they touch. Click a flow to follow it.'
    case 'code':
      return 'Files grouped by folder; lines are calls between files.'
    default:
      return `${s.shown} of ${s.functions} functions${s.significant_only ? ', the ones with effects' : ''}. Click one for its details.`
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
async function pick(hit: SearchHit, enter: (r: Route) => Promise<void>, select: (id: string) => void) {
  const own = routeFor(hit.kind, hit.id)
  if (own) return enter(own)
  try {
    const detail = await api.graph.node(hit.id)
    const app = detail.breadcrumbs.find((c) => c.kind === 'application')
    if (app && hit.kind !== 'function') {
      await enter({ view: 'application', id: app.id, layer: 'architecture' })
      window.setTimeout(() => select(hit.id), 900)
      return
    }
  } catch {
    // fall through to showing the details
  }
  select(hit.id)
}
