import { api } from '../api/client'
import { usePoll } from '../hooks/usePoll'

/** Connection status for the sidebar footer: the API, Postgres, and Neo4j. */
export function HealthStatus() {
  const { data, error } = usePoll(api.health, 5000)
  const rows: [string, boolean | undefined][] = error
    ? [['API', false]]
    : [
        ['Postgres', data && data.checks.postgres === 'ok'],
        ['Neo4j', data && data.checks.neo4j === 'ok'],
      ]

  return (
    <>
      {rows.map(([name, ok]) => (
        <div key={name} className="health-row" title={error ?? data?.checks[name.toLowerCase()]}>
          <span>{name}</span>
          <span className={`status-dot ${ok === undefined ? '' : ok ? 'ok' : 'fail'}`} />
        </div>
      ))}
      {data && <span>Steno v{data.version}</span>}
    </>
  )
}
