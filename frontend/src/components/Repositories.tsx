import { useState } from 'react'
import { api } from '../api/client'
import { usePoll } from '../hooks/usePoll'

export function Repositories({ onQueued }: { onQueued: () => void }) {
  const repos = usePoll(api.repositories, 10000)
  const spaces = usePoll(api.spaces, 10000)
  const [pending, setPending] = useState<number>()
  const [error, setError] = useState<string>()

  const spaceName = (id: number | null) => spaces.data?.find((s) => s.id === id)?.name ?? '—'

  async function dryRun(repositoryId: number) {
    setPending(repositoryId)
    setError(undefined)
    try {
      await api.createJob(repositoryId, 'dry_run')
      onQueued()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setPending(undefined)
    }
  }

  return (
    <section className="panel">
      <h2>Repositories</h2>
      {repos.error && <p className="error">{repos.error}</p>}
      {error && <p className="error">{error}</p>}
      {repos.data?.length === 0 && (
        <p className="muted">
          None yet. Declare them in <code>config/steno.yaml</code> and run <code>make load-config</code>.
        </p>
      )}
      <table>
        <tbody>
          {repos.data?.map((r) => (
            <tr key={r.id}>
              <td>
                <strong>{r.name}</strong>
                <div className="muted">{spaceName(r.space_id)} · {r.default_branch}</div>
              </td>
              <td className="mono muted">{r.last_ingested_sha?.slice(0, 8) ?? 'never ingested'}</td>
              <td className="right">
                <button onClick={() => dryRun(r.id)} disabled={pending === r.id}>
                  Dry run
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  )
}
