import { Activity, Check, ChevronRight, Loader2, X } from 'lucide-react'
import { useCallback, useState } from 'react'
import { api, type Job, type JobStatus, type Stage, type StageName } from '../api/client'
import { usePoll } from '../hooks/usePoll'
import { duration, timeAgo } from '../lib/time'
import { EmptyState, ErrorAlert } from './ui'

const STAGES: StageName[] = ['clone', 'deps', 'parse', 'resolve', 'flows', 'write', 'cards']

const STATUS_BADGE: Record<JobStatus, string> = {
  queued: 'badge',
  running: 'badge badge-warn',
  succeeded: 'badge badge-ok',
  failed: 'badge badge-fail',
}

const MODE_LABEL = { dry_run: 'Dry run', full: 'Full', incremental: 'Incremental' }

export function Jobs({ jobs, error, repoName }: { jobs?: Job[]; error?: string; repoName: (id: number) => string }) {
  const [open, setOpen] = useState<number>()

  return (
    <>
      <div className="section-title">
        <h2>Recent jobs</h2>
      </div>
      <ErrorAlert message={error} />
      <div className="card">
        {jobs?.length === 0 ? (
          <EmptyState icon={Activity} title="No jobs yet">
            Start a dry run on a repository to see each ingestion stage here.
          </EmptyState>
        ) : (
          <ul className="list">
            {jobs?.map((j) => (
              <li key={j.id}>
                <div
                  className={`list-row clickable ${open === j.id ? 'selected' : ''}`}
                  onClick={() => setOpen(open === j.id ? undefined : j.id)}
                >
                  <ChevronRight
                    size={16}
                    className="muted"
                    style={{ transform: open === j.id ? 'rotate(90deg)' : undefined, transition: 'transform .15s' }}
                  />
                  <span className={`${STATUS_BADGE[j.status]} badge-dot`}>{j.status}</span>
                  <div className="list-main">
                    <div className="list-title">
                      {repoName(j.repository_id)}
                      <span className="badge badge-accent">{MODE_LABEL[j.mode]}</span>
                    </div>
                    <div className="list-sub">
                      #{j.id} · {j.trigger} · queued {timeAgo(j.queued_at)}
                    </div>
                  </div>
                  <span className="muted small">{duration(j.started_at, j.finished_at) ?? ''}</span>
                </div>
                {open === j.id && <JobDetail id={j.id} />}
              </li>
            ))}
          </ul>
        )}
      </div>
    </>
  )
}

function JobDetail({ id }: { id: number }) {
  const load = useCallback(() => api.job(id), [id])
  const { data } = usePoll(load)
  const byName = new Map(data?.stages.map((s) => [s.stage, s]))

  return (
    <div className="job-detail">
      <ol className="pipeline">
        {STAGES.map((name) => {
          const stage = byName.get(name)
          return (
            <li
              key={name}
              className={stage?.status ?? 'pending'}
              title={typeof stage?.metrics.reason === 'string' ? `Skipped: ${stage.metrics.reason}` : undefined}
            >
              <StageIcon stage={stage} />
              <span className="stage-name">{name}</span>
              <span className="muted small">{stage ? (duration(stage.started_at, stage.finished_at) ?? '…') : ''}</span>
            </li>
          )
        })}
      </ol>
      {data?.error && (
        <div className="alert" style={{ marginTop: 16 }}>
          {data.error}
        </div>
      )}
    </div>
  )
}

function StageIcon({ stage }: { stage?: Stage }) {
  const icon =
    stage?.status === 'succeeded' ? (
      <Check size={13} strokeWidth={3} />
    ) : stage?.status === 'failed' ? (
      <X size={13} strokeWidth={3} />
    ) : stage?.status === 'running' ? (
      <Loader2 size={13} className="spin" />
    ) : null
  return <span className="stage-icon">{icon}</span>
}
