"""Fitting work to the machine (D128): the machine is read at run time, a container's limits
win over the host's, and a job starts only when its memory fits what the machine has left."""

from pathlib import Path

from steno.ingestion.resources import Machine, budget_mb, fits, machine

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


def test_a_job_alone_always_starts_even_when_large() -> None:
    assert fits(9000, LAPTOP, reserved=0, running=0)


def test_a_job_waits_when_it_doesnt_fit_beside_the_running_ones() -> None:
    assert budget_mb(LAPTOP) == 5600
    assert fits(2000, LAPTOP, reserved=3000, running=1)
    assert not fits(3000, LAPTOP, reserved=3000, running=1)  # over the budget
    assert not fits(4500, Machine(20000, 4000, 16), reserved=1000, running=1)  # not free now


def test_no_more_jobs_than_cpus() -> None:
    assert not fits(100, Machine(64000, 60000, 2), reserved=200, running=2)
