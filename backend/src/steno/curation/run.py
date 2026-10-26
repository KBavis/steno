"""A rule curation run (D120): turn open patterns into proposals a person reviews.

By default a run works on every open pattern (what a first ingestion needs: account for
everything Steno can't explain yet), optionally capped at a number. Its patterns are drafted
in parallel by any workers, in score order, until the run's budget is spent; the rest wait for
the next run. For each pattern:

1. Gather samples: varied occurrences across repositories, read from the git host at the
   commit Steno ingested.
2. Ask the kit whether an existing pack already matches them (no LLM): if so, the proposal is
   "enable that pack", not a new rule.
3. Otherwise the drafter investigates and proposes; a rule is accepted only when its own test
   cases pass.
4. Store the proposal and move the pattern's state, so it's never proposed twice.

docs/how-it-works/ingestion/rule-curation.md §5.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from steno.config import get_settings
from steno.connectors.source import read_file
from steno.coverage.patterns import frameworks
from steno.coverage.runs import latest_runs
from steno.curation import kit
from steno.curation.drafters import (
    Drafter,
    DrafterUnavailable,
    DraftRequest,
    Sample,
    get_drafter,
)
from steno.db.models import (
    Connector,
    CoverageItem,
    CoveragePattern,
    CurationRun,
    PatternState,
    ProposalKind,
    ProposalStatus,
    Repository,
    RuleProposal,
)

log = logging.getLogger(__name__)

# Signals a rule could explain; the rest are fixed another way (a parse error, a version).
# Not `inferred_entry` or `unreachable_effect`: their pattern is one function in one
# application (dead code, an unfinished stub), not something a rule would match elsewhere.
# They stay in the coverage report; when a framework is what starts such code, its annotation
# or base type shows up as an `unknown_hook` pattern, and that is drafted.
DRAFTABLE = {
    "unknown_hook",
    "external_call",
    "library",
    "unread_file",
    "unread_config",
}
SAMPLES = 6
MIN_HISTORY = 5  # past drafts with a model before its cost is projected
CONTEXT = 30  # lines either side of an occurrence


@dataclass
class Picked:
    pattern: CoveragePattern
    siblings: list[str]


def pick(
    session: Session, limit: int | None, fingerprints: list[str] | None = None
) -> list[Picked]:
    """The patterns to work on: the ones asked for, or open draftable patterns in score order,
    taken framework by framework (D123), with their framework's other patterns as context."""
    query = select(CoveragePattern)
    if fingerprints:
        query = query.where(CoveragePattern.fingerprint.in_(fingerprints))
    else:
        query = query.where(
            CoveragePattern.state == PatternState.OPEN,
            CoveragePattern.signal.in_(DRAFTABLE),
            CoveragePattern.occurrences > 0,
        )
    rows = list(session.scalars(query))
    out: list[Picked] = []
    for group in frameworks(rows):
        labels = [p.label for p in group["patterns"]]
        for p in group["patterns"]:
            out.append(Picked(p, [lbl for lbl in labels if lbl != p.label]))
    return out if limit is None else out[:limit]


def curate(
    session: Session,
    limit: int | None = None,
    fingerprints: list[str] | None = None,
    drafter: Drafter | None = None,
    budget_usd: float | None = None,
    trigger: str = "manual",
    model: str | None = None,
) -> CurationRun:
    """Create a run and work it to the end in this process (the command line). Errors that
    stop a run (the provider unavailable or not built) are raised after it's recorded."""
    drafter = drafter or get_drafter(model=model)
    run = queue_run(session, limit, fingerprints, budget_usd, trigger, drafter.name, model)
    while (work := claim(session, run.id)) is not None:
        draft_one(session, work, drafter)
    session.refresh(run)
    return run


def queue_run(
    session: Session,
    limit: int | None = None,
    fingerprints: list[str] | None = None,
    budget_usd: float | None = None,
    trigger: str = "manual",
    drafter: str | None = None,
    model: str | None = None,
) -> CurationRun:
    """A run waiting for workers (the UI's "Find missing rules"): drafting is slow LLM work,
    so it runs in workers, never in the API process."""
    settings = get_settings()
    run = CurationRun(
        trigger=trigger,
        drafter=drafter or settings.rule_drafter,
        model=model or settings.rule_drafter_model,
        status="queued",
        budget_usd=budget_usd if budget_usd is not None else settings.curation_budget_usd,
        stats={"params": {"limit": limit, "fingerprints": fingerprints or []}},
    )
    session.add(run)
    session.commit()
    return run


# ---------------------------------------------------------------- the cost check


def estimate(session: Session, model: str | None = None) -> dict[str, Any]:
    """What a search over every open pattern would cost (§9): the worst case (each pattern at
    the per-pattern cap), and a projection from the cost of past drafts with this model. With
    too little history there's no projection; a pilot of the top patterns gives one."""
    settings = get_settings()
    model = model or settings.rule_drafter_model
    picks = pick(session, None)
    costs = sorted(
        c
        for (c,) in session.execute(
            select(RuleProposal.cost_usd)
            .join(CurationRun, CurationRun.id == RuleProposal.run_id)
            .where(CurationRun.model == model, RuleProposal.cost_usd > 0)
        )
    )
    n = len(picks)
    out: dict[str, Any] = {
        "model": model,
        "patterns": n,
        "frameworks": len({p.pattern.owner for p in picks if p.pattern.owner}),
        "worst_usd": round(n * settings.rule_drafter_max_usd, 2),
        "budget_usd": settings.curation_budget_usd,
        "pilot": settings.curation_pilot,
        "history": len(costs),
    }
    if len(costs) >= MIN_HISTORY:
        mean = sum(costs) / len(costs)
        p90 = costs[min(len(costs) - 1, int(0.9 * len(costs)))]
        out |= {"mean_usd": round(mean, 4), "p90_usd": round(p90, 4),
                "expected_usd": round(n * mean, 2), "high_usd": round(n * p90, 2)}  # fmt: skip
    return out


# ---------------------------------------------------------------- parallel drafting
#
# A run is a queue of patterns. The first worker to see a queued run lists its patterns and
# marks them `queued` (so no other run takes them). Then any worker process, on any machine,
# claims the next one, drafts it, and records the result, so a run's patterns are drafted in
# parallel up to `curation_parallel` at once. Claiming locks the run's row briefly and reserves
# the per-pattern cap against the budget, so parallel drafts never spend past it. A claim a
# worker never finished (it crashed) goes back to the queue after `rule_drafter_timeout_min`.
# The run finishes when nothing is in flight and nothing more can be claimed.


@dataclass
class Work:
    run_id: int
    fingerprint: str


def claim(session: Session, run_id: int | None = None) -> Work | None:
    """The next pattern to draft, from the oldest run with one to give (or from `run_id`),
    or None. Starts queued runs and finishes runs that are done along the way."""
    query = select(CurationRun).where(CurationRun.status.in_(("queued", "running")))
    if run_id is not None:
        query = query.where(CurationRun.id == run_id)
    for rid in list(
        session.scalars(query.with_only_columns(CurationRun.id).order_by(CurationRun.id))
    ):
        run = _locked(session, rid)
        if run is None or run.status not in ("queued", "running"):
            session.commit()
            continue
        if run.status == "queued":
            _start(session, run)
        _recover(session, run)
        work = _next(session, run)
        if work is None and not _stats(run).get("inflight"):
            _finish(session, run)
        session.commit()
        if work is not None:
            return work
    return None


def draft_one(session: Session, work: Work, drafter: Drafter | None = None) -> None:
    """Draft one claimed pattern and record what came of it."""
    run = session.get_one(CurationRun, work.run_id)
    drafter = drafter or get_drafter(run.drafter, run.model)
    p = session.scalar(
        select(CoveragePattern).where(CoveragePattern.fingerprint == work.fingerprint)
    )
    try:
        if p is None:
            raise ValueError(f"pattern {work.fingerprint} is gone")
        log.info("curation %s: %s", work.run_id, work.fingerprint)
        reader = _reader(session)
        samples, files = gather(session, p, reader)
        proposal = _existing_rule(p, samples, files) or _draft(
            drafter, p, samples, _siblings(session, run, p), reader
        )
    except Exception as exc:
        # Nothing more can be drafted (the provider is unavailable or not built, or the run's
        # own machinery failed): this pattern goes back to the queue and the run stops
        if isinstance(exc, SQLAlchemyError):
            session.rollback()  # the session can't be used until it is
        run = _locked(session, work.run_id)
        if p is not None and p.state == PatternState.DRAFTING:
            p.state = PatternState.QUEUED
        _release(run, work.fingerprint)
        stats = _stats(run)
        stats["stopping"] = "error"
        run.stats = stats
        run.error = run.error or (
            str(exc) if isinstance(exc, DrafterUnavailable) else f"{type(exc).__name__}: {exc}"
        )
        if not stats.get("inflight"):
            _finish(session, run)
        session.commit()
        raise
    run = _locked(session, work.run_id)
    proposal.run_id = run.id
    session.add(proposal)
    p.state = _state_after(proposal)
    p.note = f"curation run {run.id}: {proposal.kind.value}, {proposal.status.value}"
    run.spent_usd = round((run.spent_usd or 0.0) + proposal.cost_usd, 4)
    _release(run, work.fingerprint)
    stats = _stats(run)
    key = f"{proposal.kind.value}:{proposal.status.value}"
    stats[key] = stats.get(key, 0) + 1  # progress, visible while it runs
    run.stats = stats
    if not stats.get("inflight") and _next_fingerprint(session, run) is None:
        _finish(session, run)
    session.commit()


def _locked(session: Session, run_id: int) -> CurationRun | None:
    """The run, re-read and locked for this transaction: claims and results on the same run
    from several workers take turns."""
    return session.scalar(
        select(CurationRun)
        .where(CurationRun.id == run_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


def _stats(run: CurationRun) -> dict[str, Any]:
    """A copy of the run's stats to change and assign back (JSON columns aren't tracked)."""
    stats = dict(run.stats or {})
    stats["inflight"] = dict(stats.get("inflight") or {})
    return stats


def _start(session: Session, run: CurationRun) -> None:
    params = (run.stats or {}).get("params") or {}
    picks = pick(session, params.get("limit"), params.get("fingerprints") or None)
    for picked in picks:
        picked.pattern.state = PatternState.QUEUED
        picked.pattern.note = f"curation run {run.id}: queued"
    run.status = "running"
    run.started_at = datetime.now(UTC)  # until now it was waiting
    run.stats = {
        **(run.stats or {}),
        "total": len(picks),
        "queue": [picked.pattern.fingerprint for picked in picks],
        "inflight": {},
        "reserved_usd": 0.0,
    }


def _next_fingerprint(session: Session, run: CurationRun) -> CoveragePattern | None:
    queue = (run.stats or {}).get("queue") or []
    if not queue:
        return None
    waiting = {
        p.fingerprint: p
        for p in session.scalars(
            select(CoveragePattern).where(
                CoveragePattern.fingerprint.in_(queue),
                CoveragePattern.state == PatternState.QUEUED,
            )
        )
    }
    return next((waiting[fp] for fp in queue if fp in waiting), None)


def _next(session: Session, run: CurationRun) -> Work | None:
    """Claim the run's next queued pattern, if the run may take one more: it isn't stopping,
    fewer than `curation_parallel` are in flight, and a worst-case draft still fits the budget."""
    settings = get_settings()
    stats = _stats(run)
    if stats.get("stopping") or len(stats["inflight"]) >= settings.curation_parallel:
        return None
    p = _next_fingerprint(session, run)
    if p is None:
        return None
    cap = settings.rule_drafter_max_usd
    committed = (run.spent_usd or 0.0) + float(stats.get("reserved_usd") or 0.0)
    if committed > 0 and committed + min(cap, run.budget_usd or 0.0) > (run.budget_usd or 0.0):
        if not stats["inflight"]:  # otherwise wait: what's in flight may cost less than its cap
            stats["stopping"] = "budget"
            run.stats = stats
        return None
    p.state = PatternState.DRAFTING
    stats["inflight"][p.fingerprint] = {
        "label": p.label or p.fingerprint,
        "since": datetime.now(UTC).isoformat(),
        "host": _host(),
    }
    stats["reserved_usd"] = round(float(stats.get("reserved_usd") or 0.0) + cap, 4)
    run.stats = stats
    return Work(run.id, p.fingerprint)


def _release(run: CurationRun, fingerprint: str) -> None:
    stats = _stats(run)
    if stats["inflight"].pop(fingerprint, None) is not None:
        cap = get_settings().rule_drafter_max_usd
        stats["reserved_usd"] = max(0.0, round(float(stats.get("reserved_usd") or 0.0) - cap, 4))
    run.stats = stats


def _recover(session: Session, run: CurationRun) -> None:
    """Claims older than the timeout, from a worker that died mid-draft, go back to the queue."""
    limit = get_settings().rule_drafter_timeout_min * 60
    now = datetime.now(UTC)
    for fp, claim_ in list(((run.stats or {}).get("inflight") or {}).items()):
        if (now - datetime.fromisoformat(claim_["since"])).total_seconds() < limit:
            continue
        log.warning("curation %s: %s was never finished; back to the queue", run.id, fp)
        p = session.scalar(select(CoveragePattern).where(CoveragePattern.fingerprint == fp))
        if p is not None and p.state == PatternState.DRAFTING:
            p.state = PatternState.QUEUED
        _release(run, fp)


def _finish(session: Session, run: CurationRun) -> None:
    """Close a run: what it didn't get to goes back to open, for the next run."""
    stats = _stats(run)
    queue = stats.get("queue") or []
    left = 0
    if queue:
        for p in session.scalars(
            select(CoveragePattern).where(
                CoveragePattern.fingerprint.in_(queue),
                CoveragePattern.state.in_((PatternState.QUEUED, PatternState.DRAFTING)),
            )
        ):
            p.state, p.note = PatternState.OPEN, None
            left += 1
    if left:
        stats["left_for_next_run"] = left
    stats.pop("inflight", None)
    stats.pop("reserved_usd", None)
    run.stats = stats
    run.status = "failed" if run.error else "succeeded"
    run.finished_at = datetime.now(UTC)


def _siblings(session: Session, run: CurationRun, p: CoveragePattern) -> list[str]:
    """The labels of the run's other patterns from the same framework, as context."""
    if not p.owner:
        return []
    queue = (run.stats or {}).get("queue") or []
    return [
        q.label
        for q in session.scalars(
            select(CoveragePattern).where(
                CoveragePattern.fingerprint.in_(queue),
                CoveragePattern.owner == p.owner,
                CoveragePattern.fingerprint != p.fingerprint,
            )
        )
    ]


def _host() -> str:
    import socket

    return socket.gethostname()


# ---------------------------------------------------------------- samples


def _reader(session: Session) -> Callable[[str, str], str]:
    """Read a repository's file from the git host at the commit Steno ingested."""

    def read(repository: str, path: str) -> str:
        repo = session.scalar(select(Repository).where(Repository.name == repository))
        if repo is None or not repo.last_ingested_sha:
            raise ValueError(f"no ingested repository {repository!r}")
        connector = session.get_one(Connector, repo.connector_id)
        return read_file(
            repo.clone_url, connector.kind, connector.credentials_ref, repo.last_ingested_sha, path
        )

    return read


def gather(
    session: Session, p: CoveragePattern, read: Callable[[str, str], str]
) -> tuple[list[Sample], dict[str, str]]:
    """Up to SAMPLES occurrences of the pattern, taken from as many repositories as possible,
    each with the code around it; and the files themselves (for the kit)."""
    jobs = {job.id: repo_id for repo_id, (job, _) in latest_runs(session).items()}
    signal, _, target = p.fingerprint.partition(":")
    by_repo: dict[int, list[dict[str, Any]]] = {}
    for it in session.scalars(
        select(CoverageItem).where(
            CoverageItem.job_id.in_(jobs),
            CoverageItem.signal == signal,
            CoverageItem.target_symbol == target,
        )
    ):
        by_repo.setdefault(jobs[it.job_id], []).extend(it.samples or [])
    names = {r.id: r.name for r in session.scalars(select(Repository))}
    picked: list[tuple[str, dict[str, Any]]] = []
    while len(picked) < SAMPLES and any(by_repo.values()):
        for repo_id in list(by_repo):  # round-robin over repositories, for variety
            if by_repo[repo_id] and len(picked) < SAMPLES:
                picked.append((names.get(repo_id, "?"), by_repo[repo_id].pop(0)))
    samples, files = [], {}
    for repo, s in picked:
        path = s.get("file")
        if not path:
            continue
        try:
            text = read(repo, path)
        except Exception as exc:  # noqa: BLE001 - a sample we can't read is just skipped
            log.warning("curation: can't read %s:%s: %s", repo, path, exc)
            continue
        files[f"{repo}/{path}"] = text
        line = s.get("line") or find_line(text, target)
        samples.append(Sample(repo, path, line, s.get("function"), _around(text, line)))
    return samples, files


def find_line(text: str, target: str) -> int | None:
    """The first line that mentions what a pattern is about, for a sample recorded without
    one (a library's import, a call): its full name, else its last part (`psycopg2.connect`,
    then `connect`). None when it's a file-level pattern or isn't named in the file."""
    names = [target.split("#")[0], target.replace("#", ".").rsplit(".", 1)[-1]]
    for name in dict.fromkeys(n for n in names if len(n) >= 3 and "@" not in n):
        for n, line in enumerate(text.splitlines(), start=1):
            if name in line:
                return n
    return None


def _around(text: str, line: int | None) -> str:
    lines = text.splitlines()
    start = max(1, (line or 1) - CONTEXT)
    end = min(len(lines), (line or 1) + CONTEXT if line else 2 * CONTEXT)
    return "\n".join(f"{n:5d}  {lines[n - 1]}" for n in range(start, end + 1))


# ---------------------------------------------------------------- proposing


def _existing_rule(
    p: CoveragePattern, samples: list[Sample], files: dict[str, str]
) -> RuleProposal | None:
    """An existing rule that matches a sample at its own line: the pack wasn't enabled (or the
    version isn't one it's tested on) where the pattern occurs. Proposed as "enable it"."""
    if not files or p.signal in ("unread_file", "unread_config"):
        return None
    try:
        matches = kit.match_existing(files)
    except Exception as exc:  # noqa: BLE001 - the drafter still gets its turn
        log.warning("curation: match_existing failed for %s: %s", p.fingerprint, exc)
        return None
    lines = {(f"{s.repository}/{s.file}", s.line) for s in samples if s.line}
    hits = [m for m in matches if (m["file"], m["line"]) in lines]
    if not hits:
        return None
    packs = sorted({m["pack"] for m in hits})
    return RuleProposal(
        kind=ProposalKind.ENABLE_PACK,
        status=ProposalStatus.READY,
        fingerprints=[p.fingerprint],
        owner=p.owner,
        title=f"Enable {', '.join(packs)} for {p.label}",
        summary=(
            f"{', '.join(sorted({m['rule'] for m in hits}))} already match these samples: the "
            "pack isn't enabled where the pattern occurs, or the library version isn't one it's "
            "tested on."
        ),
        target_pack=packs[0],
        samples=[_sample_json(s) for s in samples],
        drafter="kit",
    )


def _draft(
    drafter: Drafter,
    p: CoveragePattern,
    samples: list[Sample],
    siblings: list[str],
    read: Callable[[str, str], str],
) -> RuleProposal:
    request = DraftRequest(
        fingerprint=p.fingerprint,
        signal=p.signal,
        label=p.label,
        owner=p.owner,
        reach={
            "repositories": p.repositories,
            "applications": p.applications,
            "occurrences": p.occurrences,
        },  # fmt: skip
        samples=samples,
        siblings=siblings,
        language=_language(samples),
    )
    try:
        result = drafter.draft(request, read)
    except (NotImplementedError, DrafterUnavailable):
        raise
    except Exception as exc:  # noqa: BLE001 - recorded, and the run goes on
        log.exception("curation: drafting %s failed", p.fingerprint)
        return RuleProposal(
            kind=ProposalKind.RULE, status=ProposalStatus.FAILED, fingerprints=[p.fingerprint],
            owner=p.owner, title=p.label, summary=f"the drafter failed: {exc}",
            samples=[_sample_json(s) for s in samples], drafter=drafter.name,
        )  # fmt: skip
    kind = {
        "rule": ProposalKind.RULE,
        "ignore": ProposalKind.IGNORE,
        "vocabulary": ProposalKind.VOCABULARY,
    }.get(result.kind, ProposalKind.RULE)
    status = ProposalStatus.NEEDS_PERSON if result.kind == "needs_person" else ProposalStatus.READY
    if result.error and result.kind == "needs_person":
        status = ProposalStatus.FAILED if "submitted nothing" not in result.summary else status
    flags = [f"drafter: {result.error}"] if result.error else []
    return RuleProposal(
        kind=kind,
        status=status,
        fingerprints=[p.fingerprint],
        owner=p.owner,
        title=p.label,
        name=result.title or None,
        summary=result.summary,
        facet=result.facet,
        rule=result.rule,
        tests=result.cases,
        target_pack=(result.pack or {}).get("name"),
        pack=result.pack or {},
        proof=result.proof,
        samples=[_sample_json(s) for s in samples],
        flags=flags + ([f"notes: {result.notes}"] if result.notes else []),
        drafter=drafter.name,
        cost_usd=result.cost_usd,
        turns=result.turns,
    )


def _state_after(proposal: RuleProposal) -> PatternState:
    if proposal.status == ProposalStatus.READY:
        if proposal.kind == ProposalKind.VOCABULARY:
            return PatternState.BLOCKED_VOCABULARY
        return PatternState.PROPOSED
    if proposal.status == ProposalStatus.FAILED:
        return PatternState.OPEN  # the drafter failed, not the pattern: try again later
    return PatternState.NEEDS_PERSON


def _language(samples: list[Sample]) -> str:
    suffixes = {s.file.rsplit(".", 1)[-1] for s in samples if "." in s.file}
    return "python" if suffixes == {"py"} else "java"


def _sample_json(s: Sample) -> dict[str, Any]:
    return {"repository": s.repository, "file": s.file, "line": s.line, "function": s.function}
