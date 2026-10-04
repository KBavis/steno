/**
 * Zoom-through transitions between levels.
 *
 * Going in, the next level starts inside the tile that was clicked and grows to fill the window,
 * while a snapshot of the level being left keeps growing past the camera and fades. Going out is
 * the reverse: the level being left shrinks into its tile in the level above. Both move together,
 * frame by frame, from the same anchor, so the two views stay lined up the whole way.
 */
import type { Viewport } from '@xyflow/react'

export interface Rect {
  x: number
  y: number
  width: number
  height: number
}
interface Point {
  x: number
  y: number
}

export type Move =
  /** Into a tile: its rect on screen in the view being left */
  | { kind: 'in'; tile: Rect }
  /** Out of a container: the screen rect the old view's content filled, and the container's
   *  rect in the new view (world coordinates) */
  | { kind: 'out'; from: Rect; tile: Rect }
  | { kind: 'fade' }

const ease = (t: number) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2)
const ramp = (e: number, from: number, to: number) => Math.min(1, Math.max(0, (e - from) / (to - from)))
const center = (r: Rect): Point => ({ x: r.x + r.width / 2, y: r.y + r.height / 2 })
const lerp = (a: Point, b: Point, t: number): Point => ({ x: a.x + (b.x - a.x) * t, y: a.y + (b.y - a.y) * t })

export const reducedMotion = () => window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false

/** A static copy of what's on screen now, to animate away while the next view comes in */
export function snapshot(stage: HTMLElement, host: HTMLElement): HTMLElement | null {
  const flow = stage.querySelector('.react-flow')
  const viewport = stage.querySelector('.react-flow__viewport')
  if (!flow || !viewport) return null
  const ghost = document.createElement('div')
  // Same classes as the live stage, so the copy looks exactly like what it replaces
  ghost.className = `${stage.className} viz-ghost`
  const shell = document.createElement('div')
  shell.className = `${flow.className} viz-ghost-flow`
  shell.appendChild(viewport.cloneNode(true))
  ghost.appendChild(shell)
  host.replaceChildren(ghost)
  return ghost
}

/**
 * Run a move. `world` is the new view's content bounds; `fit` is where the camera ends.
 * Returns a cancel function.
 */
export function play(
  move: Move,
  opts: {
    world: Rect
    fit: Viewport
    stage: HTMLElement
    ghost: HTMLElement | null
    setViewport: (v: Viewport) => void
    maxZoom: number
    onDone: () => void
  },
): () => void {
  const { world, fit, stage, ghost, setViewport, onDone } = opts
  // The world point that stays anchored, and where it sits on screen at the start and end
  let anchor: Point
  let z0: number
  let s0: Point
  let ghostFrom: Point
  if (move.kind === 'in') {
    anchor = center(world)
    z0 = Math.min(move.tile.width / world.width, move.tile.height / world.height)
    s0 = center(move.tile)
    ghostFrom = s0
  } else if (move.kind === 'out') {
    anchor = center(move.tile)
    z0 = Math.min(opts.maxZoom, move.from.width / move.tile.width, move.from.height / move.tile.height)
    s0 = center(move.from)
    ghostFrom = s0
  } else {
    anchor = center(world)
    z0 = fit.zoom * 0.96
    s0 = { x: anchor.x * fit.zoom + fit.x, y: anchor.y * fit.zoom + fit.y }
    ghostFrom = s0
  }
  const z1 = fit.zoom
  const s1 = { x: anchor.x * z1 + fit.x, y: anchor.y * z1 + fit.y }
  const duration = reducedMotion() ? 0 : move.kind === 'fade' ? 420 : 900

  const frame = (e: number) => {
    const z = z0 * Math.pow(z1 / z0, e)
    const s = lerp(s0, s1, e)
    setViewport({ zoom: z, x: s.x - anchor.x * z, y: s.y - anchor.y * z })
    if (move.kind === 'in') {
      stage.style.opacity = String(ramp(e, 0.05, 0.55))
      if (ghost) place(ghost, ghostFrom, s, z / z0, 1 - ramp(e, 0.12, 0.62))
    } else if (move.kind === 'out') {
      stage.style.opacity = String(ramp(e, 0, 0.45))
      if (ghost) place(ghost, ghostFrom, s, z / z0, 1 - ramp(e, 0.35, 0.9))
    } else {
      stage.style.opacity = String(ramp(e, 0, 1))
      if (ghost) ghost.style.opacity = String(1 - ramp(e, 0, 0.8))
    }
  }

  let raf = 0
  let stopped = false
  const finish = () => {
    if (stopped) return
    stopped = true
    setViewport(fit)
    stage.style.opacity = ''
    ghost?.remove()
    onDone()
  }
  if (duration === 0) {
    finish()
    return () => {}
  }
  const start = performance.now()
  const tick = (now: number) => {
    if (stopped) return
    const t = Math.min(1, (now - start) / duration)
    frame(ease(t))
    if (t < 1) raf = requestAnimationFrame(tick)
    else finish()
  }
  frame(0)
  raf = requestAnimationFrame(tick)
  return () => {
    cancelAnimationFrame(raf)
    finish()
  }
}

/** Scale the snapshot by `k` about the point `from`, which moves to `to` */
function place(ghost: HTMLElement, from: Point, to: Point, k: number, opacity: number) {
  ghost.style.transform = `translate(${to.x - from.x * k}px, ${to.y - from.y * k}px) scale(${k})`
  ghost.style.opacity = String(opacity)
}
