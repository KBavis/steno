// Typed client for the Admin API (backend/src/steno/api). Types mirror api/schemas.py.

export type JobMode = 'full' | 'dry_run'
export type JobStatus = 'queued' | 'running' | 'succeeded' | 'failed'
export type StageName = 'clone' | 'deps' | 'parse' | 'extract' | 'assemble' | 'flows' | 'write' | 'coverage' | 'cards'
export type StageStatus = 'running' | 'succeeded' | 'failed' | 'skipped'

export interface Health {
  status: 'ok' | 'degraded'
  version: string
  checks: Record<string, string>
}

export interface Organization {
  name: string
  description: string | null
  onboarded_at: string | null
}
export type OrganizationInput = Pick<Organization, 'name' | 'description'>

export type ConnectorKind = 'bitbucket' | 'github' | 'gitlab'

export interface Connector {
  id: number
  name: string
  kind: ConnectorKind
  base_url: string
  scope: Record<string, string>
  credentials_ref: string | null
}
export type ConnectorInput = Omit<Connector, 'id'>

export interface Space {
  id: number
  parent_id: number | null
  name: string
  description: string | null
}

export type SpaceInput = Omit<Space, 'id'>

export interface Repository {
  id: number
  connector_id: number
  space_id: number | null
  name: string
  clone_url: string
  default_branch: string
  last_ingested_sha: string | null
}

export type RepositoryInput = Omit<Repository, 'id' | 'last_ingested_sha'>

export interface Job {
  id: number
  repository_id: number
  trigger: 'initial' | 'nightly' | 'manual'
  mode: JobMode
  status: JobStatus
  from_sha: string | null
  to_sha: string | null
  queued_at: string
  started_at: string | null
  finished_at: string | null
  error: string | null
  stats: Record<string, unknown>
}

export interface Stage {
  id: number
  stage: StageName
  status: StageStatus
  started_at: string | null
  finished_at: string | null
  metrics: Record<string, unknown>
  llm_cost: string
  jev_cost: string
}

export interface JobDetail extends Job {
  stages: Stage[]
}

// ---------------------------------------------------------------- coverage

export type CoverageStatus = 'unexplained' | 'ignored' | 'explained'
export type CoverageSignal = 'unknown_host' | 'external_call' | 'library' | 'unreachable_effect' | 'dropped_match' | 'unparsed'

export interface CoverageSample {
  file?: string | null
  line?: number | null
  function?: string | null
  detail?: string | null
  repository: string
  url?: string | null
}
export interface CoverageItem {
  kind: string
  signal: CoverageSignal
  target: string
  label: string
  /** In the current scope */
  occurrences: number
  status: CoverageStatus
  applications: { name: string; occurrences: number; space: string | null }[]
  /** How many spaces those applications are in */
  spaces: number
  samples: CoverageSample[]
}
export interface CoverageNumbers {
  applications: number
  functions: number
  functions_reachable: number
  reachable_pct: number | null
  io_calls: number
  io_calls_explained: number
  io_explained_pct: number | null
  open_items: number
}
/** A space or application under the current scope, with its rolled-up numbers */
export interface CoverageRow extends CoverageNumbers {
  scope: string
  kind: 'space' | 'application'
  label: string
}
export interface CoverageReport {
  scope: string
  breadcrumbs: { scope: string; label: string }[]
  summary: CoverageNumbers
  children: CoverageRow[]
  items: CoverageItem[]
  runs: { application: string; repository: string; job_id: number; commit: string | null; finished_at: string | null }[]
}

// ---------------------------------------------------------------- graph views

export interface GraphNode {
  id: string
  kind: string
  label: string
  sub?: string | null
  parent?: string | null
  stub?: boolean
  drill?: 'space' | 'app' | 'flow' | null
  /** Organization and space levels: outside the current container (a neighbor) */
  outside?: boolean
  /** Index of the top-level space the node sits in, so it keeps one color at every level */
  hue?: number | null
  description?: string | null
  stats?: Record<string, number>
  /** A space's largest applications */
  preview?: string[]
  /** Application view: what a flow does (tables written and read, calls made) */
  effects?: { writes?: number; reads?: number; calls?: number }
  /** Application view: how many flows write, read, or call a target */
  usage?: { writes?: number; reads?: number; calls?: number }
  /** Flow view: a step's place in execution order and call depth */
  order?: number
  depth?: number
  conditional?: boolean
  container?: string | null
  /** Flow view: the subclass an inherited method runs as (Task.run as DiffTaskRunner) */
  bound_to?: string | null
  /** Flow view: one of several implementations that can run here, chosen at runtime */
  candidate?: boolean
  /** Trace entries: a helper called from many places, never expanded; a call to a function already shown above */
  utility?: boolean
  repeat?: boolean
  /** Flow view: what a step does, in words (operation and target) */
  does?: { type: string; op?: string | null; target: string }[]
  /** Flow view: the first step that uses a target */
  first_use?: number | null
  /** Flow view: the step that calls this one (nearest one shown), and its place in the outline (1.2.3) */
  parent_step?: string | null
  outline?: string
  /** Flow view, folding: whether it has steps under it, whether they're shown, how many are folded into it */
  has_children?: boolean
  expanded?: boolean
  inside?: number
  /** Flow view: what the steps folded into this one do (distinct targets per kind) */
  below?: { writes?: number; reads?: number; calls?: number }
  /** Flow view header */
  path?: string
  trigger?: string | null
  purpose?: string | null
  method?: string
  significant?: boolean
  is_async?: boolean
  functions?: number
}
export interface GraphGroup {
  id: string
  kind: string
  label: string
  parent: string | null
  stub?: boolean
}
export interface GraphEdge {
  id: string
  src: string
  dst: string
  type: string
  n: number
  label?: string
  async?: boolean
  conditional?: boolean
  op?: string
}
export interface Crumb {
  id: string
  label: string
  kind: string
}
export interface GraphView {
  view: 'organization' | 'space' | 'application' | 'code' | 'flow'
  focus?: string
  /** Organization and space levels: the container being shown */
  container?: GraphNode
  nodes: GraphNode[]
  groups: GraphGroup[]
  edges: GraphEdge[]
  breadcrumbs: Crumb[]
  summary?: Record<string, number | boolean>
  empty?: boolean
}
export interface NodeDetail {
  id: string
  kind: string
  labels: string[]
  label: string
  properties: Record<string, unknown>
  provenance: Record<string, string | number>
  neighbors: { type: string; dir: 'in' | 'out'; node: { id: string; kind: string; label: string }; props: Record<string, unknown> }[]
  breadcrumbs: Crumb[]
  source_url: string | null
}
export interface SearchHit {
  id: string
  kind: string
  label: string
  sub?: string
}

const q = (params: Record<string, string | boolean>) =>
  new URLSearchParams(Object.entries(params).map(([k, v]) => [k, String(v)])).toString()

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, {
    ...init,
    headers: { 'content-type': 'application/json', ...init?.headers },
  })
  if (!res.ok) throw new Error(await errorMessage(res))
  return (res.status === 204 ? undefined : await res.json()) as T
}

/** FastAPI errors: {detail: string} or, for validation, {detail: [{loc, msg}]} */
async function errorMessage(res: Response): Promise<string> {
  const text = await res.text()
  try {
    const { detail } = JSON.parse(text)
    if (typeof detail === 'string') return detail
    if (Array.isArray(detail)) {
      return detail.map((d) => `${d.loc?.slice(1).join('.') ?? ''}: ${d.msg}`).join('; ')
    }
  } catch {
    // not JSON
  }
  return `${res.status} ${text || res.statusText}`
}

/** create / update / delete for one Admin API collection */
function crud<T, In>(path: string) {
  return {
    list: () => request<T[]>(path),
    create: (body: In) => request<T>(path, { method: 'POST', body: JSON.stringify(body) }),
    update: (id: number, body: In) =>
      request<T>(`${path}/${id}`, { method: 'PUT', body: JSON.stringify(body) }),
    remove: (id: number) => request<void>(`${path}/${id}`, { method: 'DELETE' }),
  }
}

export const api = {
  health: () => request<Health>('/health'),
  organization: {
    /** null until onboarding creates it */
    get: () =>
      request<Organization>('/organization').catch((e: Error) => {
        if (e.message.startsWith('no organization yet')) return null
        throw e
      }),
    put: (body: OrganizationInput) =>
      request<Organization>('/organization', { method: 'PUT', body: JSON.stringify(body) }),
    completeOnboarding: () => request<Organization>('/organization/onboarding/complete', { method: 'POST' }),
  },
  spaces: crud<Space, SpaceInput>('/spaces'),
  connectors: crud<Connector, ConnectorInput>('/connectors'),
  repositories: crud<Repository, RepositoryInput>('/repositories'),
  jobs: () => request<Job[]>('/jobs'),
  graph: {
    overview: () => request<GraphView>('/graph/overview'),
    space: (id: string) => request<GraphView>(`/graph/space?${q({ id })}`),
    application: (id: string, layer: 'architecture' | 'code') =>
      request<GraphView>(`/graph/application?${q({ id, layer })}`),
    flow: (id: string, significantOnly: boolean) =>
      request<GraphView>(`/graph/flow?${q({ id, significant_only: significantOnly })}`),
    node: (id: string) => request<NodeDetail>(`/graph/node?${q({ id })}`),
    search: (text: string) => request<SearchHit[]>(`/graph/search?${q({ q: text })}`),
  },
  job: (id: number) => request<JobDetail>(`/jobs/${id}`),
  coverage: {
    report: (scope = 'org') => request<CoverageReport>(`/coverage?${q({ scope })}`),
    triage: (kind: string, target: string, status: CoverageStatus) =>
      request<{ updated: number }>('/coverage/triage', { method: 'PUT', body: JSON.stringify({ kind, target, status }) }),
  },
  createJob: (repository_id: number, mode: JobMode) =>
    request<Job>('/jobs', { method: 'POST', body: JSON.stringify({ repository_id, mode }) }),
}
