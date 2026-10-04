import { RoutedEdge } from './edges'
import { AppNode, ClusterNode, EntityNode, FlowHeadNode, FlowRowNode, GhostNode, SpaceNode, StepRowNode, TargetRowNode } from './nodes'

export const nodeTypes = {
  entity: EntityNode,
  space: SpaceNode,
  app: AppNode,
  ghost: GhostNode,
  cluster: ClusterNode,
  flowrow: FlowRowNode,
  targetrow: TargetRowNode,
  flowhead: FlowHeadNode,
  steprow: StepRowNode,
}
export const edgeTypes = { routed: RoutedEdge }
