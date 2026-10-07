import {
  AppWindow,
  Braces,
  Building2,
  Cloud,
  Database,
  FileCode2,
  Folder,
  ListOrdered,
  FolderGit2,
  Globe,
  Layers,
  Package,
  Route,
  Shapes,
  Table,
  Workflow,
  type LucideIcon,
} from 'lucide-react'

export interface KindStyle {
  label: string
  icon: LucideIcon
  /** CSS custom property family: --k-<tone> (ink) and --k-<tone>-soft (fill) */
  tone: 'org' | 'space' | 'app' | 'flow' | 'iface' | 'data' | 'ext' | 'code'
}

export const KINDS: Record<string, KindStyle> = {
  organization: { label: 'Organization', icon: Building2, tone: 'org' },
  space: { label: 'Space', icon: Layers, tone: 'space' },
  application: { label: 'Application', icon: AppWindow, tone: 'app' },
  flow: { label: 'Flow', icon: Workflow, tone: 'flow' },
  step: { label: 'Step', icon: ListOrdered, tone: 'flow' },
  endpoint: { label: 'Endpoint', icon: Globe, tone: 'iface' },
  interface: { label: 'Interface', icon: Globe, tone: 'iface' },
  entity: { label: 'Entity', icon: Shapes, tone: 'data' },
  table: { label: 'Table', icon: Table, tone: 'data' },
  datastore: { label: 'Data store', icon: Database, tone: 'data' },
  external: { label: 'External system', icon: Cloud, tone: 'ext' },
  repository: { label: 'Repository', icon: FolderGit2, tone: 'code' },
  module: { label: 'Module', icon: Package, tone: 'code' },
  file: { label: 'File', icon: FileCode2, tone: 'code' },
  // Functions aren't graph nodes; this is a flow's trace entry (D60)
  function: { label: 'Function', icon: Braces, tone: 'code' },
  // group kinds
  resource: { label: 'Resource', icon: Route, tone: 'flow' },
  externals: { label: 'External', icon: Cloud, tone: 'ext' },
  folder: { label: 'Folder', icon: Folder, tone: 'code' },
}

export const kindStyle = (kind: string): KindStyle => KINDS[kind] ?? { label: kind, icon: Shapes, tone: 'code' }

/** Edge types and how they read in the legend */
export const EDGE_TYPES: Record<string, { label: string; tone: string }> = {
  READS_FROM: { label: 'reads', tone: 'read' },
  WRITES_TO: { label: 'writes', tone: 'write' },
  CALLS: { label: 'calls', tone: 'call' },
  INVOKES: { label: 'calls functions in', tone: 'invoke' },
  STARTS: { label: 'starts', tone: 'bridge' },
  FIRST_STEP: { label: 'first step', tone: 'bridge' },
  NEXT: { label: 'then', tone: 'bridge' },
  SUBSTEP: { label: 'first substep', tone: 'bridge' },
  PRODUCES: { label: 'produces', tone: 'call' },
  CONSUMES: { label: 'consumes', tone: 'call' },
}
