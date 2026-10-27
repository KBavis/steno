"""Reading the coverage report back: each repository's latest run, and what's unexplained.

Shared by the Admin API's coverage report and the MCP tools, whose answers carry an
application's unexplained counts so something Steno can see but can't explain is never
silent (D118).
"""

from collections import defaultdict
from typing import Any

from sqlalchemy import func, select

from steno.db.models import (
    CoverageItem,
    CoverageStatus,
    IngestionJob,
    IngestionStage,
    StageName,
    StageStatus,
)

# How each signal reads in an answer (coverage-report.md §1)
SIGNAL_WORDS = {
    "unknown_hook": "framework hooks no rule explains",
    "unknown_host": "calls whose host is set outside the repository",
    "disagreement": "disagreements with other witnesses",
    "unmatched_name": "names other witnesses use that match no application",
    "external_call": "I/O calls no rule explains",
    "config_unset": "config keys set outside the repository",
    "library": "libraries no rule pack covers",
    "version_untested": "libraries newer or older than their pack is tested on",
    "version_unknown": "libraries whose version isn't stated",
    "inferred_entry": "work nothing in the code starts",
    "no_trigger_application": "applications with no known trigger (kind unknown)",
    "unreachable_effect": "code with effects no entry point reaches",
    "dropped_match": "rule matches Steno couldn't complete",
    "unparsed": "files Steno couldn't read",
}


def latest_runs(session: Any) -> dict[int, tuple[IngestionJob, IngestionStage]]:
    """Each repository's latest job whose coverage stage succeeded, with that stage."""
    last = (
        select(IngestionJob.repository_id, func.max(IngestionJob.id).label("job_id"))
        .join(IngestionStage, IngestionStage.job_id == IngestionJob.id)
        .where(
            IngestionStage.stage == StageName.COVERAGE,
            IngestionStage.status == StageStatus.SUCCEEDED,
        )
        .group_by(IngestionJob.repository_id)
        .subquery()
    )
    rows = session.execute(
        select(IngestionJob, IngestionStage)
        .join(last, last.c.job_id == IngestionJob.id)
        .join(IngestionStage, IngestionStage.job_id == IngestionJob.id)
        .where(IngestionStage.stage == StageName.COVERAGE)
    ).all()
    return {job.repository_id: (job, stage) for job, stage in rows}


def unexplained(session: Any, applications: list[str]) -> dict[str, dict[str, int]]:
    """For each application, how many open coverage items of each kind its repository's
    latest run found in it: `{"users-svc": {"framework hooks no rule explains": 2}}`.
    Items triaged as ignored or explained aren't counted."""
    wanted = set(applications)
    if not wanted:
        return {}
    jobs = [job.id for job, _ in latest_runs(session).values()]
    if not jobs:
        return {}
    items = session.scalars(
        select(CoverageItem).where(
            CoverageItem.job_id.in_(jobs), CoverageItem.status == CoverageStatus.UNEXPLAINED
        )
    )
    return count_by_application(((it.signal, it.applications or {}) for it in items), wanted)


def count_by_application(items: Any, wanted: set[str]) -> dict[str, dict[str, int]]:
    """Count items per application and signal, in words. `items`: (signal, {app: n})."""
    out: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for signal, apps in items:
        for app in apps:
            if app in wanted:
                out[app][SIGNAL_WORDS.get(signal, signal)] += 1
    return {app: dict(sorted(counts.items())) for app, counts in sorted(out.items())}
