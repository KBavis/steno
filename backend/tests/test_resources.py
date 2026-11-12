"""Fitting work to the machine: the machine is read at run time, a container's limits
win over the host's, and a job starts only when its memory fits what the machine has left."""

from pathlib import Path
from types import SimpleNamespace

from steno.db.models import JobStatus
from steno.ingestion.queue import _record_wait
from steno.ingestion.resources import (
    Machine,
    Plan,
    budget_mb,
    can_never_fit,
    fit_heap,
    fit_plan,
    fits,
    free_mb,
    machine,
)

MEMINFO = "MemTotal:       16000000 kB\nMemFree:         2000000 kB\nMemAvailable:    8000000 kB\n"


def _meminfo(tmp: Path) -> Path:
    p = tmp / "meminfo"
    p.write_text(MEMINFO)
    return p


def test_physical_memory_without_a_container(tmp_path: Path) -> None:
    m = machine(_meminfo(tmp_path), tmp_path / "no-cgroup")
    assert (m.memory_mb, m.available_mb) == (15625, 7812)
    assert m.cpus >= 1


def test_a_containers_memory_and_cpu_limits_win(tmp_path: Path) -> None:
    cg = tmp_path / "cgroup"
    cg.mkdir()
    (cg / "memory.max").write_text(str(4 * 1024**3))  # 4 GB container on a 16 GB host
    (cg / "memory.current").write_text(str(1024**3))
    (cg / "cpu.max").write_text("200000 100000")  # 2 CPUs
    m = machine(_meminfo(tmp_path), cg)
    assert m.memory_mb == 4096
    assert m.available_mb == 3072  # the container's room, not the host's
    assert m.cpus <= 2


def test_no_limit_means_the_hosts_memory(tmp_path: Path) -> None:
    cg = tmp_path / "cgroup"
    cg.mkdir()
    (cg / "memory.max").write_text("max")
    assert machine(_meminfo(tmp_path), cg).memory_mb == 15625


LAPTOP = Machine(memory_mb=7000, available_mb=4000, cpus=16)


def test_a_job_alone_starts_only_if_it_fits_the_memory_free_now() -> None:
    # 4,000 MB free less the 768 kept back: a job alone is no exception
    assert free_mb(LAPTOP) == 3232
    assert fits(3000, LAPTOP, reserved=0, running=0)
    assert not fits(3300, LAPTOP, reserved=0, running=0)
    assert not fits(9000, LAPTOP, reserved=0, running=0)


def test_a_job_bigger_than_the_whole_budget_can_never_fit() -> None:
    assert can_never_fit(9000, LAPTOP) and not can_never_fit(5000, LAPTOP)


def test_the_jvm_heap_is_cut_to_what_fits_but_not_below_what_the_repository_needs() -> None:
    free = 3232  # (3232 - 512 for the worker) / 1.2 = 2266 MB of heap
    assert fit_heap(2048, 512, free) == 2048  # fits
    assert fit_heap(4096, 512, free) == 2266  # cut
    assert fit_heap(4096, 512, 1500) is None  # under the smallest worth trying (1024)
    assert fit_heap(4096, 512, free, floor_mb=2500) is None  # under what it is known to need


def test_a_plan_that_doesnt_fit_is_cut_and_batched_or_left_to_wait() -> None:
    plan = Plan(reserve_mb=6000, jvm_heap_mb=4096, batched=False, source="last run")
    cut = fit_plan(plan, LAPTOP)
    assert (cut.jvm_heap_mb, cut.batched, cut.reserve_mb) == (2266, True, 3232)
    assert "heap cut from 4096 to 2266" in cut.source
    assert fit_plan(plan, Machine(7000, 1800, 16)) is plan  # nothing fits now: unchanged, waits
    small = Plan(reserve_mb=2000, jvm_heap_mb=1024, batched=False, source="last run")
    assert fit_plan(small, LAPTOP) is small  # already fits


def _job(**more):
    return SimpleNamespace(id=7, host="here", stats={}, status=JobStatus.QUEUED, error=None, **more)


def test_a_waiting_job_says_what_it_needs_and_what_is_free_once() -> None:
    job, plan = _job(), Plan(reserve_mb=6000, jvm_heap_mb=None, batched=False, source="default")
    _record_wait(job, plan, LAPTOP, running_here=1)
    wait = job.stats["resources"]["waiting_for_memory"]
    assert (wait["need_mb"], wait["free_mb"], wait["running_here"]) == (6000, 3232, 1)
    job.stats["resources"]["waiting_for_memory"]["marker"] = True
    _record_wait(
        job, plan, Machine(7000, 4100, 16), running_here=1
    )  # about the same: not rewritten
    assert job.stats["resources"]["waiting_for_memory"].get("marker")
    _record_wait(job, plan, Machine(7000, 5000, 16), running_here=1)  # free memory changed a lot
    assert "marker" not in job.stats["resources"]["waiting_for_memory"]
    assert job.status == JobStatus.QUEUED


def test_a_job_that_can_never_fit_fails_with_a_message_instead_of_waiting_forever() -> None:
    job, plan = _job(), Plan(reserve_mb=9000, jvm_heap_mb=None, batched=False, source="default")
    _record_wait(job, plan, LAPTOP, running_here=0)
    assert job.status == JobStatus.FAILED
    assert "9000 MB" in job.error and "5600 MB of its 7000 MB" in job.error
    assert ".wslconfig" in job.error


def test_a_job_waits_when_it_doesnt_fit_beside_the_running_ones() -> None:
    assert budget_mb(LAPTOP) == 5600
    assert fits(2000, LAPTOP, reserved=3000, running=1)
    assert not fits(3000, LAPTOP, reserved=3000, running=1)  # over the budget
    assert not fits(4500, Machine(20000, 4000, 16), reserved=1000, running=1)  # not free now


def test_no_more_jobs_than_cpus() -> None:
    assert not fits(100, Machine(64000, 60000, 2), reserved=200, running=2)
