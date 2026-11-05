"""Coverage items become patterns across the organization (D121, D122, D123).

Each run's coverage items are folded into `coverage_pattern` (one row per fingerprint: what's
unexplained, named by what it is) and `coverage_occurrence` (one row per place it occurs, in
each repository's latest run). Reach is counted from the occurrences, so it's the current
state, never a running total: re-reading old code adds nothing, and new code is exactly the
occurrences a run saw first. Everything here is deterministic; no LLM is involved.
"""

import math
from collections import defaultdict
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from steno.connectors.git import CloneError, resolve_secret
from steno.connectors.pull_requests import DECLINED, MERGED, UNKNOWN, url_in
from steno.connectors.pull_requests import state_of as pull_request_state
from steno.coverage.collect import Item
from steno.coverage.runs import latest_runs
from steno.db.models import (
    Connector,
    CoverageItem,
    CoverageKind,
    CoverageOccurrence,
    CoveragePattern,
    CoverageStatus,
    IgnoredFile,
    PatternState,
)

# How much a pattern of each signal can change the map (D101, D122, D146): what can hide a
# connection between applications first, then the rest of the surface
WEIGHT = {
    "silent_repository": 4.0,
    "silent_witness": 4.0,
    "one_sided": 3.5,
    "internal_library": 3.0,
    "unknown_hook": 3.0,
    "external_call": 3.0,
    "disagreement": 2.5,
    "inferred_entry": 2.0,
    "no_trigger_application": 2.0,
    "unreachable_effect": 2.0,
    "library": 2.0,
    "external_library": 1.5,
    "file_call": 1.0,
    "unknown_host": 1.5,
    "unmatched_name": 1.5,
    "config_unset": 1.0,
    "dropped_match": 1.0,
    "unparsed": 0.5,
    "version_untested": 0.5,
    "version_unknown": 0.2,
    "unread_file": 0.5,  # low until something says it matters (D122)
    "unread_config": 0.5,
}
# Signals about the application's own code, which no library owns
OWN_CODE = {
    "inferred_entry", "no_trigger_application", "unreachable_effect", "unknown_host",
    "dropped_match", "unparsed", "unread_file", "silent_repository", "one_sided",
}  # fmt: skip


def record(
    session: Session,
    repository_id: int,
    job_id: int,
    items: list[Item],
    statuses: dict[str, CoverageStatus] | None = None,
) -> dict[str, int]:
    """Fold one repository's run into the organization's patterns. `statuses`: each item's
    triage status by fingerprint, so an item someone ignored makes its pattern ignored."""
    statuses = statuses or {}
    by_fp = {it.fingerprint: it for it in items}
    patterns = {
        p.fingerprint: p
        for p in session.scalars(
            select(CoveragePattern).where(CoveragePattern.fingerprint.in_(by_fp))
        )
    } if by_fp else {}  # fmt: skip
    created = 0
    for fp, it in by_fp.items():
        p = patterns.get(fp)
        if p is None:
            p = CoveragePattern(
                fingerprint=fp,
                kind=CoverageKind(it.kind),
                signal=it.signal,
                label=it.label,
                owner=owner_of(it),
                first_seen_job=job_id,
                state=PatternState.OPEN,
                quiet=it.quiet,
            )
            session.add(p)
            patterns[fp] = p
            created += 1
        else:
            p.label = it.label
            p.quiet = it.quiet
        if it.evidence or p.evidence:
            p.evidence = sorted(it.evidence | set(p.evidence or []))
    session.flush()

    # This repository's occurrences: insert what's new, delete what's gone
    existing = {
        (o.pattern_id, o.file, o.symbol): o
        for o in session.scalars(
            select(CoverageOccurrence).where(CoverageOccurrence.repository_id == repository_id)
        )
    }
    current: set[tuple[int, str, str]] = set()
    new = 0
    for fp, it in by_fp.items():
        pid = patterns[fp].id
        for file, symbol, app in it.keys:
            key = (pid, file, symbol)
            if key in current:
                continue
            current.add(key)
            row = existing.get(key)
            if row is None:
                session.add(
                    CoverageOccurrence(
                        pattern_id=pid,
                        repository_id=repository_id,
                        file=file,
                        symbol=symbol,
                        application=app or None,
                        first_seen_job=job_id,
                    )
                )
                new += 1
            elif row.application != (app or None):
                row.application = app or None
    gone = [o for key, o in existing.items() if key not in current]
    if gone:
        ids = [o.id for o in gone]
        session.execute(delete(CoverageOccurrence).where(CoverageOccurrence.id.in_(ids)))
    session.flush()

    touched = {p.id for p in patterns.values()} | {o.pattern_id for o in gone}
    _recount(session, touched)
    # A pattern ignored only for some file names (IgnoredFile) that still shows up has files
    # nobody looked at: the ignored names were filtered out before this, so it's open again
    scoped = set(
        session.scalars(
            select(IgnoredFile.fingerprint).where(IgnoredFile.fingerprint.in_(patterns))
        )
    ) if patterns else set()  # fmt: skip
    for fp in scoped:
        p = patterns[fp]
        if p.state == PatternState.IGNORED and p.occurrences > 0:
            p.state, p.note = PatternState.OPEN, "new files beside the names ignored before"
    for fp, p in patterns.items():
        if statuses.get(fp) == CoverageStatus.IGNORED and p.state == PatternState.OPEN:
            p.state = PatternState.IGNORED
            p.note = "ignored in the coverage report"
        elif statuses.get(fp) == CoverageStatus.EXPECTED and p.state == PatternState.OPEN:
            p.state = PatternState.EXPECTED
            p.note = "expected: outside the organization"
    return {"patterns": len(by_fp), "new_patterns": created, "new_occurrences": new,
            "gone_occurrences": len(gone)}  # fmt: skip


def _recount(session: Session, pattern_ids: set[int]) -> None:
    """Reach from the occurrences, then the score and the state that follows from it."""
    if not pattern_ids:
        return
    counts = {
        row.pattern_id: row
        for row in session.execute(
            select(
                CoverageOccurrence.pattern_id,
                func.count(func.distinct(CoverageOccurrence.repository_id)).label("repos"),
                func.count(func.distinct(CoverageOccurrence.application)).label("apps"),
                func.count().label("occurrences"),
            )
            .where(CoverageOccurrence.pattern_id.in_(pattern_ids))
            .group_by(CoverageOccurrence.pattern_id)
        )
    }
    for p in session.scalars(select(CoveragePattern).where(CoveragePattern.id.in_(pattern_ids))):
        row = counts.get(p.id)
        p.repositories = row.repos if row else 0
        p.applications = row.apps if row else 0
        p.occurrences = row.occurrences if row else 0
        p.score = score(p.signal, p.repositories, p.applications, p.occurrences, p.evidence)
        # Gone: a rule explains it now, or the code is gone (a proposed rule merged);
        # an ignored or expected pattern stays so, since that was decided on purpose
        settled = (PatternState.COVERED, PatternState.IGNORED, PatternState.EXPECTED)
        if p.occurrences == 0 and p.state not in settled:
            p.state = PatternState.COVERED
            p.note = "no longer occurs: a rule explains it, or the code is gone"
        elif p.occurrences and p.state == PatternState.COVERED:
            p.state = PatternState.OPEN
            p.note = "occurs again: the rule doesn't cover these"


def score(
    signal: str,
    repositories: int,
    applications: int,
    occurrences: int,
    evidence: list[str] | None = None,
) -> float:
    """Rank by how much one rule would explain: repositories first, then applications, then
    occurrences, weighted by how much the signal can change the map (D122), and raised by
    each piece of evidence that it matters (the code refers to it)."""
    reach = 100 * repositories + 10 * applications + math.log1p(occurrences)
    return round(WEIGHT.get(signal, 1.0) * reach * (1 + 4 * len(evidence or [])), 2)


def owner_of(it: Item) -> str | None:
    """What a pattern belongs to, for clustering it into a framework (D123): the library a
    call goes into, the package root of an annotation or type, or a config key's prefix.
    None for the application's own code."""
    if it.signal in OWN_CODE:
        return None
    if it.signal == "external_call":
        lib = next((s.get("library") for s in it.samples if s.get("library")), None)
        return lib or package_root(it.target)
    if it.signal in ("library", "version_untested", "version_unknown"):
        return it.target.split(" ")[0]
    if it.signal in ("config_unset", "unread_config"):
        return "config:" + ".".join(it.target.split(".")[:2])
    return package_root(it.target)


def package_root(symbol: str) -> str | None:
    """`com.acme.messaging.AcmeHandler#on(...)` → `com.acme.messaging`: the first three
    package segments (fewer when the package is shorter); None for a bare name."""
    name = symbol.split("#", 1)[0].split("(", 1)[0].lstrip("@")
    parts = [p for p in name.split(".") if p]
    package = [p for p in parts if not p[:1].isupper()]
    if not package:
        return None
    return ".".join(package[:3])


def frameworks(patterns: list[CoveragePattern]) -> list[dict[str, Any]]:
    """Open patterns grouped by owner, ranked by their summed score: the units rule curation
    investigates once each (D123). Patterns no library owns are listed alone."""
    groups: dict[str | None, list[CoveragePattern]] = defaultdict(list)
    for p in patterns:
        groups[p.owner].append(p)
    out = []
    for owner, members in groups.items():
        members.sort(key=lambda p: -p.score)
        if owner is None:
            out.extend({"owner": None, "score": p.score, "patterns": [p]} for p in members)
        else:
            out.append({"owner": owner, "score": sum(p.score for p in members),
                        "patterns": members})  # fmt: skip
    out.sort(key=lambda g: -g["score"])
    return out


# What the rule author or a person may set (D147): ignored (harmless), expected (outside the
# organization), proposed (a rule's pull request is open), or open again. Covered is Steno's
TRIAGE = {PatternState.IGNORED, PatternState.EXPECTED, PatternState.PROPOSED, PatternState.OPEN}
STATUS_OF = {
    PatternState.IGNORED: CoverageStatus.IGNORED,
    PatternState.EXPECTED: CoverageStatus.EXPECTED,
}


def triage(
    session: Session, fingerprints: list[str], state: PatternState, note: str | None = None
) -> int:
    """Set whole patterns' state (D122, D147), in bulk, with why: ignored, expected, proposed,
    or open. The same items in every repository's latest coverage run follow, so the report
    and the patterns agree; later runs carry the decision over. A one-sided join has no items
    (it's read from the graph), so its pattern is created here. Returns how many changed."""
    if state not in TRIAGE:
        raise ValueError(f"triage sets {sorted(s.value for s in TRIAGE)}, not {state.value}")
    patterns = list(
        session.scalars(
            select(CoveragePattern).where(CoveragePattern.fingerprint.in_(fingerprints))
        )
    )
    status = STATUS_OF.get(state, CoverageStatus.UNEXPLAINED)
    known = {p.fingerprint for p in patterns}
    for fp in fingerprints:
        if fp.startswith("one_sided:") and fp not in known:
            p = CoveragePattern(fingerprint=fp, kind=CoverageKind.INCONSISTENCY,
                                signal="one_sided", label=fp, state=state)  # fmt: skip
            session.add(p)
            patterns.append(p)
            known.add(fp)
    jobs = [job.id for job, _ in latest_runs(session).values()]
    for p in patterns:
        p.state = state
        p.note = note
        signal, _, target = p.fingerprint.partition(":")
        for it in session.scalars(
            select(CoverageItem).where(
                CoverageItem.job_id.in_(jobs),
                CoverageItem.signal == signal,
                CoverageItem.target_symbol == target,
            )
        ):
            it.status = status
    return len(patterns)


def refresh_proposed(
    session: Session, repository_id: int, state_of: Callable[[str], str] | None = None
) -> dict[str, int]:
    """Ask the git host what became of the pull requests behind this repository's `proposed`
    patterns (D148). Declined: a person decided against the rule, so the pattern is ignored,
    with why, and stays out of the report. Merged: it's `covered`, and this run decides: if it
    still occurs here it reopens (`_recount`), so a partial rule leaves what it missed visible.
    Open or unknown: nothing changes. Run before this run's items are written, so the decision
    carries into them."""
    lookup = state_of or _pull_request_state(session)
    rows = list(
        session.scalars(
            select(CoveragePattern)
            .join(CoverageOccurrence, CoverageOccurrence.pattern_id == CoveragePattern.id)
            .where(
                CoverageOccurrence.repository_id == repository_id,
                CoveragePattern.state == PatternState.PROPOSED,
            )
            .distinct()
        )
    )
    declined = merged = 0
    for p in rows:
        url = url_in(p.note)
        answer = lookup(url) if url else UNKNOWN
        if answer == DECLINED:
            triage(session, [p.fingerprint], PatternState.IGNORED, f"pull request declined: {url}")
            declined += 1
        elif answer == MERGED:
            p.state, p.note = PatternState.COVERED, f"pull request merged: {url}"
            merged += 1
    return {"proposed_checked": len(rows), "declined": declined, "merged": merged}


def _pull_request_state(session: Session) -> Callable[[str], str]:
    """Reads a pull request's state with the credentials of the connector on its host."""
    tokens: dict[str, str | None] = {}
    for c in session.scalars(select(Connector)):
        host = urlsplit(c.base_url).netloc.rsplit("@", 1)[-1]
        try:
            tokens[host] = resolve_secret(c.credentials_ref)
        except CloneError:
            tokens[host] = None

    def lookup(url: str) -> str:
        return pull_request_state(url, tokens.get(urlsplit(url).netloc.rsplit("@", 1)[-1]))

    return lookup


def occurrences(session: Session, fingerprint: str, limit: int = 50) -> dict[str, Any]:
    """Where a pattern occurs now, for the rule author (D147): each place with its repository,
    file, enclosing symbol, application, and the commit Steno ingested, spread across
    repositories; plus the samples (with lines) from each repository's latest coverage item."""
    from steno.api.links import source_url
    from steno.db.models import Repository

    p = session.scalar(select(CoveragePattern).where(CoveragePattern.fingerprint == fingerprint))
    if p is None:
        raise LookupError(f"no pattern {fingerprint!r}")
    latest = latest_runs(session)
    repos = {r.id: r for r in session.scalars(select(Repository))}
    places: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for o in session.scalars(
        select(CoverageOccurrence)
        .where(CoverageOccurrence.pattern_id == p.id)
        .order_by(CoverageOccurrence.file)
    ):
        repo, job = repos.get(o.repository_id), latest.get(o.repository_id, (None, None))[0]
        places[o.repository_id].append({
            "repository": repo.name if repo else "?",
            "file": o.file, "symbol": o.symbol, "application": o.application,
            "commit": job.to_sha if job else None,
            "url": source_url(repo.clone_url if repo else None, job.to_sha if job else None,
                              o.file, None),
        })  # fmt: skip
    spread: list[dict[str, Any]] = []
    while len(spread) < limit and any(places.values()):
        for rid in list(places):  # round-robin, so one large repository doesn't fill it
            if places[rid] and len(spread) < limit:
                spread.append(places[rid].pop(0))
    signal, _, target = fingerprint.partition(":")
    samples = []
    jobs = {job.id: rid for rid, (job, _) in latest.items()}
    for it in session.scalars(
        select(CoverageItem).where(
            CoverageItem.job_id.in_(jobs),
            CoverageItem.signal == signal,
            CoverageItem.target_symbol == target,
        )
    ):
        repo, job = repos.get(jobs[it.job_id]), latest[jobs[it.job_id]][0]
        for smp in it.samples:
            samples.append({
                **smp, "repository": repo.name if repo else "?",
                "url": source_url(repo.clone_url if repo else None, job.to_sha,
                                  smp.get("file"), smp.get("line")),
            })  # fmt: skip
    return {
        "fingerprint": p.fingerprint, "signal": p.signal, "label": p.label, "owner": p.owner,
        "state": p.state.value, "note": p.note, "repositories": p.repositories,
        "applications": p.applications, "occurrences": p.occurrences,
        "places": spread, "more": max(p.occurrences - len(spread), 0), "samples": samples[:20],
    }  # fmt: skip
