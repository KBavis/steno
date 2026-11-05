import { AppWindow, ArrowUpRight, ChevronRight, Layers, ScanSearch } from 'lucide-react'
import { Fragment, useCallback, useEffect, useMemo, useState } from 'react'
import { api, type CoverageGroup, type CoverageItem, type CoverageRow, type CoverageSignal, type CoverageStatus } from '../api/client'
import { EmptyState, ErrorAlert, PageHeader } from '../components/ui'
import { usePoll } from '../hooks/usePoll'
import { timeAgo } from '../lib/time'

/** What each signal means, and what usually fixes it (the catalog: docs/how-it-works/ingestion/what-steno-must-see.md) */
const SIGNALS: Record<CoverageSignal, { label: string; help: string }> = {
  one_sided: {
    label: 'One end missing',
    help: 'A topic produced but never consumed, consumed but never produced, or an endpoint called that no application exposes. The other end was missed, or is outside the organization (mark it Expected).',
  },
  unknown_host: {
    label: 'Unknown hosts',
    help: 'HTTP calls whose host is only known where the app runs (config, a database row). Each is an "Unknown host" node for the calling class; the URL template and its config keys are kept.',
  },
  disagreement: {
    label: 'Disagreements',
    help: 'The code and another witness disagree: a use no ACL grants (code only), or a grant no code shows (declared only).',
  },
  unmatched_name: {
    label: 'Unmatched names',
    help: 'A name another witness uses (an ACL user, a manifest component) that matches no application: not ingested, or named differently.',
  },
  silent_repository: {
    label: 'Silent repositories',
    help: 'A repository that produced nothing: no applications, no claims, no library modules. Usually a witness no pack reads (config, deploy, ACLs), or a library not linked yet. Mark it Ignore if it really has nothing to say.',
  },
  silent_witness: {
    label: 'Silent witnesses',
    help: 'A PLATFORM or DECLARED pack ran on this repository and stated nothing: its files changed shape, or its rules no longer match them.',
  },
  internal_library: {
    label: 'Internal libraries',
    help: "Calls into the organization's own library that Steno hasn't read (STENO_INTERNAL_PREFIXES, or the repository's own package roots). A wrapper can hide a call to another application: ingest the library's repository, or write a rule for it.",
  },
  external_library: {
    label: 'External libraries',
    help: 'Calls into a public or vendor library that no pack covers and no list declares as I/O or harmless. Mark it Ignore if it never reaches the surface; otherwise a core-pack rule.',
  },
  library: {
    label: 'Uncovered I/O libraries',
    help: 'I/O libraries the code imports that no rule pack covers. A pack (or an org rule) for it would explain its calls.',
  },
  unknown_hook: {
    label: 'Unknown hooks',
    help: 'Code a framework runs without the code calling it (a listener, a filter, an annotation) that no rule explains. Hooks that can start work are listed first.',
  },
  inferred_entry: {
    label: 'Unknown triggers',
    help: 'Work inside an application that nothing in the code starts (a worker loop, a CLI command). Steno still gives it a flow, marked inferred; a rule that names its trigger makes the flow certain.',
  },
  no_trigger_application: {
    label: 'No known trigger',
    help: "An application none of whose flows has a known trigger, so its kind is unknown. A rule for whatever starts it gives it one.",
  },
  unreachable_effect: {
    label: 'Unreachable effects',
    help: "Code that reads, writes, or calls out, but that no entry point reaches: usually a missing entry point (a consumer, a schedule).",
  },
  external_call: {
    label: 'Unexplained I/O calls',
    help: 'Network, messaging, and database calls no rule explains. Usually a rule worth writing.',
  },
  config_unset: {
    label: 'Config set elsewhere',
    help: 'Config keys a fact needs (a topic, a host, a datasource) that no file in the repository sets: set where the app runs.',
  },
  unread_config: {
    label: 'Connection settings unread',
    help: 'Config keys that name a connection (a URL, a host, a topic, a binding) that no rule read.',
  },
  dropped_match: {
    label: "Couldn't check",
    help: "Matches of a rule that states a surface fact (a table, a topic, an endpoint) that Steno couldn't complete.",
  },
  file_call: {
    label: 'Unexplained file I/O',
    help: 'Local file reads and writes no rule explains: model artifacts, uploads, exports. Ranked below the network.',
  },
  version_untested: {
    label: 'Untested versions',
    help: 'A pack ran on a library version outside the range it is tested on. Rule health, not a gap.',
  },
  version_unknown: {
    label: 'Unknown versions',
    help: "No build file states the version of a library a pack is tested against. Rule health, not a gap.",
  },
  unread_file: {
    label: 'Unread files',
    help: 'Files no rule read, grouped by extension and place: input for a rule author, hidden from the report.',
  },
}

const STATUS_BADGE: Record<CoverageStatus, string> = {
  unexplained: 'badge badge-warn',
  ignored: 'badge',
  explained: 'badge badge-ok',
  expected: 'badge',
}
const STATUS_LABEL: Record<CoverageStatus, string> = {
  unexplained: 'open',
  ignored: 'ignored',
  explained: 'explained',
  expected: 'expected',
}

type StatusFilter = 'open' | 'ignored' | 'expected' | 'explained' | 'all'
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

  const [hidden, setHidden] = useState(false)
  const load = useCallback(() => api.coverage.report(scope, hidden), [scope, hidden])
  const { data, error, reload } = usePoll(load, 10000)
  const [group, setGroup] = useState<CoverageGroup | 'all'>('all')
  const [status, setStatus] = useState<StatusFilter>('open')
  const [open, setOpen] = useState<string>()
  const [busy, setBusy] = useState<string>()
  const [actionError, setActionError] = useState<string>()
  const [sort, setSort] = useState<{ key: SortKey; asc: boolean }>({ key: 'io_explained_pct', asc: true })
  const [unfolded, setUnfolded] = useState<Set<string>>(new Set())
  const toggleRow = (scope: string) =>
    setUnfolded((cur) => {
      const next = new Set(cur)
      if (next.has(scope)) next.delete(scope)
      else next.add(scope)
      return next
    })

  const items = useMemo(() => data?.items ?? [], [data])
  const matchesStatus = useCallback(
    (i: CoverageItem) => status === 'all' || (status === 'open' ? i.status === 'unexplained' : i.status === status),
    [status],
  )
  const counts = useMemo(() => {
    const c: Record<string, number> = {}
    for (const i of items) if (matchesStatus(i)) c[i.group] = (c[i.group] ?? 0) + 1
    return c
  }, [items, matchesStatus])
  const groups = (data?.groups ?? []).filter((g) => hidden || (g.key !== 'hidden' && g.key !== 'rule_health'))
  const shown = items.filter((i) => matchesStatus(i) && (group === 'all' || i.group === group))
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
                <span className="muted small">worst first · click a space to see its applications</span>
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
                    {rows.map((r) => {
                      const apps = r.application_rows ?? []
                      const isOpen = unfolded.has(r.scope)
                      return (
                        <Fragment key={r.scope}>
                          <tr
                            className="clickable"
                            aria-expanded={r.kind === 'space' ? isOpen : undefined}
                            onClick={() => (r.kind === 'space' && apps.length ? toggleRow(r.scope) : goScope(r.scope))}
                          >
                            <td>
                              <span className="coverage-row-name">
                                {r.kind === 'space' ? (
                                  <ChevronRight size={14} className="coverage-caret" style={{ transform: isOpen ? 'rotate(90deg)' : undefined }} />
                                ) : null}
                                {r.kind === 'space' ? <Layers size={14} /> : <AppWindow size={14} />}
                                {r.label}
                              </span>
                            </td>
                            <td><Pct value={r.io_explained_pct} /></td>
                            <td><Pct value={r.reachable_pct} /></td>
                            <td className="num">{r.open_items}</td>
                            <td className="num">{r.applications}</td>
                          </tr>
                          {isOpen &&
                            apps.map((a) => (
                              <tr key={a.scope} className="clickable coverage-subrow" onClick={() => goScope(a.scope)}>
                                <td>
                                  <span className="coverage-row-name">
                                    <AppWindow size={14} />
                                    {a.label}
                                  </span>
                                </td>
                                <td><Pct value={a.io_explained_pct} /></td>
                                <td><Pct value={a.reachable_pct} /></td>
                                <td className="num">{a.open_items}</td>
                                <td className="num muted">—</td>
                              </tr>
                            ))}
                        </Fragment>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            </>
          )}

          <div className="section-title">
            <h2>What Steno might be missing</h2>
            <span className="muted small">grouped by what was missed, connections between applications first; ranked by reach within each group</span>
          </div>
          <div className="coverage-filters">
            <div className="coverage-chips" role="tablist" aria-label="What was missed">
              <button role="tab" aria-selected={group === 'all'} className={group === 'all' ? 'on' : ''} onClick={() => setGroup('all')}>
                All <span>{Object.values(counts).reduce((a, b) => a + b, 0)}</span>
              </button>
              {groups.map((g) => (
                <button key={g.key} role="tab" aria-selected={group === g.key} className={group === g.key ? 'on' : ''} onClick={() => setGroup(g.key)}>
                  {g.label} <span>{counts[g.key] ?? 0}</span>
                </button>
              ))}
            </div>
            <select className="coverage-status" value={status} onChange={(e) => setStatus(e.target.value as StatusFilter)} aria-label="Status">
              <option value="open">Open</option>
              <option value="ignored">Ignored</option>
              <option value="expected">Expected</option>
              <option value="explained">Explained</option>
              <option value="all">All statuses</option>
            </select>
            <label className="muted small" title="Unread files and rule health: input for rule authors, left out of the report">
              <input type="checkbox" checked={hidden} onChange={(e) => setHidden(e.target.checked)} /> Show hidden
            </label>
          </div>

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
                  const places = `${i.occurrences} ${i.occurrences === 1 ? 'place' : 'places'}`
                  const reach = isApp
                    ? places
                    : i.applications.length === 0
                      ? `outside any application (scripts, tooling) · ${places}`
                      : `${i.applications.length} ${i.applications.length === 1 ? 'application' : 'applications'} · ${i.spaces} ${i.spaces === 1 ? 'space' : 'spaces'} · ${i.occurrences} ${i.occurrences === 1 ? 'place' : 'places'}`
                  return (
                    <li key={key}>
                      <div className={`list-row clickable ${expanded ? 'selected' : ''}`} onClick={() => setOpen(expanded ? undefined : key)}>
                        <ChevronRight size={16} className="muted" style={{ transform: expanded ? 'rotate(90deg)' : undefined, transition: 'transform .15s' }} />
                        <span className={`coverage-signal sig-${i.signal}`} title={SIGNALS[i.signal]?.help}>
                          {SIGNALS[i.signal]?.label ?? i.signal}
                        </span>
                        <div className="list-main">
                          <div className="list-title mono coverage-label" title={i.target}>{i.label}</div>
                          <div className="list-sub">{reach}</div>
                        </div>
                        {i.pull_request && (
                          <a
                            className="badge badge-ok"
                            href={i.pull_request.url ?? undefined}
                            target="_blank"
                            rel="noreferrer"
                            onClick={(e) => e.stopPropagation()}
                            title="A rule for this is in a pull request. It goes away after the rule merges and this repository is ingested again."
                          >
                            PR open <ArrowUpRight size={11} />
                          </a>
                        )}
                        <span className={STATUS_BADGE[i.status]}>{STATUS_LABEL[i.status]}</span>
                        <div className="list-actions" onClick={(e) => e.stopPropagation()}>
                          {i.status === 'unexplained' ? (
                            <>
                              <button className="btn btn-ghost btn-sm" disabled={busy === key} onClick={() => triage(i, 'ignored')} title="Harmless: never reaches the surface">
                                Ignore
                              </button>
                              <button className="btn btn-secondary btn-sm" disabled={busy === key} onClick={() => triage(i, 'expected')} title="A real gap, legitimately outside the organization">
                                Expected
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
