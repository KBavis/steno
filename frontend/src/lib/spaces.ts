import type { Space } from '../api/client'

export interface SpaceNode {
  space: Space
  depth: number
  path: string
}

/** Spaces in tree order (parents before children), with depth and a "Parent / Child" path. */
export function spaceTree(spaces: Space[]): SpaceNode[] {
  const children = new Map<number | null, Space[]>()
  for (const s of spaces) {
    const siblings = children.get(s.parent_id) ?? []
    siblings.push(s)
    children.set(s.parent_id, siblings)
  }
  const out: SpaceNode[] = []
  const walk = (parent: number | null, depth: number, prefix: string) => {
    for (const s of (children.get(parent) ?? []).sort((a, b) => a.name.localeCompare(b.name))) {
      const path = prefix ? `${prefix} / ${s.name}` : s.name
      out.push({ space: s, depth, path })
      walk(s.id, depth + 1, path)
    }
  }
  walk(null, 0, '')
  return out
}

/** A space and everything under it: the parents it can't be moved under. */
export function selfAndDescendants(spaces: Space[], id: number): Set<number> {
  const out = new Set([id])
  let grew = true
  while (grew) {
    grew = false
    for (const s of spaces) {
      if (s.parent_id !== null && out.has(s.parent_id) && !out.has(s.id)) {
        out.add(s.id)
        grew = true
      }
    }
  }
  return out
}
