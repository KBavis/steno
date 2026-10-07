import type { GraphEdge, GraphNode, GraphView } from '../../api/client'

/** Levels open when a flow is first shown: the entry, its calls, and theirs */
export const OPEN_DEPTH = 2

/** Which steps the viewer opened or closed; anything not listed follows OPEN_DEPTH */
export type Expansion = Record<string, boolean>

const KIND_OF: Record<string, 'writes' | 'reads' | 'calls'> = {
  WRITES_TO: 'writes',
  READS_FROM: 'reads',
  CALLS: 'calls',
  PRODUCES: 'calls',
  CONSUMES: 'reads',
}

/**
 * Fold a flow view to the steps the viewer has open. A closed step stands for everything under
 * it: it shows how many steps are inside and what they touch, and their lines to tables and
 * external systems are drawn from it, so no effect disappears when a branch is folded.
 * `all: true` opens everything.
 */
export function collapseFlow(view: GraphView, expansion: Expansion, all = false): GraphView {
  if (view.view !== 'flow') return view
  const steps = view.nodes.filter((n) => n.kind === 'function').sort((a, b) => (a.order ?? 0) - (b.order ?? 0))
  const others = view.nodes.filter((n) => n.kind !== 'function')
  const byId = new Map(steps.map((s) => [s.id, s]))
  const children = new Map<string, GraphNode[]>()
  for (const s of steps) {
    const p = s.parent_step && byId.has(s.parent_step) ? s.parent_step : ''
    children.set(p, [...(children.get(p) ?? []), s])
  }

  // Outline numbers (1, 1.1, 1.2, …) over the whole tree, so they don't change as it folds
  const outline = new Map<string, string>()
  const level = new Map<string, number>()
  const number = (parent: string, prefix: string, depth: number) =>
    (children.get(parent) ?? []).forEach((s, i) => {
      const label = prefix ? `${prefix}.${i + 1}` : String(i + 1)
      outline.set(s.id, label)
      level.set(s.id, depth)
      number(s.id, label, depth + 1)
    })
  number('', '', 0)

  const isOpen = (id: string) => all || (expansion[id] ?? (level.get(id) ?? 0) < OPEN_DEPTH)
  // Each step is drawn as itself, or as the nearest open ancestor's closed child it's folded into
  const shownAs = new Map<string, string>()
  const visible: GraphNode[] = []
  const walk = (parent: string, shown: string | null) => {
    for (const s of children.get(parent) ?? []) {
      const as = shown ?? s.id
      shownAs.set(s.id, as)
      if (as === s.id) visible.push(s)
      walk(s.id, shown ?? (isOpen(s.id) ? null : s.id))
    }
  }
  walk('', null)

  // What each folded step stands for: how many steps, and what they touch
  const inside = new Map<string, number>()
  const below = new Map<string, Record<string, Set<string>>>()
  for (const s of steps) {
    const as = shownAs.get(s.id)!
    if (as === s.id) continue
    inside.set(as, (inside.get(as) ?? 0) + 1)
    const bag = below.get(as) ?? {}
    for (const d of s.does ?? []) {
      const k = KIND_OF[d.type] ?? 'calls'
      ;(bag[k] ??= new Set()).add(d.target)
    }
    below.set(as, bag)
  }

  const order = new Map<string, number>()
  const shownSteps = visible.map((s, i) => {
    order.set(s.id, i + 1)
    const bag = below.get(s.id)
    const kids = (children.get(s.id) ?? []).length > 0
    return {
      ...s,
      order: i + 1,
      depth: level.get(s.id) ?? 0,
      outline: outline.get(s.id),
      has_children: kids,
      expanded: kids && isOpen(s.id),
      inside: inside.get(s.id) ?? 0,
      below: bag ? Object.fromEntries(Object.entries(bag).map(([k, v]) => [k, v.size])) : undefined,
    }
  })

  // Lines from folded steps start at the step they're folded into, merged per target
  const edges = new Map<string, GraphEdge>()
  for (const e of view.edges) {
    const src = shownAs.get(e.src) ?? e.src
    const id = `${e.type}|${src}|${e.dst}`
    const seen = edges.get(id)
    if (seen) edges.set(id, { ...seen, n: (seen.n ?? 1) + (e.n ?? 1), op: seen.op === e.op ? seen.op : undefined })
    else edges.set(id, { ...e, id, src })
  }

  // Each target sits beside the first shown step that uses it
  const firstUse = new Map<string, number>()
  for (const e of edges.values()) {
    const o = order.get(e.src)
    if (o !== undefined) firstUse.set(e.dst, Math.min(firstUse.get(e.dst) ?? Infinity, o))
  }
  const targets = others.map((n) => (n.kind === 'flow' ? n : { ...n, first_use: firstUse.get(n.id) ?? n.first_use }))

  return { ...view, nodes: [...targets, ...shownSteps], edges: [...edges.values()] }
}

/** Expansion that opens or closes every step that has steps under it */
export function expandAll(view: GraphView | undefined, open: boolean): Expansion {
  const out: Expansion = {}
  for (const n of view?.nodes ?? []) {
    if (n.kind === 'function' && view?.nodes.some((m) => m.parent_step === n.id)) out[n.id] = open
  }
  return out
}
