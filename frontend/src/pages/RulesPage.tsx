import { AlertTriangle, CheckCircle2, ChevronRight, ExternalLink, History, Loader2, Search, Sparkles } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { api, type CurationEstimate, type CurationInfo, type CurationRun, type Proposal, type ProposalSummary } from '../api/client'
import { Drawer, EmptyState, ErrorAlert, Notice, PageHeader } from '../components/ui'
import { useAction } from '../hooks/useAction'
import { usePoll } from '../hooks/usePoll'

/**
 * Rule Composer (D120): Steno drafts rules for code its current rules don't explain, and a
 * person accepts or rejects each one. Nothing reaches the graph until it's approved.
 *
 * The page has two parts: the search happening now (or ready to start) at the top, and below
 * it what's to review, what's approved, and every past search with what it found and cost.
 */
type Tab = 'review' | 'approved' | 'history'

export function RulesPage() {
  const loadInfo = useCallback(() => api.curation.info(), [])
  const { data: info, error: infoError, reload: reloadInfo } = usePoll(loadInfo, 2000)
  const loadList = useCallback(() => api.curation.proposals(), [])
  const { data: proposals, error, reload } = usePoll(loadList, 3000)
  const [tab, setTab] = useState<Tab>('review')
  const [openProposal, setOpenProposal] = useState<number>()
  const [openRun, setOpenRun] = useState<CurationRun>()
  const [notice, setNotice] = useState<string>()
  const { run, busy, error: startError } = useAction()

  const latest = info?.runs[0]
  const working = !!latest && (latest.status === 'queued' || latest.status === 'running')
  const toReview = (proposals ?? []).filter((p) => p.status === 'ready' || p.status === 'needs_person')
  // Rejected suggestions stay in the database (their patterns aren't drafted again); they're
  // listed only inside the search that drafted them
  const approved = (proposals ?? []).filter((p) => p.status === 'approved')

  // Show the search the moment it's queued, not at the next poll
  const start = (opts: StartOptions) =>
    run(async () => {
      await api.curation.start(opts)
      reloadInfo()
      reload()
    })

  return (
    <>
      <PageHeader
        title="Rule Composer"
        description="Steno drafts and tests rules for code it can't explain yet. Nothing changes until you approve."
      />
      <ErrorAlert message={error ?? infoError ?? startError} />
      <Notice message={notice} onDone={() => setNotice(undefined)} />

      {info && <Now info={info} latest={latest} working={working} starting={busy} onStart={start} onOpen={setOpenRun} />}

      <div className="rules-tabs" role="tablist">
        <TabButton active={tab === 'review'} onClick={() => setTab('review')} label="To review" count={toReview.length} />
        <TabButton active={tab === 'approved'} onClick={() => setTab('approved')} label="Approved" count={approved.length} />
        <TabButton active={tab === 'history'} onClick={() => setTab('history')} label="Past searches" count={info?.searches ?? 0} />
      </div>

      {tab === 'review' &&
        (proposals && toReview.length === 0 ? (
          <EmptyState icon={Sparkles} title={working ? 'Suggestions will appear here' : 'Nothing to review'}>
            {working
              ? 'Each suggestion shows up as soon as it’s drafted and tested.'
              : 'Start a search above to have Steno draft rules for the code its rules don’t explain yet.'}
          </EmptyState>
        ) : (
          <div className="rules-list">
            {groups(toReview).map((g) =>
              g.items.length > 1 && g.owner ? (
                <Framework
                  key={g.owner}
                  owner={g.owner}
                  items={g.items}
                  onOpen={setOpenProposal}
                  onDone={(message) => {
                    setNotice(message)
                    reload()
                  }}
                />
              ) : (
                g.items.map((p) => <Row key={p.id} p={p} onOpen={() => setOpenProposal(p.id)} />)
              ),
            )}
          </div>
        ))}

      {tab === 'approved' &&
        (approved.length === 0 ? (
          <EmptyState icon={CheckCircle2} title="Nothing approved yet">
            Rules you approve appear here with the pull request they went into.
          </EmptyState>
        ) : (
          <div className="rules-list">
            {approved.map((p) => (
              <Row key={p.id} p={p} onOpen={() => setOpenProposal(p.id)} />
            ))}
          </div>
        ))}

      {tab === 'history' && info && <PastSearches info={info} onOpen={setOpenRun} />}

      {openRun && openProposal === undefined && (
        <SearchDrawer run={openRun} models={info?.models ?? []} onClose={() => setOpenRun(undefined)} onOpenProposal={setOpenProposal} />
      )}
      {openProposal !== undefined && (
        <Review
          id={openProposal}
          onClose={() => setOpenProposal(undefined)}
          onDone={(message) => {
            setOpenProposal(undefined)
            setNotice(message)
            reload()
          }}
        />
      )}
    </>
  )
}

function TabButton({ active, onClick, label, count }: { active: boolean; onClick: () => void; label: string; count: number }) {
  return (
    <button role="tab" aria-selected={active} className={`rules-tab${active ? ' active' : ''}`} onClick={onClick}>
      {label}
      {count > 0 && <span className="rules-tab-count">{count}</span>}
    </button>
  )
}

// ------------------------------------------------------------------ the search happening now

/**
 * The top panel: a search running now, or one ready to start. The last search is mentioned in
 * one line, so "now" and "before" are never confused.
 */
function Now({
  info,
  latest,
  working,
  starting,
  onStart,
  onOpen,
}: {
  info: CurationInfo
  latest?: CurationRun
  working: boolean
  starting: boolean
  onStart: (opts: StartOptions) => void
  onOpen: (run: CurationRun) => void
}) {
  const [model, setModel] = useState(info.model)
  const [est, setEst] = useState<CurationEstimate>()
  const [checking, setChecking] = useState(false)
  const waiting = info.open_patterns
  useEffect(() => {
    if (working) return
    api.curation.estimate(model).then(setEst, () => setEst(undefined))
  }, [model, waiting, working])
  if (latest && working) return <Running run={latest} models={info.models} />
  const name = modelName(info.models, model)
  // A large search is confirmed first (§9): its projection passes the limit, or there's no
  // history to project from
  const large = !!est && est.patterns > est.pilot && (est.high_usd === undefined || est.high_usd > est.budget_usd)
  return (
    <section className="rules-now">
      <div className="rules-now-main">
        <div className="rules-now-label">New search</div>
        <div className="rules-now-title">
          {waiting === 0 ? 'Nothing left to search' : `${waiting} unexplained ${waiting === 1 ? 'pattern' : 'patterns'}`}
        </div>
        <div className="small muted">
          {waiting === 0
            ? 'New ones appear when a repository is ingested or added.'
            : est?.expected_usd !== undefined
              ? `About $${est.expected_usd.toFixed(2)} to draft with ${name} (up to $${est.high_usd?.toFixed(2)}) · limit $${info.budget_usd.toFixed(0)}`
              : `No cost history for ${name} yet · limit $${info.budget_usd.toFixed(0)}`}
        </div>
      </div>
      <div className="rules-now-start">
        <select className="rules-model" value={model} onChange={(e) => setModel(e.target.value)} disabled={starting} aria-label="Model that drafts the rules" title="The model that drafts this search's rules">
          {info.models.map((m) => (
            <option key={m.id} value={m.id}>
              {m.name}
              {m.id === info.model ? ' (default)' : ''}
            </option>
          ))}
        </select>
        <button
          className="btn btn-primary"
          onClick={() => (large ? setChecking(true) : onStart({ model }))}
          disabled={starting || waiting === 0}
          title={waiting === 0 ? 'No open patterns to search' : undefined}
        >
          {starting ? <Loader2 size={15} className="spin" /> : <Search size={15} />}
          {starting ? 'Starting…' : 'Find missing rules'}
        </button>
      </div>
      {checking && est && (
        <CostCheck
          est={est}
          modelName={name}
          onClose={() => setChecking(false)}
          onStart={(opts) => {
            setChecking(false)
            onStart({ model, ...opts })
          }}
        />
      )}
      {latest?.finished_at && (
        <button className="rules-now-last" onClick={() => onOpen(latest)}>
          <History size={14} />
          <span>
            Last search {ago(latest.finished_at)}: {found(latest)}, ${latest.spent_usd.toFixed(2)}
            {latest.status === 'failed' && <span className="rules-warn-text"> · stopped early</span>}
          </span>
          <ChevronRight size={14} />
        </button>
      )}
    </section>
  )
}

function modelName(models: CurationInfo['models'], id: string | null): string {
  return models.find((m) => m.id === id)?.name ?? id ?? 'unknown model'
}

function Running({ run, models }: { run: CurationRun; models: CurationInfo['models'] }) {
  const now = useNow()
  const total = Number(run.stats.total ?? 0)
  const byKind = finished(run)
  const done = Object.values(byKind).reduce((a, b) => a + b, 0)
  const inflight = Object.values((run.stats.inflight ?? {}) as Record<string, { label: string; since: string }>)
  const queued = run.status === 'queued'
  const waitedLong = queued && (now - new Date(run.started_at).getTime()) / 1000 > 10

  return (
    <section className="rules-now rules-now-running">
      <div className="rules-now-main">
        <div className="rules-now-label">
          <span className="rules-dot" /> Searching now
        </div>
        <div className="rules-now-title">
          {queued ? 'Starting…' : total ? `${done} of ${total} patterns looked at` : 'Choosing what to look at…'}
        </div>
        {waitedLong && <div className="small rules-warn-text">Still waiting for a worker. Is one running? (make worker)</div>}
      </div>
      <div className="rules-now-cost">
        <div className="rules-now-label">Drafting cost</div>
        <div className="rules-now-title">
          ${run.spent_usd.toFixed(2)} <span className="muted small">of ${(run.budget_usd ?? 0).toFixed(0)}</span>
        </div>
      </div>
      {!queued && (
        <>
          <div className="rules-bar">
            <div style={{ width: `${total ? (100 * done) / total : 0}%` }} />
          </div>
          <div className="rules-now-foot small">
            <span className="muted">
              {inflight.length > 0 ? `Drafting ${inflight.length} at once` : run.stats.stopping ? 'Finishing…' : 'Getting ready…'}
            </span>
            <span className="muted">
              {done > 0 ? `${breakdown(byKind)} · ` : ''}
              {modelName(models, run.model)} · running {since(run.started_at, now)}
            </span>
          </div>
          {inflight.length > 0 && (
            <ul className="rules-inflight small">
              {inflight.map((d) => (
                <li key={d.label}>
                  <Loader2 size={13} className="spin" />
                  <span className="mono">{d.label}</span>
                  <span className="muted">{since(d.since, now)}</span>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </section>
  )
}

type StartOptions = { model?: string; limit?: number; budget_usd?: number }

/** Before a large search: what it could cost, and a pilot or a ceiling the person sets */
function CostCheck({
  est,
  modelName,
  onClose,
  onStart,
}: {
  est: CurationEstimate
  modelName: string
  onClose: () => void
  onStart: (opts: StartOptions) => void
}) {
  const [ceiling, setCeiling] = useState(String(Math.ceil(Math.max(est.budget_usd, est.high_usd ?? 0))))
  const value = Number(ceiling)
  return (
    <Drawer
      title="Before a large search"
      description={`${est.patterns} patterns in ${est.frameworks} frameworks, drafted with ${modelName}`}
      onClose={onClose}
      footer={
        <>
          <button className="btn btn-ghost" onClick={onClose}>
            Cancel
          </button>
          <button className="btn btn-secondary" onClick={() => onStart({ limit: est.pilot })}>
            Pilot: top {est.pilot}
          </button>
          <button className="btn btn-primary" disabled={!(value > 0)} onClick={() => onStart({ budget_usd: value })}>
            Search all, up to ${value > 0 ? value : '…'}
          </button>
        </>
      }
    >
      <div className="rules-stats rules-stats-3">
        <Stat label="Expected" value={est.expected_usd !== undefined ? `$${est.expected_usd.toFixed(2)}` : 'unknown'} />
        <Stat label="High" value={est.high_usd !== undefined ? `$${est.high_usd.toFixed(2)}` : 'unknown'} />
        <Stat label="Worst case" value={`$${est.worst_usd.toFixed(0)}`} />
      </div>
      <p className="small muted rules-check-note">
        {est.expected_usd !== undefined
          ? `Projected from ${est.history} past drafts with ${modelName}, about $${est.mean_usd?.toFixed(2)} each. The worst case is every pattern at the $${(est.worst_usd / Math.max(est.patterns, 1)).toFixed(0)} cap per suggestion.`
          : `${modelName} hasn't drafted anything yet, so there's nothing to project from. A pilot of the top ${est.pilot} gives a projection for the rest.`}
      </p>
      <label className="rules-ceiling">
        <span>Spending limit for this search</span>
        <span className="rules-ceiling-input">
          $<input type="number" min={1} step={1} value={ceiling} onChange={(e) => setCeiling(e.target.value)} />
        </span>
      </label>
      <p className="small muted">The search stops before going over it; what's left waits for the next one.</p>
    </Drawer>
  )
}

// ------------------------------------------------------------------ to review, by framework

type Group = { owner: string | null; items: ProposalSummary[] }

/** Suggestions grouped by the framework (library or package root) their pattern belongs to,
 * largest first; a framework is reviewed as one unit (D123) */
function groups(items: ProposalSummary[]): Group[] {
  const by = new Map<string, ProposalSummary[]>()
  const alone: Group[] = []
  for (const p of items) {
    if (!p.owner) alone.push({ owner: null, items: [p] })
    else by.set(p.owner, [...(by.get(p.owner) ?? []), p])
  }
  return [...[...by].map(([owner, items]) => ({ owner, items })).sort((a, b) => b.items.length - a.items.length), ...alone]
}

function Framework({
  owner,
  items,
  onOpen,
  onDone,
}: {
  owner: string
  items: ProposalSummary[]
  onOpen: (id: number) => void
  onDone: (message: string) => void
}) {
  const [rejecting, setRejecting] = useState(false)
  const [why, setWhy] = useState('')
  const { run, busy, error } = useAction()
  const ready = items.filter((p) => p.status === 'ready')
  const byKind: Record<string, number> = {}
  for (const p of items) byKind[p.kind] = (byKind[p.kind] ?? 0) + 1

  const result = (out: { ok: boolean; error?: string }[], verb: string) => {
    const failed = out.filter((r) => !r.ok)
    if (failed.length) throw new Error(`${out.length - failed.length} ${verb}; ${failed.length} failed: ${failed[0].error}`)
    onDone(`${out.length} ${verb}`)
  }
  const approveAll = () => run(async () => result(await api.curation.approveMany(ready.map((p) => p.id)), 'approved'))
  const rejectAll = () => run(async () => result(await api.curation.rejectMany(items.map((p) => p.id), why), 'rejected'))

  return (
    <section className="rules-group">
      <header className="rules-group-head">
        <div className="rules-row-main">
          <div className="rules-row-title mono">{owner}</div>
          <div className="small muted">
            {items.length} suggestions · {breakdown(byKind)}
          </div>
        </div>
        {rejecting ? (
          <div className="rules-group-actions">
            <input className="rules-why" autoFocus placeholder="Why not, for all of them?" value={why} onChange={(e) => setWhy(e.target.value)} />
            <button className="btn btn-ghost" onClick={() => setRejecting(false)}>
              Back
            </button>
            <button className="btn btn-danger" disabled={busy || !why.trim()} onClick={rejectAll}>
              Reject all
            </button>
          </div>
        ) : (
          <div className="rules-group-actions">
            <button className="btn btn-secondary" disabled={busy} onClick={() => setRejecting(true)}>
              Reject all
            </button>
            {ready.length > 0 && (
              <button className="btn btn-primary" disabled={busy} onClick={approveAll}>
                {busy ? <Loader2 size={15} className="spin" /> : <CheckCircle2 size={15} />} Approve {ready.length === items.length ? 'all' : `${ready.length} ready`}
              </button>
            )}
          </div>
        )}
      </header>
      <ErrorAlert message={error} />
      <div className="rules-group-items">
        {items.map((p) => (
          <Row key={p.id} p={p} onOpen={() => onOpen(p.id)} />
        ))}
      </div>
    </section>
  )
}

// ------------------------------------------------------------------ past searches

function PastSearches({ info, onOpen }: { info: CurationInfo; onOpen: (run: CurationRun) => void }) {
  if (info.runs.length === 0)
    return (
      <EmptyState icon={History} title="No searches yet">
        Each search is kept here with what it found and what it cost.
      </EmptyState>
    )
  return (
    <>
      <div className="rules-totals">
        <div>
          <div className="rules-now-label">Searches</div>
          <div className="rules-totals-value">{info.searches}</div>
        </div>
        <div>
          <div className="rules-now-label">Drafting cost, all searches</div>
          <div className="rules-totals-value">${info.spent_total_usd.toFixed(2)}</div>
        </div>
        <p className="small muted">
          What Claude cost to draft suggestions, at API prices. On a Claude plan login it counts toward the plan's usage instead. Approved rules cost nothing to run.
        </p>
      </div>
      <div className="rules-list">
        {info.runs.map((r) => (
          <button key={r.id} className="rules-row" onClick={() => onOpen(r)}>
            <RunStatusIcon run={r} />
            <div className="rules-row-main">
              <div className="rules-row-title">
                {when(r.started_at)} <span className="rules-model-tag">{modelName(info.models, r.model)}</span>
              </div>
              <div className="rules-row-summary">
                {r.status === 'running' || r.status === 'queued'
                  ? 'Searching now'
                  : `${found(r)}${Object.keys(finished(r)).length ? ` · ${breakdown(finished(r))}` : ''}`}
              </div>
            </div>
            <div className="rules-row-side">
              <div className="rules-money">${r.spent_usd.toFixed(2)}</div>
              <div className="small muted">{r.finished_at ? since(r.started_at, new Date(r.finished_at).getTime()) : '…'}</div>
            </div>
            <ChevronRight size={16} className="muted" />
          </button>
        ))}
      </div>
    </>
  )
}

function RunStatusIcon({ run }: { run: CurationRun }) {
  if (run.status === 'failed') return <AlertTriangle size={18} className="rules-icon-warn" aria-label="Stopped early" />
  if (run.status === 'running' || run.status === 'queued') return <Loader2 size={18} className="spin rules-icon-accent" aria-label="Running" />
  return <CheckCircle2 size={18} className="rules-icon-ok" aria-label="Finished" />
}

/** One past search: when, how long, what it cost, why it stopped, and what it found */
function SearchDrawer({
  run,
  models,
  onClose,
  onOpenProposal,
}: {
  run: CurationRun
  models: CurationInfo['models']
  onClose: () => void
  onOpenProposal: (id: number) => void
}) {
  const [items, setItems] = useState<ProposalSummary[]>()
  const [loadError, setLoadError] = useState<string>()
  useEffect(() => {
    api.curation.ofRun(run.id).then(setItems, (e: Error) => setLoadError(e.message))
  }, [run.id])

  const left = Number(run.stats.left_for_next_run ?? 0)
  const total = Number(run.stats.total ?? 0)
  const shown = (items ?? []).filter((p) => p.status !== 'failed')
  const notDrafted = (items ?? []).length - shown.length

  return (
    <Drawer
      wide
      title={`Search on ${when(run.started_at)}`}
      description={`Rules drafted with ${modelName(models, run.model)}`}
      onClose={onClose}
      footer={
        <button className="btn btn-secondary" onClick={onClose}>
          Close
        </button>
      }
    >
      <ErrorAlert message={loadError} />
      <div className="rules-stats">
        <Stat label="Found" value={String(Object.values(finished(run)).reduce((a, b) => a + b, 0))} />
        <Stat label="Patterns" value={total ? String(total) : '—'} />
        <Stat label="Took" value={run.finished_at ? since(run.started_at, new Date(run.finished_at).getTime()) : 'running'} />
        <Stat label="Drafting cost" value={`$${run.spent_usd.toFixed(2)}`} />
      </div>

      {run.status === 'failed' && (
        <div className="rules-callout rules-callout-warn">
          <AlertTriangle size={15} />
          <div>
            <strong>It stopped early.</strong> {run.error?.replace(/\.?$/, '.')}
            {left > 0 && ` ${left} ${left === 1 ? 'pattern was' : 'patterns were'} left for the next search.`}
          </div>
        </div>
      )}
      {run.status === 'succeeded' && left > 0 && (
        <div className="rules-callout">
          It stopped at the ${(run.budget_usd ?? 0).toFixed(0)} limit with {left} {left === 1 ? 'pattern' : 'patterns'} left for the next search.
        </div>
      )}

      <h3 className="rules-subhead">What it found</h3>
      {items && shown.length === 0 && <p className="muted small">No suggestions.</p>}
      <div className="rules-list">
        {shown.map((p) => (
          <Row key={p.id} p={p} onOpen={() => onOpenProposal(p.id)} />
        ))}
      </div>
      {notDrafted > 0 && (
        <p className="small muted">
          {notDrafted} more {notDrafted === 1 ? 'pattern wasn’t' : 'patterns weren’t'} drafted because Claude couldn’t be used; they stayed open for a later search.
        </p>
      )}
    </Drawer>
  )
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rules-stat">
      <div className="rules-now-label">{label}</div>
      <div className="rules-stat-value">{value}</div>
    </div>
  )
}

// ------------------------------------------------------------------ suggestions

const KIND_LABEL: Record<ProposalSummary['kind'], string> = {
  rule: 'New rule',
  enable_pack: 'Existing pack',
  ignore: 'Ignore',
  vocabulary: "Can't capture yet",
}

/** A suggestion's name: the drafter's title, or (older drafts) one built from the pattern */
function headline(p: ProposalSummary): string {
  if (p.name) return p.name
  return p.kind === 'rule' ? `New rule for ${p.title}` : p.kind === 'ignore' ? `Ignore ${p.title}` : p.title
}

/** The kind, and the pattern it was drafted for */
function Meta({ p }: { p: ProposalSummary }) {
  return (
    <div className="rules-meta-line">
      <span className={`rules-kind rules-kind-${p.kind}`}>{KIND_LABEL[p.kind]}</span>
      <span className="mono" title={p.title}>
        {p.title}
      </span>
    </div>
  )
}

function statusOf(p: ProposalSummary): { label: string; className: string } {
  if (p.status === 'needs_person') return { label: 'Needs your judgment', className: 'badge badge-warn' }
  if (p.status === 'approved') return { label: 'Approved', className: 'badge badge-ok' }
  if (p.status === 'rejected') return { label: 'Rejected', className: 'badge' }
  if (p.status === 'failed') return { label: 'Not drafted', className: 'badge' }
  return { label: '', className: '' }
}

function Row({ p, onOpen }: { p: ProposalSummary; onOpen: () => void }) {
  const s = statusOf(p)
  return (
    <div className="rules-row-wrap">
      <button className="rules-row" onClick={onOpen}>
        <div className="rules-row-main">
          <div className="rules-row-title">{headline(p)}</div>
          <div className="rules-row-summary">{p.summary}</div>
          <Meta p={p} />
        </div>
        {s.label && <span className={s.className}>{s.label}</span>}
        <ChevronRight size={16} className="muted" />
      </button>
      {p.status === 'approved' && p.link && (
        <a className="rules-row-link small" href={p.link} target="_blank" rel="noreferrer">
          Pull request <ExternalLink size={12} />
        </a>
      )}
    </div>
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
          <Meta p={p} />

          <section className="rules-effect">
            <h3>{open ? 'If you approve' : p.status === 'approved' ? 'Approved' : p.status === 'failed' ? 'Not drafted' : 'Rejected'}</h3>
            {p.status === 'rejected' ? (
              <p>
                {p.reviewed_by ?? 'Someone'}: {p.comment}
              </p>
            ) : p.status === 'failed' ? (
              <p>{p.summary}</p>
            ) : (
              <Effect p={p} passed={passed} total={cases.length} />
            )}
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
              <summary>Where it's used ({p.samples.length})</summary>
              {p.samples.map((s, i) => {
                const where = `${s.repository} · ${s.file}${s.line ? `:${s.line}` : ''}`
                return (
                  <div key={i} className="mono small rules-sample">
                    {s.url ? (
                      <a href={s.url} target="_blank" rel="noreferrer" title="Open at the commit Steno ingested">
                        {where} <ExternalLink size={12} />
                      </a>
                    ) : (
                      where
                    )}
                  </div>
                )
              })}
            </details>
          )}

          <p className="rules-meta">Drafting this cost ${p.cost_usd.toFixed(2)} (one time). Running the rule costs nothing.</p>
        </div>
      )}
    </Drawer>
  )
}

/** What approving does: for a rule, where it goes; otherwise one short sentence */
function Effect({ p, passed, total }: { p: Proposal; passed: number; total: number }) {
  if (p.status === 'needs_person') return <p>Steno couldn't tell what this code does. Reject it with a note.</p>
  if (p.kind === 'ignore') return <p>Steno stops flagging this code as unexplained.</p>
  if (p.kind === 'vocabulary') return <p>Logged as a gap in Steno's vocabulary. Nothing changes in the graph.</p>
  if (p.kind === 'enable_pack') return <p>Records that an existing pack covers this; turning it on is up to you.</p>
  const pub = p.publish
  const pack = String(p.pack?.name ?? 'a pack')
  const pr = pub?.mode !== 'push'
  return (
    <dl className="rules-facts">
      <dt>Rule</dt>
      <dd className="mono">{p.rule_id}</dd>
      <dt>Pack</dt>
      <dd>
        <span className="mono">{pack}</span>{' '}
        <span className="muted">{p.pack_exists ? 'existing' : `new, ${p.pack?.source === 'core' ? 'core' : 'organization'}`}</span>
      </dd>
      {pub?.path && (
        <>
          <dt>Folder</dt>
          <dd className="mono">{pub.path.replace(/\/[^/]+$/, '/')}</dd>
        </>
      )}
      <dt>Goes to</dt>
      <dd>
        {pub?.error ? (
          <span className="rules-warn-text">Publishing isn't set up: {pub.error}</span>
        ) : pr ? (
          <>
            Pull request <span className="mono">{pub?.branch ?? 'steno/rules'}</span> → <span className="mono">{pub?.base ?? 'main'}</span> <span className="muted">· {pub?.repository}</span>
          </>
        ) : (
          <>
            Pushed to <span className="mono">{pub?.base}</span> <span className="muted">· {pub?.repository}</span>
          </>
        )}
      </dd>
      <dt>Tests</dt>
      <dd>
        {passed} of {total} pass
      </dd>
      <dt>Runs</dt>
      <dd>{pr ? 'Once the pull request is merged' : 'From the next ingestion'}</dd>
    </dl>
  )
}

// ------------------------------------------------------------------ helpers

/** How many suggestions a search has finished, by kind */
function finished(run: CurationRun): Record<string, number> {
  const out: Record<string, number> = {}
  for (const [k, v] of Object.entries(run.stats)) {
    if (k.includes(':')) out[k.split(':')[0]] = (out[k.split(':')[0]] ?? 0) + Number(v)
  }
  return out
}

function found(run: CurationRun): string {
  const n = Object.values(finished(run)).reduce((a, b) => a + b, 0)
  return `${n} ${n === 1 ? 'suggestion' : 'suggestions'}`
}

const KIND_WORDS: Record<string, [string, string]> = {
  rule: ['new rule', 'new rules'],
  enable_pack: ['existing pack', 'existing packs'],
  ignore: ['to ignore', 'to ignore'],
  vocabulary: ["can't capture yet", "can't capture yet"],
  needs_person: ['needs your judgment', 'need your judgment'],
}

function breakdown(byKind: Record<string, number>): string {
  return Object.entries(byKind)
    .map(([k, n]) => `${n} ${(KIND_WORDS[k] ?? [k, k])[n === 1 ? 0 : 1]}`)
    .join(' · ')
}

function since(iso: string | null | undefined, now: number): string {
  if (!iso) return ''
  const s = Math.max(0, Math.round((now - new Date(iso).getTime()) / 1000))
  return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, '0')}s`
}

function when(iso: string): string {
  return new Date(iso).toLocaleString(undefined, { weekday: 'short', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })
}

function ago(iso: string): string {
  const m = Math.round((Date.now() - new Date(iso).getTime()) / 60000)
  if (m < 1) return 'just now'
  if (m < 60) return `${m} min ago`
  if (m < 60 * 24) return `${Math.round(m / 60)} h ago`
  return `on ${when(iso)}`
}

/** The clock, ticking once a second while a search is shown */
function useNow(): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(timer)
  }, [])
  return now
}
