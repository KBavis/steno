import { CheckCircle2, ChevronRight, ExternalLink, Loader2, Search, Sparkles } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { api, type CurationRun, type Proposal, type ProposalSummary } from '../api/client'
import { Drawer, EmptyState, ErrorAlert, Notice, PageHeader } from '../components/ui'
import { useAction } from '../hooks/useAction'
import { usePoll } from '../hooks/usePoll'

/**
 * Suggested rules (D120): Steno drafts rules for code its current rules don't explain, and a
 * person accepts or rejects each one. Nothing reaches the graph until it's approved.
 */
export function RulesPage() {
  const loadRuns = useCallback(() => api.curation.info(), [])
  const { data: info, error: runsError } = usePoll(loadRuns, 3000)
  const loadList = useCallback(() => api.curation.proposals(), [])
  const { data: proposals, error, reload } = usePoll(loadList, 4000)
  const [open, setOpen] = useState<number>()
  const [showDone, setShowDone] = useState(false)
  const [count, setCount] = useState(3)
  const [notice, setNotice] = useState<string>()
  const { run, busy, error: startError } = useAction()

  const latest = info?.runs[0]
  const working = latest && (latest.status === 'queued' || latest.status === 'running')
  const toReview = (proposals ?? []).filter((p) => p.status === 'ready' || p.status === 'needs_person')
  const done = (proposals ?? []).filter((p) => p.status === 'approved' || p.status === 'rejected')

  const start = () => run(() => api.curation.start(count))

  return (
    <>
      <PageHeader
        title="Suggested New Rules"
        description="Rules Steno drafted for code it can't explain yet. Each is tested before you see it; nothing changes until you approve."
        action={
          <div className="rules-start">
            <select value={count} onChange={(e) => setCount(Number(e.target.value))} disabled={busy || working} aria-label="How many">
              {[1, 3, 5, 10].map((n) => (
                <option key={n} value={n}>
                  {n} {n === 1 ? 'suggestion' : 'suggestions'}
                </option>
              ))}
            </select>
            <button className="btn btn-primary" onClick={start} disabled={busy || working}>
              <Search size={15} /> Find missing rules
            </button>
          </div>
        }
      />
      <ErrorAlert message={error ?? runsError ?? startError ?? (latest?.status === 'failed' ? `The last search stopped: ${latest.error}` : undefined)} />
      <Notice message={notice} onDone={() => setNotice(undefined)} />
      {working && latest && <Progress run={latest} />}

      <h2 className="rules-heading">To review {toReview.length > 0 && <span className="muted">{toReview.length}</span>}</h2>
      {proposals && toReview.length === 0 && !working && (
        <EmptyState icon={Sparkles} title="Nothing to review">
          Click “Find missing rules” to have Steno look at the code its rules don't explain yet. It works from the latest ingestion of each repository.
        </EmptyState>
      )}
      <div className="rules-list">
        {toReview.map((p) => (
          <Row key={p.id} p={p} onOpen={() => setOpen(p.id)} />
        ))}
      </div>

      {done.length > 0 && (
        <>
          <button className="link rules-toggle" onClick={() => setShowDone(!showDone)}>
            <ChevronRight size={14} className={showDone ? 'rotated' : ''} /> {showDone ? 'Hide' : 'Show'} {done.length} reviewed
          </button>
          {showDone && (
            <div className="rules-list">
              {done.map((p) => (
                <Row key={p.id} p={p} onOpen={() => setOpen(p.id)} />
              ))}
            </div>
          )}
        </>
      )}

      {open !== undefined && (
        <Review
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

function Progress({ run }: { run: CurationRun }) {
  const params = (run.stats.params ?? {}) as { limit?: number }
  const drafted = Object.entries(run.stats)
    .filter(([k]) => k.includes(':'))
    .reduce((n, [, v]) => n + Number(v), 0)
  return (
    <div className="rules-progress">
      <Loader2 size={16} className="spin" />
      {run.status === 'queued'
        ? 'Waiting for a worker to start (make worker)…'
        : `Looking for missing rules… ${drafted} of ${params.limit ?? '?'} drafted`}
    </div>
  )
}

/** A suggestion's headline, in plain words */
function headline(p: ProposalSummary): string {
  switch (p.kind) {
    case 'rule':
      return `New rule for ${p.title}`
    case 'enable_pack':
      return `Turn on an existing rule pack for ${p.title}`
    case 'ignore':
      return `Ignore ${p.title}`
    case 'vocabulary':
      return `Can't capture yet: ${p.title}`
  }
}

function statusOf(p: ProposalSummary): { label: string; className: string } {
  if (p.status === 'needs_person') return { label: 'Needs your judgment', className: 'badge badge-warn' }
  if (p.status === 'approved') return { label: 'Approved', className: 'badge badge-ok' }
  if (p.status === 'rejected') return { label: 'Rejected', className: 'badge' }
  return { label: 'Ready', className: 'badge badge-accent' }
}

function Row({ p, onOpen }: { p: ProposalSummary; onOpen: () => void }) {
  const s = statusOf(p)
  return (
    <button className="rules-row" onClick={onOpen}>
      <div className="rules-row-main">
        <div className="rules-row-title">{headline(p)}</div>
        <div className="rules-row-summary">{p.summary}</div>
      </div>
      <span className={s.className}>{s.label}</span>
      <ChevronRight size={16} className="muted" />
    </button>
  )
}

function Review({ id, onClose, onDone }: { id: number; onClose: () => void; onDone: (message: string) => void }) {
  const [p, setP] = useState<Proposal>()
  const [loadError, setLoadError] = useState<string>()
  const [rejecting, setRejecting] = useState(false)
  const [why, setWhy] = useState('')
  const { run, busy, error } = useAction()

  useEffect(() => {
    api.curation.proposal(id).then(setP, (e: Error) => setLoadError(e.message))
  }, [id])

  const open = p && (p.status === 'ready' || p.status === 'needs_person')
  const approve = () =>
    run(async () => {
      const out = await api.curation.approve(id)
      onDone(
        p?.kind !== 'rule'
          ? 'Approved'
          : out.mode === 'push'
            ? `${p.rule_id} pushed to ${out.base ?? 'the default branch'}`
            : `${p.rule_id} added to the pull request`,
      )
    })
  const reject = () =>
    run(async () => {
      await api.curation.reject(id, why)
      onDone('Rejected')
    })

  const cases = p ? Object.entries(p.tests ?? {}) : []
  const passed = p?.proof?.cases?.filter((c) => c.passed).length ?? 0

  return (
    <Drawer
      wide
      title={p ? headline(p) : 'Suggested rule'}
      onClose={onClose}
      footer={
        open ? (
          rejecting ? (
            <>
              <input className="rules-why" autoFocus placeholder="Why not? (Steno keeps this with the pattern)" value={why} onChange={(e) => setWhy(e.target.value)} />
              <button className="btn btn-ghost" onClick={() => setRejecting(false)}>
                Back
              </button>
              <button className="btn btn-danger" disabled={busy || !why.trim()} onClick={reject}>
                Reject
              </button>
            </>
          ) : (
            <>
              <button className="btn btn-secondary" onClick={() => setRejecting(true)}>
                Reject
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
        <div className="rules-review">
          <p className="rules-summary">{p.summary}</p>

          <section className="rules-effect">
            <h3>{open ? 'If you approve' : p.status === 'approved' ? 'What approving did' : 'Rejected'}</h3>
            <p>{p.status === 'rejected' ? `${p.reviewed_by ?? 'Someone'} rejected it: ${p.comment}` : effect(p, passed)}</p>
            {p.status === 'approved' && (p.publish?.pr_url || p.publish?.compare_url) && (
              <a className="rules-pr" href={(p.publish.pr_url ?? p.publish.compare_url) as string} target="_blank" rel="noreferrer">
                {p.publish.pr_url ? 'Open the pull request' : 'Open a pull request for steno/rules'} <ExternalLink size={13} />
              </a>
            )}
          </section>

          {p.rule && (
            <section>
              <h3>The rule</h3>
              <pre className="rules-code">{p.rule}</pre>
            </section>
          )}

          {cases.length > 0 && (
            <details>
              <summary>
                Test cases: {passed} of {cases.length} pass
              </summary>
              {cases.map(([name, c]) => (
                <div key={name} className="rules-case">
                  <div className="rules-case-name">{name.replaceAll('-', ' ')}</div>
                  {Object.entries(c.files ?? {}).map(([path, text]) => (
                    <div key={path}>
                      <div className="mono small muted">{path}</div>
                      <pre className="rules-code">{text}</pre>
                    </div>
                  ))}
                  <div className="small muted">Facts it must produce</div>
                  <pre className="rules-code">{JSON.stringify(c.expected ?? {}, null, 1)}</pre>
                </div>
              ))}
            </details>
          )}

          {p.samples.length > 0 && (
            <details>
              <summary>Where Steno found the code ({p.samples.length})</summary>
              {p.samples.map((s, i) => (
                <div key={i} className="mono small rules-sample">
                  {s.repository} · {s.file}
                  {s.line ? `:${s.line}` : ''}
                </div>
              ))}
            </details>
          )}

          <p className="rules-meta">
            Drafting this cost ${p.cost_usd.toFixed(2)} (one time). Running the rule costs nothing.
          </p>
        </div>
      )}
    </Drawer>
  )
}

/** What approving does, in one concrete sentence */
function effect(p: Proposal, passed: number): string {
  if (p.status === 'needs_person')
    return "Steno couldn't tell what this code means. Reject it with a note so it isn't suggested again."
  switch (p.kind) {
    case 'rule': {
      const pack = String(p.pack?.name ?? 'a pack')
      const where = p.pack_exists ? `the existing pack ${pack}` : `a new ${p.pack?.source === 'core' ? 'core' : 'organization'} pack, ${pack}`
      const pub = p.publish
      if (pub?.error) return `Adds ${p.rule_id} to ${where}. Publishing isn't set up: ${pub.error}`
      const repo = pub?.repository ?? 'its repository'
      if (p.status === 'approved')
        return pub?.mode === 'push'
          ? `Added ${p.rule_id} to ${where} and pushed it to ${pub.base} on ${repo}.`
          : `Added ${p.rule_id} to ${where} on the ${pub?.branch} pull request (${repo}). It runs once the pull request is merged.`
      return pub?.mode === 'push'
        ? `Adds ${p.rule_id} to ${where} and pushes it to ${pub.base} on ${repo} (${passed} tests pass). It runs from the next ingestion.`
        : `Adds ${p.rule_id} to ${where} on the pull request from ${pub?.branch ?? 'steno/rules'} into ${pub?.base ?? 'main'} (${repo}). ${passed} tests pass; it runs once merged.`
    }
    case 'enable_pack':
      return 'Records that an existing pack covers this; turning it on is still yours to do.'
    case 'ignore':
      return 'Stops reporting it as unexplained, everywhere. Reopen it from Coverage anytime.'
    case 'vocabulary':
      return 'Nothing changes in the graph; it’s logged as a gap that needs a new kind of fact.'
  }
}
