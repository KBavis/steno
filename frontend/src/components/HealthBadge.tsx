import { api } from '../api/client'
import { usePoll } from '../hooks/usePoll'

export function HealthBadge() {
  const { data, error } = usePoll(api.health, 5000)

  if (error) return <span className="badge badge-failed">API unreachable</span>
  if (!data) return <span className="badge">checking…</span>

  return (
    <span className="health">
      {Object.entries(data.checks).map(([name, value]) => (
        <span key={name} className={`badge ${value === 'ok' ? 'badge-succeeded' : 'badge-failed'}`} title={value}>
          {name}
        </span>
      ))}
      <span className="muted">v{data.version}</span>
    </span>
  )
}
