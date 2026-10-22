"""A rule curation run (D120): turn the top open patterns into proposals a person reviews.

For each pattern, in score order, until the run's budget is spent:

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
from sqlalchemy.orm import Session

from steno.config import get_settings
from steno.connectors.source import read_file
from steno.coverage.patterns import frameworks
from steno.coverage.runs import latest_runs
from steno.curation import kit
from steno.curation.drafters import Drafter, DraftRequest, Sample, get_drafter
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

# Signals a rule could explain; the rest are fixed another way (a parse error, a version)
DRAFTABLE = {
    "unknown_hook",
    "external_call",
    "inferred_entry",
    "unreachable_effect",
    "library",
    "unread_file",
    "unread_config",
}
SAMPLES = 6
CONTEXT = 30  # lines either side of an occurrence


@dataclass
class Picked:
    pattern: CoveragePattern
    siblings: list[str]


def pick(session: Session, limit: int, fingerprints: list[str] | None = None) -> list[Picked]:
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
    return out[:limit]


def curate(
    session: Session,
    limit: int = 5,
    fingerprints: list[str] | None = None,
    drafter: Drafter | None = None,
    budget_usd: float | None = None,
    trigger: str = "manual",
) -> CurationRun:
    """Create a run and execute it now (the command line)."""
    drafter = drafter or get_drafter()
    run = queue_run(session, limit, fingerprints, budget_usd, trigger, drafter.name)
    run.status = "running"
    session.commit()
    return execute(session, run, drafter)


def queue_run(
    session: Session,
    limit: int = 5,
    fingerprints: list[str] | None = None,
    budget_usd: float | None = None,
    trigger: str = "manual",
    drafter: str | None = None,
) -> CurationRun:
    """A run waiting for a worker (the UI's "Curate"): drafting is slow LLM work, so it runs
    in the worker, never in the API process."""
    settings = get_settings()
    run = CurationRun(
        trigger=trigger,
        drafter=drafter or settings.rule_drafter,
        model=settings.rule_drafter_model,
        status="queued",
        budget_usd=budget_usd if budget_usd is not None else settings.curation_budget_usd,
        stats={"params": {"limit": limit, "fingerprints": fingerprints or []}},
    )
    session.add(run)
    session.commit()
    return run


def claim_run(session: Session) -> CurationRun | None:
    """The oldest queued run, marked running; two workers never claim the same one."""
    run = session.scalar(
        select(CurationRun)
        .where(CurationRun.status == "queued")
        .order_by(CurationRun.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if run is not None:
        run.status = "running"
        session.commit()
    return run


def execute(session: Session, run: CurationRun, drafter: Drafter | None = None) -> CurationRun:
    drafter = drafter or get_drafter(run.drafter)
    params = (run.stats or {}).get("params") or {}
    counts: dict[str, int] = {}
    try:
        _work(session, run, drafter, run.budget_usd or 0.0, int(params.get("limit", 5)),
              params.get("fingerprints") or None, counts)  # fmt: skip
    except Exception as exc:
        # Every proposal so far is committed; record why the run stopped
        run.status = "failed"
        run.error = f"{type(exc).__name__}: {exc}"
        run.finished_at = datetime.now(UTC)
        run.stats = {"params": params, **counts}
        session.commit()
        raise
    run.status = "succeeded"
    run.finished_at = datetime.now(UTC)
    run.stats = {"params": params, **counts}
    session.commit()
    return run


def _work(
    session: Session,
    run: CurationRun,
    drafter: Drafter,
    budget: float,
    limit: int,
    fingerprints: list[str] | None,
    counts: dict[str, int],
) -> None:
    reader = _reader(session)
    for picked in pick(session, limit, fingerprints):
        if run.spent_usd >= budget:
            counts["stopped_at_budget"] = 1
            break
        p = picked.pattern
        log.info("curation %s: %s", run.id, p.fingerprint)
        samples, files = gather(session, p, reader)
        proposal = _existing_rule(p, samples, files) or _draft(
            drafter, p, samples, picked.siblings, reader
        )
        proposal.run_id = run.id
        session.add(proposal)
        p.state = _state_after(proposal)
        p.note = f"curation run {run.id}: {proposal.kind.value}, {proposal.status.value}"
        run.spent_usd = round(run.spent_usd + proposal.cost_usd, 4)
        key = f"{proposal.kind.value}:{proposal.status.value}"
        counts[key] = counts.get(key, 0) + 1
        run.stats = {**(run.stats or {}), **counts}  # progress, visible while it runs
        session.commit()


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
        samples.append(
            Sample(repo, path, s.get("line"), s.get("function"), _around(text, s.get("line")))
        )
    return samples, files


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
    except NotImplementedError:
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
