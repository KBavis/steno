import { CheckCircle2, CircleX, FileCode2, Loader2, Sparkles } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { api, type Proposal, type ProposalKind, type ProposalStatus } from '../api/client'
import { Drawer, EmptyState, ErrorAlert, Notice, PageHeader } from '../components/ui'
import { useAction } from '../hooks/useAction'
import { usePoll } from '../hooks/usePoll'
import { timeAgo } from '../lib/time'

const KIND: Record<ProposalKind, string> = {
  rule: 'New rule',
  enable_pack: 'Enable a pack',
  ignore: 'Ignore as noise',
  vocabulary: 'Needs vocabulary',
}
const STATUS_BADGE: Record<ProposalStatus, string> = {
  drafting: 'badge',
  ready: 'badge badge-accent',
  approved: 'badge badge-ok',
  rejected: 'badge badge-fail',
  superseded: 'badge',
  needs_person: 'badge badge-warn',
  failed: 'badge badge-fail',
}
const FILTERS: { value: ProposalStatus | 'all'; label: string }[] = [
  { value: 'ready', label: 'Ready for review' },
  { value: 'needs_person', label: 'Needs a person' },
  { value: 'approved', label: 'Approved' },
  { value: 'rejected', label: 'Rejected' },
  { value: 'failed', label: 'Failed' },
  { value: 'all', label: 'All' },
]
const APPROVE_HELP: Record<ProposalKind, string> = {
  rule: 'Writes the rule and its test cases into the pack below; the pack’s tests must pass, or nothing is kept. The next ingestion uses it.',
  enable_pack: 'Records the decision; turning the pack on (or widening its versions) is still yours to do.',
  ignore: 'Ignores the pattern everywhere it occurs; later runs keep it ignored.',
  vocabulary: 'Accepts the gap into the backlog; the pattern stays blocked until the vocabulary is built.',
}

/** Rule curation (D120): runs, and the proposals they produce, reviewed one by one. */
export function ProposalsPage() {
  const [filter, setFilter] = useState<ProposalStatus | 'all'>('ready')
  const loadInfo = useCallback(() => api.curation.info(), [])
  const { data: info, error: infoError } = usePoll(loadInfo, 3000)
  const loadList = useCallback(() => api.curation.proposals(filter === 'all' ? undefined : filter), [filter])
  const { data: proposals, error, reload } = usePoll(loadList, 5000)
  const [open, setOpen] = useState<number>()
  const [notice, setNotice] = useState<string>()

  const active = info?.runs.filter((r) => r.status === 'queued' || r.status === 'running') ?? []

  return (
    <>
      <PageHeader
        title="Proposals"
        description={
          info
            ? `Rules Steno drafted for patterns no rule explains. A rule is proposed only after its own test cases pass. Drafter: ${info.drafter} (${info.model}, ${info.effort} effort), at most ${info.max_turns} turns and $${info.max_usd_per_pattern} per pattern, $${info.budget_usd} per run.`
            : undefined
        }
      />
      <ErrorAlert message={error ?? infoError} />
      <Notice message={notice} onDone={() => setNotice(undefined)} />

      {info && info.runs.length > 0 && (
        <div className="card">
          <div className="card-body">
            <div className="section-title" style={{ marginTop: 0 }}>
              <h2>Curation runs</h2>
            </div>
            {info.runs.slice(0, 5).map((r) => (
              <div className="list-row" key={r.id}>
                <div className="list-main">
                  <div className="list-title">
                    Run #{r.id}{' '}
                    <span className={r.status === 'failed' ? 'badge badge-fail' : r.status === 'succeeded' ? 'badge badge-ok' : 'badge badge-accent'}>
                      {(r.status === 'running' || r.status === 'queued') && <Loader2 size={12} className="spin" />} {r.status}
                    </span>
                  </div>
                  <div className="list-sub">
                    ${r.spent_usd.toFixed(2)} of ${r.budget_usd ?? '?'} · {timeAgo(r.started_at)} · {runCounts(r.stats)}
                  </div>
                  {r.error && <div className="list-sub error-text">{r.error}</div>}
                  {r.status === 'queued' && (
                    <div className="list-sub muted">Waiting for a worker: start one with make worker (or steno worker).</div>
                  )}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
      {active.length > 0 && <p className="muted small">Proposals appear here as the run drafts them.</p>}

      <div className="segmented" role="tablist">
        {FILTERS.map((f) => (
          <button key={f.value} className={filter === f.value ? 'active' : ''} onClick={() => setFilter(f.value)}>
            {f.label}
          </button>
        ))}
      </div>

      {proposals && proposals.length === 0 && (
        <EmptyState icon={Sparkles} title="No proposals here">
          Start a curation run from the Patterns page.
        </EmptyState>
      )}
      <div className="card">
        {proposals?.map((p) => (
          <button className="list-row curation-proposal" key={p.id} onClick={() => setOpen(p.id)}>
            <div className="list-main">
              <div className="list-title">
                #{p.id} {p.title}
              </div>
              <div className="list-sub">
                <span className="badge">{KIND[p.kind]}</span> <span className={STATUS_BADGE[p.status]}>{p.status.replace('_', ' ')}</span>
                {p.kind === 'rule' && (
                  <span className={p.cases_passed === p.cases && p.cases > 0 ? 'badge badge-ok' : 'badge badge-warn'}>
                    {p.cases_passed}/{p.cases} cases pass
                  </span>
                )}
                {p.facet && <span className="badge">{p.facet}</span>} ${p.cost_usd.toFixed(2)} · {p.turns} turns
              </div>
              <div className="list-sub muted">{p.summary}</div>
            </div>
          </button>
        ))}
      </div>

      {open !== undefined && (
        <ProposalDrawer
          id={open}
          onClose={() => setOpen(undefined)}
          onDone={(message) => {
            setOpen(undefined)
            setNotice(message)
            reload()
          }}
        />
      )}
    </>
  )
}

function ProposalDrawer({ id, onClose, onDone }: { id: number; onClose: () => void; onDone: (message: string) => void }) {
  const [p, setP] = useState<Proposal>()
  const [loadError, setLoadError] = useState<string>()
  const [rejecting, setRejecting] = useState(false)
  const [why, setWhy] = useState('')
  const [commit, setCommit] = useState(false)
  const { run, busy, error } = useAction()

  useEffect(() => {
    api.curation.proposal(id).then(setP, (e: Error) => setLoadError(e.message))
  }, [id])

  const reviewable = p && (p.status === 'ready' || p.status === 'needs_person')
  const approve = () =>
    run(async () => {
      const out = await api.curation.approve(id, commit)
      onDone(`#${id} approved` + (out.rule_file ? `: ${String(out.rule_file)}` : '') + (out.commit ? `, committed ${String(out.commit).slice(0, 8)}` : ''))
    })
  const reject = () =>
    run(async () => {
      await api.curation.reject(id, why)
      onDone(`#${id} rejected`)
    })

  return (
    <Drawer
      title={p ? `#${p.id} ${p.title}` : `Proposal #${id}`}
      description={p ? `${KIND[p.kind]} · ${p.status.replace('_', ' ')} · ${p.drafter ?? ''} · $${p.cost_usd.toFixed(2)}, ${p.turns} turns` : undefined}
      onClose={onClose}
      footer={
        reviewable ? (
          rejecting ? (
            <>
              <input className="curation-why" autoFocus placeholder="Why? (kept with the pattern)" value={why} onChange={(e) => setWhy(e.target.value)} />
              <button className="btn btn-ghost" onClick={() => setRejecting(false)}>
                Back
              </button>
              <button className="btn btn-danger" disabled={busy || !why.trim()} onClick={reject}>
                <CircleX size={15} /> Reject
              </button>
            </>
          ) : (
            <>
              {p?.kind === 'rule' && (
                <label className="small muted">
                  <input type="checkbox" checked={commit} onChange={(e) => setCommit(e.target.checked)} /> Commit as Steno
                </label>
              )}
              <button className="btn btn-secondary" onClick={() => setRejecting(true)}>
                Reject…
              </button>
              {p?.status === 'ready' && (
                <button className="btn btn-primary" disabled={busy} onClick={approve}>
                  <CheckCircle2 size={15} /> Approve
                </button>
              )}
            </>
          )
        ) : (
          <button className="btn btn-secondary" onClick={onClose}>
            Close
          </button>
        )
      }
    >
      <ErrorAlert message={loadError ?? error} />
      {p && (
        <div className="curation-detail">
          <p>{p.summary}</p>
          {reviewable && p.status === 'ready' && <p className="field-hint">{APPROVE_HELP[p.kind]}</p>}
          {p.comment && <p className="error-text">Rejected by {p.reviewed_by}: {p.comment}</p>}
          {p.reviewed_by && !p.comment && (
            <p className="muted small">
              Approved by {p.reviewed_by} {p.reviewed_at ? timeAgo(p.reviewed_at) : ''}
              {p.commit_sha ? ` · commit ${p.commit_sha.slice(0, 8)}` : ''}
            </p>
          )}

          <h3>Patterns</h3>
          {p.fingerprints.map((f) => (
            <div key={f} className="mono small">
              {f}
            </div>
          ))}

          {p.pack && (
            <>
              <h3>Lands in</h3>
              <div className="small">
                <span className="mono">{String(p.pack.name)}</span> · {String(p.pack.source)} pack ({String(p.pack.language)}) · enabled when{' '}
                <span className="mono">{JSON.stringify(p.pack.enabled_when)}</span>
              </div>
            </>
          )}

          {p.rule && (
            <>
              <h3>
                <FileCode2 size={14} /> The rule
              </h3>
              <pre className="curation-code">{p.rule}</pre>
            </>
          )}

          {Object.keys(p.tests ?? {}).length > 0 && (
            <>
              <h3>Test cases</h3>
              {Object.entries(p.tests).map(([name, c]) => {
                const result = p.proof?.cases?.find((r) => r.case === name)
                return (
                  <div key={name} className="curation-case">
                    <div className="list-title">
                      {name}{' '}
                      {result && <span className={result.passed ? 'badge badge-ok' : 'badge badge-fail'}>{result.passed ? 'passes' : 'fails'}</span>}
                    </div>
                    {Object.entries(c.files ?? {}).map(([path, text]) => (
                      <div key={path}>
                        <div className="mono small muted">{path}</div>
                        <pre className="curation-code">{text}</pre>
                      </div>
                    ))}
                    <div className="small muted">Expected facts</div>
                    <pre className="curation-code">{JSON.stringify(c.expected ?? {}, null, 1)}</pre>
                  </div>
                )
              })}
            </>
          )}

          {p.samples.length > 0 && (
            <>
              <h3>Where it occurs (samples)</h3>
              {p.samples.map((s, i) => (
                <div key={i} className="mono small">
                  {s.repository}:{s.file}
                  {s.line ? `:${s.line}` : ''} {s.function ? `· ${s.function}` : ''}
                </div>
              ))}
            </>
          )}
          {p.flags.length > 0 && (
            <>
              <h3>Flags</h3>
              {p.flags.map((f) => (
                <div key={f} className="small">
                  {f}
                </div>
              ))}
            </>
          )}
        </div>
      )}
    </Drawer>
  )
}

function runCounts(stats: Record<string, unknown>): string {
  const parts = Object.entries(stats)
    .filter(([k]) => k !== 'params')
    .map(([k, v]) => `${String(v)} ${k.replace(':', ' ').replace('_', ' ')}`)
  return parts.length ? parts.join(', ') : 'no proposals yet'
}
