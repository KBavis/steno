// Typed client for the Admin API (backend/src/steno/api). Types mirror api/schemas.py.

export type JobMode = 'full' | 'incremental' | 'dry_run'
export type JobStatus = 'queued' | 'running' | 'succeeded' | 'failed'
export type StageName = 'clone' | 'deps' | 'parse' | 'resolve' | 'flows' | 'write' | 'cards'
export type StageStatus = 'running' | 'succeeded' | 'failed' | 'skipped'

export interface Health {
  status: 'ok' | 'degraded'
  version: string
  checks: Record<string, string>
}

export interface Space {
  id: number
  parent_id: number | null
  name: string
  description: string | null
}

export interface Repository {
  id: number
  connector_id: number
  space_id: number | null
  name: string
  clone_url: string
  default_branch: string
  last_ingested_sha: string | null
}

export interface Job {
  id: number
  repository_id: number
  trigger: 'initial' | 'merge' | 'manual'
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

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, {
    ...init,
    headers: { 'content-type': 'application/json', ...init?.headers },
  })
  if (!res.ok) {
    throw new Error(`${init?.method ?? 'GET'} /api${path}: ${res.status} ${await res.text()}`)
  }
  return res.json() as Promise<T>
}

export const api = {
  health: () => request<Health>('/health'),
  spaces: () => request<Space[]>('/spaces'),
  repositories: () => request<Repository[]>('/repositories'),
  jobs: () => request<Job[]>('/jobs'),
  job: (id: number) => request<JobDetail>(`/jobs/${id}`),
  createJob: (repository_id: number, mode: JobMode) =>
    request<Job>('/jobs', { method: 'POST', body: JSON.stringify({ repository_id, mode }) }),
}
