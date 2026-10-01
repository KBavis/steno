import { useCallback, useState } from 'react'
import { api, type Job } from '../api/client'
import { usePoll } from '../hooks/usePoll'

export function Jobs({ jobs, error }: { jobs?: Job[]; error?: string }) {
  const [selected, setSelected] = useState<number>()

  return (
    <section className="panel">
      <h2>Ingestion jobs</h2>
      {error && <p className="error">{error}</p>}
      {jobs?.length === 0 && <p className="muted">No jobs yet.</p>}
      <table>
        <tbody>
          {jobs?.map((j) => (
            <tr key={j.id} className={selected === j.id ? 'selected' : ''} onClick={() => setSelected(j.id)}>
              <td className="mono">#{j.id}</td>
              <td>{j.mode}</td>
              <td>
                <span className={`badge badge-${j.status}`}>{j.status}</span>
              </td>
              <td className="muted">{new Date(j.queued_at).toLocaleString()}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {selected !== undefined && <JobStages id={selected} />}
    </section>
  )
}

function JobStages({ id }: { id: number }) {
  const load = useCallback(() => api.job(id), [id])
  const { data } = usePoll(load)
  if (!data) return null

  return (
    <div className="stages">
      {data.error && <p className="error">{data.error}</p>}
      <ol className="pipeline">
        {data.stages.map((s) => (
          <li key={s.id} className={`stage stage-${s.status}`}>
            <span>{s.stage}</span>
            <span className="muted">{typeof s.metrics.seconds === 'number' ? `${s.metrics.seconds}s` : ''}</span>
          </li>
        ))}
      </ol>
    </div>
  )
}
