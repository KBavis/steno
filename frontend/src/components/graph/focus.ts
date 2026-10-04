import { createContext } from 'react'

/** The hovered or selected node and its direct neighbors; null when nothing is focused. */
export const FocusContext = createContext<ReadonlySet<string> | null>(null)

/** Open a node's details without zooming into it (the (i) on a tile) */
export const InspectContext = createContext<(id: string) => void>(() => {})
