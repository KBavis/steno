import { AppWindow, ArrowUpRight, ChevronRight, Layers, ScanSearch } from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { api, type CoverageItem, type CoverageRow, type CoverageSignal, type CoverageStatus } from '../api/client'
import { EmptyState, ErrorAlert, PageHeader } from '../components/ui'
import { usePoll } from '../hooks/usePoll'
import { timeAgo } from '../lib/time'

/** What each signal means, and what usually fixes it */
const SIGNALS: Record<CoverageSignal, { label: string; help: string }> = {
  unknown_host: {
    label: 'Unknown hosts',
    help: "HTTP calls whose host is only known at runtime (read from config or a database row). The graph shows each as an \"Unknown host\" node for the class making the call; the URL template is kept on every call.",
  },
  external_call: {
    label: 'Unexplained calls',
    help: 'Calls into a library that look like I/O, in functions where no rule recorded what they read, write, or call. Usually a rule worth writing.',
  },
  library: {
    label: 'Uncovered libraries',
    help: 'I/O libraries the code imports that no rule pack covers. A pack (or an org rule) for it would explain its calls.',
  },
  unreachable_effect: {
    label: 'Unreachable code',
    help: "Code that reads, writes, or calls out, but that no entry point reaches: dead code, a missing entry point (a consumer, a schedule), or a call the resolver can't follow.",
  },
  dropped_match: {
    label: "Couldn't check",
    help: "Matches a rule found but couldn't complete because Steno couldn't tell: a condition it couldn't check, a name it couldn't resolve.",
  },
  unparsed: {
    label: 'Unparsed files',
    help: "Files Steno couldn't read at all, so nothing in them is in the graph.",
  },
}

const STATUS_BADGE: Record<CoverageStatus, string> = {
  unexplained: 'badge badge-warn',
  ignored: 'badge',
  explained: 'badge badge-ok',
}
const STATUS_LABEL: Record<CoverageStatus, string> = { unexplained: 'open', ignored: 'ignored', explained: 'explained' }

type StatusFilter = 'open' | 'ignored' | 'explained' | 'all'
type SortKey = 'label' | 'io_explained_pct' | 'reachable_pct' | 'open_items' | 'applications'

// The scope lives in the hash after the tab: #coverage, #coverage/space:3, #coverage/app:billing
function scopeFromHash(): string {
  const [, ...rest] = window.location.hash.slice(1).split('/')
  return rest.length ? decodeURIComponent(rest.join('/')) : 'org'
}
function goScope(scope: string) {
  window.location.hash = scope === 'org' ? 'coverage' : `coverage/${encodeURIComponent(scope)}`
}

export function CoveragePage() {
  const [scope, setScope] = useState(scopeFromHash)
  useEffect(() => {
    const onHash = () => setScope(scopeFromHash())
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])

  const load = useCallback(() => api.coverage.report(scope), [scope])
  const { data, error, reload } = usePoll(load, 10000)
  const [signal, setSignal] = useState<CoverageSignal | 'all'>('all')
  const [status, setStatus] = useState<StatusFilter>('open')
  const [open, setOpen] = useState<string>()
  const [busy, setBusy] = useState<string>()
  const [actionError, setActionError] = useState<string>()
  const [sort, setSort] = useState<{ key: SortKey; asc: boolean }>({ key: 'io_explained_pct', asc: true })

  const items = useMemo(() => data?.items ?? [], [data])
  const matchesStatus = useCallback(
    (i: CoverageItem) => status === 'all' || (status === 'open' ? i.status === 'unexplained' : i.status === status),
    [status],
  )
  const counts = useMemo(() => {
    const c: Record<string, number> = {}
    for (const i of items) if (matchesStatus(i)) c[i.signal] = (c[i.signal] ?? 0) + 1
    return c
  }, [items, matchesStatus])
  const shown = items.filter((i) => matchesStatus(i) && (signal === 'all' || i.signal === signal))
  const rows = useMemo(() => sortRows(data?.children ?? [], sort.key, sort.asc), [data, sort])

  const triage = async (i: CoverageItem, next: CoverageStatus) => {
    const key = `${i.kind}|${i.target}`
    setBusy(key)
    setActionError(undefined)
    try {
      await api.coverage.triage(i.kind, i.target, next)
      reload()
    } catch (e) {
      setActionError((e as Error).message)
    } finally {
      setBusy(undefined)
    }
  }

  const s = data?.summary
  const isApp = scope.startsWith('app:')
  const latest = data?.runs.reduce<string | null>((a, r) => (r.finished_at && (!a || r.finished_at > a) ? r.finished_at : a), null)

  return (
    <>
      <PageHeader
        title="Coverage"
        description="What no rule explained in each application's latest run. Start at the organization and drill into the spaces and applications that need the most work; items are ranked by how many applications one fix would help."
      />
      <ErrorAlert message={error ?? actionError} />

      {data && (
        <nav className="coverage-crumbs" aria-label="Scope">
          {data.breadcrumbs.map((c, i) => (
            <span key={c.scope}>
              {i > 0 && <ChevronRight size={13} className="muted" />}
              {i < data.breadcrumbs.length - 1 ? (
                <button className="link" onClick={() => goScope(c.scope)}>
                  {c.label}
                </button>
              ) : (
                <strong>{c.label}</strong>
              )}
            </span>
          ))}
        </nav>
      )}

      {data && s && s.applications === 0 && (
        <div className="card">
          <EmptyState icon={ScanSearch} title="No coverage yet">
            Run an ingestion and its coverage report appears here.
          </EmptyState>
        </div>
      )}

      {data && s && s.applications > 0 && (
        <>
          <div className="coverage-summary card">
            <Metric value={s.io_explained_pct} label="I/O calls explained" detail={`${s.io_calls_explained} of ${s.io_calls}`} />
            <Metric value={s.reachable_pct} label="functions reached" detail={`${s.functions_reachable} of ${s.functions}`} />
            <div className="coverage-metric">
              <strong>{s.open_items}</strong>
              <span>open items</span>
            </div>
            <div className="coverage-metric">
              <strong>{s.applications}</strong>
              <span>{s.applications === 1 ? 'application' : 'applications'}</span>
              {latest && <span className="muted small">latest run {timeAgo(latest)}</span>}
            </div>
          </div>

          {!isApp && rows.length > 0 && (
            <>
              <div className="section-title">
                <h2>{scope === 'org' ? 'Spaces' : 'In this space'}</h2>
                <span className="muted small">worst first · click a row to drill in</span>
              </div>
              <div className="card coverage-table-wrap">
                <table className="coverage-table">
                  <thead>
                    <tr>
                      <SortHead label="Name" k="label" sort={sort} onSort={setSort} />
                      <SortHead label="I/O explained" k="io_explained_pct" sort={sort} onSort={setSort} />
                      <SortHead label="Functions reached" k="reachable_pct" sort={sort} onSort={setSort} />
                      <SortHead label="Open items" k="open_items" sort={sort} onSort={setSort} />
                      <SortHead label="Applications" k="applications" sort={sort} onSort={setSort} />
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((r) => (
                      <tr key={r.scope} className="clickable" onClick={() => goScope(r.scope)}>
                        <td>
                          <span className="coverage-row-name">
                            {r.kind === 'space' ? <Layers size={14} /> : <AppWindow size={14} />}
                            {r.label}
                          </span>
                        </td>
                        <td><Pct value={r.io_explained_pct} /></td>
                        <td><Pct value={r.reachable_pct} /></td>
                        <td className="num">{r.open_items}</td>
                        <td className="num">{r.applications}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}

          <div className="section-title">
            <h2>{isApp ? 'Items' : 'Rule backlog'}</h2>
            {!isApp && <span className="muted small">ranked by reach: applications, then spaces, then occurrences</span>}
          </div>
          <div className="coverage-filters">
            <div className="coverage-chips" role="tablist" aria-label="Signal">
              <button role="tab" aria-selected={signal === 'all'} className={signal === 'all' ? 'on' : ''} onClick={() => setSignal('all')}>
                All <span>{Object.values(counts).reduce((a, b) => a + b, 0)}</span>
              </button>
              {(Object.keys(SIGNALS) as CoverageSignal[]).map((k) => (
                <button key={k} role="tab" aria-selected={signal === k} className={signal === k ? 'on' : ''} onClick={() => setSignal(k)}>
                  {SIGNALS[k].label} <span>{counts[k] ?? 0}</span>
                </button>
              ))}
            </div>
            <select className="coverage-status" value={status} onChange={(e) => setStatus(e.target.value as StatusFilter)} aria-label="Status">
              <option value="open">Open</option>
              <option value="ignored">Ignored</option>
              <option value="explained">Explained</option>
              <option value="all">All statuses</option>
            </select>
          </div>

          {signal !== 'all' && <p className="coverage-help">{SIGNALS[signal].help}</p>}

          <div className="card">
            {shown.length === 0 ? (
              <EmptyState icon={ScanSearch} title="Nothing here">
                {status === 'open' ? 'No open items for this filter.' : 'No items for this filter.'}
              </EmptyState>
            ) : (
              <ul className="list">
                {shown.map((i) => {
                  const key = `${i.kind}|${i.target}`
                  const expanded = open === key
                  const reach = isApp
                    ? `${i.occurrences} ${i.occurrences === 1 ? 'place' : 'places'}`
                    : `${i.applications.length} ${i.applications.length === 1 ? 'application' : 'applications'} · ${i.spaces} ${i.spaces === 1 ? 'space' : 'spaces'} · ${i.occurrences} ${i.occurrences === 1 ? 'place' : 'places'}`
                  return (
                    <li key={key}>
                      <div className={`list-row clickable ${expanded ? 'selected' : ''}`} onClick={() => setOpen(expanded ? undefined : key)}>
                        <ChevronRight size={16} className="muted" style={{ transform: expanded ? 'rotate(90deg)' : undefined, transition: 'transform .15s' }} />
                        <span className={`coverage-signal sig-${i.signal}`}>{SIGNALS[i.signal]?.label ?? i.signal}</span>
                        <div className="list-main">
                          <div className="list-title mono coverage-label" title={i.target}>{i.label}</div>
                          <div className="list-sub">{reach}</div>
                        </div>
                        <span className={STATUS_BADGE[i.status]}>{STATUS_LABEL[i.status]}</span>
                        <div className="list-actions" onClick={(e) => e.stopPropagation()}>
                          {i.status === 'unexplained' ? (
                            <>
                              <button className="btn btn-ghost btn-sm" disabled={busy === key} onClick={() => triage(i, 'ignored')} title="Not I/O, or not worth a rule">
                                Ignore
                              </button>
                              <button className="btn btn-secondary btn-sm" disabled={busy === key} onClick={() => triage(i, 'explained')} title="A rule now covers it">
                                Explained
                              </button>
                            </>
                          ) : (
                            <button className="btn btn-ghost btn-sm" disabled={busy === key} onClick={() => triage(i, 'unexplained')}>
                              Reopen
                            </button>
                          )}
                        </div>
                      </div>
                      {expanded && (
                        <div className="coverage-expanded">
                          {!isApp && (
                            <div className="coverage-apps">
                              {i.applications.map((a) => (
                                <button key={a.name} className="coverage-app" onClick={() => goScope(`app:${a.name}`)}>
                                  {a.name}
                                  {a.space ? <span className="muted"> · {a.space}</span> : null}
                                  <span className="muted"> · {a.occurrences}</span>
                                </button>
                              ))}
                            </div>
                          )}
                          <ul className="coverage-samples">
                            {i.samples.map((smp, n) => (
                              <li key={n}>
                                <span className="mono small">
                                  {smp.url ? (
                                    <a className="link" href={smp.url} target="_blank" rel="noreferrer">
                                      {smp.file}
                                      {smp.line ? `:${smp.line}` : ''} <ArrowUpRight size={11} />
                                    </a>
                                  ) : (
                                    <>
                                      {smp.file}
                                      {smp.line ? `:${smp.line}` : ''}
                                    </>
                                  )}
                                </span>
                                {smp.function && <span className="muted small mono">{smp.function.split('.').slice(-2).join('.')}</span>}
                                {smp.detail && <span className="coverage-detail mono small">{smp.detail}</span>}
                              </li>
                            ))}
                            {i.occurrences > i.samples.length && <li className="muted small">…and {i.occurrences - i.samples.length} more</li>}
                          </ul>
                        </div>
                      )}
                    </li>
                  )
                })}
              </ul>
            )}
          </div>
        </>
      )}
    </>
  )
}

function sortRows(rows: CoverageRow[], key: SortKey, asc: boolean): CoverageRow[] {
  const dir = asc ? 1 : -1
  return [...rows].sort((a, b) => {
    const x = a[key]
    const y = b[key]
    if (x === null || x === undefined) return 1
    if (y === null || y === undefined) return -1
    return (typeof x === 'string' ? x.localeCompare(String(y)) : (x as number) - (y as number)) * dir
  })
}

function SortHead({
  label,
  k,
  sort,
  onSort,
}: {
  label: string
  k: SortKey
  sort: { key: SortKey; asc: boolean }
  onSort: (s: { key: SortKey; asc: boolean }) => void
}) {
  const on = sort.key === k
  return (
    <th>
      <button className={`coverage-sort ${on ? 'on' : ''}`} onClick={() => onSort({ key: k, asc: on ? !sort.asc : k === 'label' || k.endsWith('pct') })}>
        {label}
        {on ? (sort.asc ? ' ↑' : ' ↓') : ''}
      </button>
    </th>
  )
}

function Pct({ value }: { value: number | null }) {
  if (value === null) return <span className="muted">—</span>
  const tone = value >= 90 ? 'good' : value >= 60 ? 'fair' : 'poor'
  return (
    <span className={`coverage-pct pct-${tone}`}>
      <span className="coverage-bar"><span style={{ width: `${Math.min(100, value)}%` }} /></span>
      {Math.round(value)}%
    </span>
  )
}

function Metric({ value, label, detail }: { value?: number | null; label: string; detail: string }) {
  return (
    <div className="coverage-metric" title={detail}>
      <strong>{value === null || value === undefined ? '—' : `${Math.round(value)}%`}</strong>
      <span>{label}</span>
      <span className="muted small">{detail}</span>
    </div>
  )
}
