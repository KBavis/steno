"""How much work this machine can take at once (D128).

A worker reads its machine when it claims a job: the memory limit (a container's cgroup limit
when there is one, else physical memory), the memory available right now, and the CPUs it may
use. Each job reserves the memory its repository needed last time (measured per stage, D127),
or a default for a first run, and a job is claimed only when its reservation fits what this
machine has left. So the same Steno runs one large job at a time on a laptop and many on a big
worker, without a number to tune per machine.
"""

import os
import socket
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from steno.config import get_settings
from steno.db.models import IngestionJob, IngestionStage, JobStatus, StageName

MEMINFO = Path("/proc/meminfo")
CGROUP = Path("/sys/fs/cgroup")
HEADROOM = 1.25  # a repository grows between runs; reserve more than it needed last time
JVM_HEAP_MIN_MB = 1024
JVM_OVERHEAD = 1.2  # the JVM's resident memory is its heap plus this much more (measured 1.12-1.13)
PYTHON_PARSE_MB = 512  # the worker's own growth while the JVM helper runs, when never measured


@dataclass(frozen=True)
class Machine:
    memory_mb: int  # what this worker may use in all: the container's limit, else physical
    available_mb: int  # free for new work right now
    cpus: int


def host() -> str:
    """This worker's machine, for counting what's running on it."""
    return get_settings().worker_host or socket.gethostname()


def machine(meminfo: Path = MEMINFO, cgroup: Path = CGROUP) -> Machine:
    info = _meminfo(meminfo)
    total = info.get("MemTotal", 0) // 1024
    available = info.get("MemAvailable", info.get("MemFree", 0)) // 1024
    limit = _cgroup_limit_mb(cgroup)
    if limit and (not total or limit < total):
        used = _cgroup_used_mb(cgroup)
        total = limit
        available = min(available, limit - used) if used is not None else min(available, limit)
    return Machine(max(total, 0), max(available, 0), _cpus(cgroup))


def budget_mb(m: Machine) -> int:
    """The memory jobs may reserve on this machine, leaving the rest to the system and the
    API process."""
    return int(m.memory_mb * get_settings().worker_memory_fraction)


def free_mb(m: Machine) -> int:
    """What a new job may use right now: the memory free at this moment, less the headroom the
    rest of the machine keeps (D134). Free memory already excludes what Neo4j, Postgres, and
    the other jobs hold."""
    return max(0, m.available_mb - get_settings().memory_headroom_mb)


def fit_heap(
    want_mb: int, python_mb: int, free: int, floor_mb: int = JVM_HEAP_MIN_MB
) -> int | None:
    """The JVM heap to run with when `free` MB are free and the worker needs `python_mb` more
    alongside it: `want_mb` if that fits, else the largest that does (batches make a smaller
    heap work, D127), None if that is below `floor_mb`: what the repository is known to need
    (a heap below its live heap, or one that already ran out, only fails again)."""
    room = int((free - python_mb) / JVM_OVERHEAD)
    if room >= want_mb:
        return want_mb
    return room if room >= floor_mb else None


@dataclass(frozen=True)
class Plan:
    """What a job reserves and how its JVM helper runs."""

    reserve_mb: int
    jvm_heap_mb: int | None  # None: the configured default
    batched: bool  # resolve Java in batches (D127) whatever the setting says
    source: str
    python_mb: int = PYTHON_PARSE_MB  # the worker's memory beside the JVM helper while it runs
    floor_mb: int = JVM_HEAP_MIN_MB  # the smallest heap worth trying: what the repository needs


def estimate(session: Session, repository_id: int, m: Machine | None = None) -> Plan:
    """What a job for this repository should reserve and the JVM heap it should get: from its
    last successful run, with headroom. When its last run ran out of JVM memory, the next gets
    twice the heap (within the machine's budget) and resolves in batches, so a repository too
    large for the defaults recovers instead of failing every night. Then cut to what is free
    on this machine now, if that's less (D134)."""
    return fit_plan(_history_plan(session, repository_id, m), m)


def fit_plan(plan: Plan, m: Machine | None) -> Plan:
    """The plan, with a smaller JVM heap (and batches) if the memory free now can't take it. If
    not even the smallest heap fits, the plan is unchanged and `fits` says no: the job waits."""
    if m is None or plan.reserve_mb <= free_mb(m):
        return plan
    free = free_mb(m)
    want = plan.jvm_heap_mb or size_mb(get_settings().resolver_jvm_max_heap)
    heap = fit_heap(want, plan.python_mb, free, plan.floor_mb)
    if heap is None or heap >= want:
        return plan
    return replace(
        plan,
        reserve_mb=min(plan.reserve_mb, free),
        jvm_heap_mb=heap,
        batched=True,
        source=f"{plan.source}; heap cut from {want} to {heap} MB to fit the {free} MB free now",
    )


def _history_plan(session: Session, repository_id: int, m: Machine | None) -> Plan:
    settings = get_settings()
    oom_heap = _last_oom_heap(session, repository_id)
    if oom_heap is not None:
        heap = oom_heap * 2
        if m is not None:
            heap = min(heap, max(JVM_HEAP_MIN_MB, budget_mb(m) - settings.job_memory_default_mb))
        reserve = heap + settings.job_memory_default_mb
        return Plan(
            reserve, heap, True, f"last run ran out of JVM memory at {oom_heap} MB",
            floor_mb=oom_heap,
        )  # fmt: skip
    peak, jvm_rss, jvm_live = _last_peaks(session, repository_id)
    if peak is None:
        return Plan(settings.job_memory_default_mb, None, False, "default: no earlier run measured")
    parse_rss = _parse_rss(session, repository_id)
    need = max(peak, (jvm_rss or 0) + parse_rss)
    heap = max(JVM_HEAP_MIN_MB, int(jvm_live * 1.5)) if jvm_live else None
    return Plan(
        int(need * HEADROOM),
        heap,
        False,
        "the repository's last successful run",
        python_mb=max(parse_rss, PYTHON_PARSE_MB),
        floor_mb=max(JVM_HEAP_MIN_MB, int((jvm_live or 0) * 1.1)),
    )


def reserved_mb(session: Session, on_host: str) -> tuple[int, int]:
    """The memory reserved by jobs running on this machine, and how many there are."""
    rows = session.execute(
        select(IngestionJob.memory_reserved_mb).where(
            IngestionJob.status == JobStatus.RUNNING, IngestionJob.host == on_host
        )
    ).all()
    return sum(r[0] or 0 for r in rows), len(rows)


def fits(need_mb: int, m: Machine, reserved: int, running: int) -> bool:
    """Whether a job needing `need_mb` may start now: it must fit the memory free right now
    (less the headroom), even alone on the machine, so a worker never starts what would push
    the machine into swap or out of memory (D134). With other jobs running here it must also fit
    the budget left after their reservations, and there is one job per CPU."""
    if need_mb > free_mb(m):
        return False
    if running == 0:
        return True
    if running >= m.cpus:
        return False
    return reserved + need_mb <= budget_mb(m)


def can_never_fit(need_mb: int, m: Machine) -> bool:
    """A job that doesn't fit this machine's whole budget even when nothing else runs: waiting
    won't help, so it fails with a message instead of staying queued forever (D134)."""
    return need_mb > budget_mb(m)


# ------------------------------------------------------------------- reading


def _last_oom_heap(session: Session, repository_id: int) -> int | None:
    """The heap (MB) the repository's latest finished job had, if it ran out of JVM memory."""
    job = session.scalar(
        select(IngestionJob)
        .where(
            IngestionJob.repository_id == repository_id,
            IngestionJob.status.in_([JobStatus.SUCCEEDED, JobStatus.FAILED]),
        )
        .order_by(IngestionJob.id.desc())
        .limit(1)
    )
    if job is None or job.status != JobStatus.FAILED or "OutOfMemoryError" not in (job.error or ""):
        return None
    heap = ((job.stats or {}).get("resources") or {}).get("jvm_heap_mb")
    return heap if isinstance(heap, int) else size_mb(get_settings().resolver_jvm_max_heap)


def size_mb(size: str) -> int:
    """A JVM size (`2g`, `1536m`) in MB."""
    size = size.strip().lower()
    if size.endswith("g"):
        return int(float(size[:-1]) * 1024)
    if size.endswith("m"):
        return int(float(size[:-1]))
    return int(size) // (1024 * 1024)


def _last_peaks(session: Session, repository_id: int) -> tuple[int | None, int | None, int | None]:
    """From the repository's last successful job: the worker's peak memory over its stages,
    and the JVM helper's peak resident memory and live heap."""
    job_id = session.scalar(
        select(IngestionJob.id)
        .where(
            IngestionJob.repository_id == repository_id,
            IngestionJob.status == JobStatus.SUCCEEDED,
        )
        .order_by(IngestionJob.id.desc())
        .limit(1)
    )
    if job_id is None:
        return None, None, None
    peak, jvm_rss, jvm_live = None, None, None
    for stage in session.scalars(select(IngestionStage).where(IngestionStage.job_id == job_id)):
        m = stage.metrics or {}
        if isinstance(m.get("rss_peak_mb"), int):
            peak = max(peak or 0, m["rss_peak_mb"])
        java = _java_memory(m)
        if java:
            jvm_rss = java.get("rss_peak_mb")
            jvm_live = java.get("heap_live_peak_mb")
    return peak, jvm_rss, jvm_live


def _parse_rss(session: Session, repository_id: int) -> int:
    """The worker's memory while the JVM helper ran (the parse stage): the two add up."""
    stage = session.scalar(
        select(IngestionStage)
        .join(IngestionJob, IngestionJob.id == IngestionStage.job_id)
        .where(
            IngestionJob.repository_id == repository_id,
            IngestionJob.status == JobStatus.SUCCEEDED,
            IngestionStage.stage == StageName.PARSE,
        )
        .order_by(IngestionJob.id.desc())
        .limit(1)
    )
    value = (stage.metrics or {}).get("rss_peak_mb") if stage else None
    return value if isinstance(value, int) else 0


def _java_memory(metrics: dict[str, Any]) -> dict[str, Any] | None:
    resolvers = metrics.get("resolvers")
    java = resolvers.get("java") if isinstance(resolvers, dict) else None
    memory = java.get("memory") if isinstance(java, dict) else None
    return memory if isinstance(memory, dict) else None


def _meminfo(path: Path) -> dict[str, int]:
    """/proc/meminfo in kB."""
    out: dict[str, int] = {}
    try:
        for line in path.read_text().splitlines():
            key, _, rest = line.partition(":")
            parts = rest.split()
            if parts and parts[0].isdigit():
                out[key] = int(parts[0])
    except OSError:
        pass
    return out


def _cgroup_limit_mb(root: Path) -> int | None:
    """A container's memory limit: cgroup v2 `memory.max`, or v1 `memory.limit_in_bytes`."""
    for name in ("memory.max", "memory/memory.limit_in_bytes"):
        text = _read(root / name)
        if text and text.isdigit():
            value = int(text) // (1024 * 1024)
            if value < 1 << 40:  # v1 reports "no limit" as a huge number
                return value
    return None


def _cgroup_used_mb(root: Path) -> int | None:
    for name in ("memory.current", "memory/memory.usage_in_bytes"):
        text = _read(root / name)
        if text and text.isdigit():
            return int(text) // (1024 * 1024)
    return None


def _cpus(root: Path) -> int:
    """CPUs this process may use: its affinity, lowered by a container's CPU quota."""
    try:
        cpus = len(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        cpus = os.cpu_count() or 1
    quota = _read(root / "cpu.max")  # v2: "<quota> <period>" or "max <period>"
    if quota:
        parts = quota.split()
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit() and int(parts[1]):
            cpus = min(cpus, max(1, int(parts[0]) // int(parts[1])))
    return max(cpus, 1)


def _read(path: Path) -> str | None:
    try:
        return path.read_text().strip()
    except OSError:
        return None
