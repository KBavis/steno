"""Coverage items become patterns across the organization (D121, D122, D123).

Each run's coverage items are folded into `coverage_pattern` (one row per fingerprint: what's
unexplained, named by what it is) and `coverage_occurrence` (one row per place it occurs, in
each repository's latest run). Reach is counted from the occurrences, so it's the current
state, never a running total: re-reading old code adds nothing, and new code is exactly the
occurrences a run saw first. Everything here is deterministic; no LLM is involved.
"""

import math
from collections import defaultdict
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from steno.coverage.collect import Item
from steno.coverage.runs import latest_runs
from steno.db.models import (
    CoverageItem,
    CoverageKind,
    CoverageOccurrence,
    CoveragePattern,
    CoverageStatus,
    PatternState,
)

# How much a pattern of each signal can change the map (D101, D122): hooks and I/O first
WEIGHT = {
    "unknown_hook": 3.0,
    "external_call": 3.0,
    "disagreement": 2.5,
    "inferred_entry": 2.0,
    "unreachable_effect": 2.0,
    "library": 2.0,
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
# States a pattern leaves when its occurrences are gone (a rule explains it now)
OPEN_STATES = {
    PatternState.OPEN,
    PatternState.QUEUED,
    PatternState.DRAFTING,
    PatternState.PROPOSED,
    PatternState.NEEDS_PERSON,
}
# Signals about the application's own code, which no library owns
OWN_CODE = {
    "inferred_entry", "unreachable_effect", "unknown_host", "dropped_match", "unparsed",
    "unread_file",
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
            )
            session.add(p)
            patterns[fp] = p
            created += 1
        else:
            p.label = it.label
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
    for fp, p in patterns.items():
        if statuses.get(fp) == CoverageStatus.IGNORED and p.state == PatternState.OPEN:
            p.state = PatternState.IGNORED
            p.note = "ignored in the coverage report"
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
        if p.occurrences == 0 and p.state in OPEN_STATES:
            p.state = PatternState.COVERED
            p.note = "no longer occurs: a rule explains it, or the code is gone"
        elif p.occurrences and p.state in (PatternState.COVERED, PatternState.APPROVED):
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


# What a person may set by triage; the other states belong to rule curation
TRIAGE = {PatternState.IGNORED, PatternState.OPEN}


def triage(
    session: Session, fingerprints: list[str], state: PatternState, note: str | None = None
) -> int:
    """Ignore or reopen whole patterns (D122), in bulk, with why. The same items in every
    repository's latest coverage run follow, so the report and the patterns agree; later runs
    carry the decision over. Returns how many patterns changed."""
    if state not in TRIAGE:
        raise ValueError(f"triage sets {sorted(s.value for s in TRIAGE)}, not {state.value}")
    patterns = list(
        session.scalars(
            select(CoveragePattern).where(CoveragePattern.fingerprint.in_(fingerprints))
        )
    )
    status = CoverageStatus.IGNORED if state == PatternState.IGNORED else CoverageStatus.UNEXPLAINED
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
