import { RoutedEdge } from './edges'
import { AppNode, ClusterNode, EntityNode, GhostNode, SpaceNode } from './nodes'

export const nodeTypes = { entity: EntityNode, space: SpaceNode, app: AppNode, ghost: GhostNode, cluster: ClusterNode }
export const edgeTypes = { routed: RoutedEdge }
