import { Puzzle, Sparkles } from 'lucide-react'
import { useCallback, useMemo, useState } from 'react'
import { api, type Pattern, type PatternState } from '../api/client'
import { EmptyState, ErrorAlert, Notice, PageHeader } from '../components/ui'
import { useAction } from '../hooks/useAction'
import { usePoll } from '../hooks/usePoll'

/** How each pattern signal reads (coverage-report.md §1, §5) */
const SIGNAL: Record<string, string> = {
  unknown_hook: 'Framework hook',
  external_call: 'I/O call',
  inferred_entry: 'No known trigger',
  unreachable_effect: 'Unreachable code',
  library: 'Library with no pack',
  unread_file: 'Unread files',
  unread_config: 'Unread config',
  unknown_host: 'Unknown host',
  config_unset: 'Config set elsewhere',
  dropped_match: "Couldn't check",
  unparsed: 'Unparsed',
  version_untested: 'Untested version',
  version_unknown: 'Unknown version',
}
const EVIDENCE: Record<string, string> = {
  referenced_by_code: 'the code names it',
  names_known_things: 'names things in the graph',
  in_internal_library: 'in an internal library',
}
const STATES: { value: PatternState; label: string }[] = [
  { value: 'open', label: 'Open' },
  { value: 'proposed', label: 'Proposed' },
  { value: 'needs_person', label: 'Needs a person' },
  { value: 'blocked_vocabulary', label: 'Needs vocabulary' },
  { value: 'ignored', label: 'Ignored' },
  { value: 'approved', label: 'Approved' },
  { value: 'rejected', label: 'Rejected' },
  { value: 'covered', label: 'Covered' },
]

/**
 * The organization's patterns (D121–D123): what no rule explains, grouped by the library or
 * package that owns it, ranked by how much one rule would explain. Curation starts here.
 */
export function PatternsPage({ onNavigate }: { onNavigate: (tab: string) => void }) {
  const [state, setState] = useState<PatternState>('open')
  const load = useCallback(() => api.patterns.list(state, 200), [state])
  const { data, error, reload } = usePoll(load, 10000)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [limit, setLimit] = useState(3)
  const [notice, setNotice] = useState<string>()
  const { run, busy, error: actionError } = useAction()

  const toggle = (fp: string) =>
    setSelected((cur) => {
      const next = new Set(cur)
      if (next.has(fp)) next.delete(fp)
      else next.add(fp)
      return next
    })
  const groups = useMemo(() => data?.frameworks ?? [], [data])
  const total = useMemo(() => groups.reduce((n, g) => n + g.patterns.length, 0), [groups])

  const curate = (fingerprints: string[]) =>
    run(async () => {
      const r = await api.curation.start(fingerprints.length || limit, fingerprints)
      setSelected(new Set())
      setNotice(`Curation run #${r.id} queued: a worker drafts the proposals`)
      onNavigate('proposals')
    })
  const triage = (fingerprints: string[], next: 'ignored' | 'open') =>
    run(async () => {
      await api.patterns.triage(fingerprints, next, next === 'ignored' ? 'ignored in the UI' : 'reopened in the UI')
      setSelected(new Set())
      reload()
    })

  return (
    <>
      <PageHeader
        title="Patterns"
        description="What no rule explains, across every repository, grouped by the library or package that owns it. Ranked by how much one rule would explain: repositories first, raised by evidence that it matters. Curation drafts rules for these; nothing reaches the graph until you approve."
        action={
          state === 'open' && (
            <div className="curation-actions">
              {selected.size > 0 ? (
                <>
                  <button className="btn btn-secondary" disabled={busy} onClick={() => triage([...selected], 'ignored')}>
                    Ignore {selected.size}
                  </button>
                  <button className="btn btn-primary" disabled={busy} onClick={() => curate([...selected])}>
                    <Sparkles size={15} /> Curate {selected.size} selected
                  </button>
                </>
              ) : (
                <>
                  <label className="muted small">
                    Top{' '}
                    <select value={limit} onChange={(e) => setLimit(Number(e.target.value))}>
                      {[1, 3, 5, 10].map((n) => (
                        <option key={n}>{n}</option>
                      ))}
                    </select>
                  </label>
                  <button className="btn btn-primary" disabled={busy || total === 0} onClick={() => curate([])}>
                    <Sparkles size={15} /> Curate top {limit}
                  </button>
                </>
              )}
            </div>
          )
        }
      />
      <ErrorAlert message={error ?? actionError} />
      <Notice message={notice} onDone={() => setNotice(undefined)} />

      <div className="segmented" role="tablist">
        {STATES.map((s) => (
          <button key={s.value} className={state === s.value ? 'active' : ''} onClick={() => setState(s.value)}>
            {s.label}
          </button>
        ))}
      </div>

      {data && total === 0 && (
        <EmptyState icon={Puzzle} title={state === 'open' ? 'Nothing unexplained' : 'None here'}>
          {state === 'open'
            ? 'Patterns come from the coverage report at the end of each ingestion. Ingest a repository to see what no rule explains.'
            : 'No pattern is in this state.'}
        </EmptyState>
      )}

      {groups.map((g) => (
        <div className="card curation-group" key={g.owner ?? g.patterns[0].fingerprint}>
          <div className="curation-group-head">
            <div>
              <div className="list-title mono">{g.owner ?? 'The application’s own code'}</div>
              <div className="list-sub">
                {g.patterns.length} pattern{g.patterns.length === 1 ? '' : 's'} · score {Math.round(g.score)}
              </div>
            </div>
            {state === 'open' && g.patterns.length > 1 && (
              <button className="btn btn-ghost btn-sm" onClick={() => setSelected(new Set(g.patterns.map((p) => p.fingerprint)))}>
                Select all
              </button>
            )}
          </div>
          {g.patterns.map((p) => (
            <PatternRow
              key={p.fingerprint}
              p={p}
              selectable={state === 'open'}
              checked={selected.has(p.fingerprint)}
              onToggle={() => toggle(p.fingerprint)}
              onReopen={state === 'ignored' ? () => triage([p.fingerprint], 'open') : undefined}
            />
          ))}
        </div>
      ))}
      {data && data.more > 0 && <p className="muted small">…and {data.more} more groups</p>}
    </>
  )
}

function PatternRow({
  p,
  selectable,
  checked,
  onToggle,
  onReopen,
}: {
  p: Pattern
  selectable: boolean
  checked: boolean
  onToggle: () => void
  onReopen?: () => void
}) {
  return (
    <div className="list-row curation-row">
      {selectable && <input type="checkbox" checked={checked} onChange={onToggle} aria-label={`Select ${p.label}`} />}
      <div className="list-main">
        <div className="list-title">{p.label}</div>
        <div className="list-sub">
          <span className="badge">{SIGNAL[p.signal] ?? p.signal}</span> {p.repositories} repo{p.repositories === 1 ? '' : 's'} · {p.applications} app
          {p.applications === 1 ? '' : 's'} · {p.occurrences} place{p.occurrences === 1 ? '' : 's'} · score {Math.round(p.score)}
          {(p.evidence ?? []).map((e) => (
            <span key={e} className="badge badge-accent">
              {EVIDENCE[e] ?? e}
            </span>
          ))}
        </div>
        {p.note && <div className="list-sub muted">{p.note}</div>}
        <div className="list-sub mono muted">{p.fingerprint}</div>
      </div>
      {onReopen && (
        <button className="btn btn-ghost btn-sm" onClick={onReopen}>
          Reopen
        </button>
      )}
    </div>
  )
}
