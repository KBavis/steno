"""The Java resolver's memory within a job (D127): large repositories start in batches, and a
run out of memory retries once in the same job instead of failing until the next run."""

import contextvars
from types import SimpleNamespace

import pytest

from steno.ingestion import pipeline
from steno.ingestion.resources import Machine
from steno.resolvers.java import JVM_BATCHED, JVM_HEAP_MB


def _machine(available_mb=20000, memory_mb=32000):
    return Machine(memory_mb=memory_mb, available_mb=available_mb, cpus=8)


def _ctx(tmp_path, java_files):
    for i in range(java_files):
        (tmp_path / f"A{i}.java").write_text("class A {}\n")
    return SimpleNamespace(workspace=tmp_path, job=SimpleNamespace(id=1, stats={}))


def test_a_large_repository_resolves_in_batches_from_its_first_run(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "machine", lambda: _machine())
    monkeypatch.setattr(pipeline.get_settings(), "resolver_jvm_batch_above_files", 3)
    big = contextvars.copy_context().run(pipeline._plan_java, _ctx(tmp_path, 4))
    assert big["batched"] and big["java_files"] == 4

    small = tmp_path / "small"
    small.mkdir()
    plan = contextvars.copy_context().run(pipeline._plan_java, _ctx(small, 2))
    assert not plan["batched"]


def test_out_of_memory_retries_in_batches_with_more_heap(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "machine", lambda: _machine())
    monkeypatch.setattr(pipeline, "budget_mb", lambda m: 9000)
    ctx = _ctx(tmp_path, 0)

    def run():
        plan = {"heap_mb": 2048, "batched": False, "retried": False}
        assert pipeline._more_memory(ctx, plan)
        return plan, JVM_BATCHED.get(), JVM_HEAP_MB.get()

    plan, batched, heap = contextvars.copy_context().run(run)
    assert batched and heap == 4096 and plan["retried"]
    # The next run starts from it
    assert ctx.job.stats["resources"] == {"jvm_heap_mb": 4096, "batched": True}

    # Already batched at the most this machine allows: nothing more to try
    capped = {"heap_mb": 9000 - pipeline.get_settings().job_memory_default_mb, "batched": True}
    assert not contextvars.copy_context().run(pipeline._more_memory, ctx, capped)

    # Doubling is capped by the machine: the rest of the job keeps its own memory
    monkeypatch.setattr(pipeline, "budget_mb", lambda m: 6000)
    plan = {"heap_mb": 2048, "batched": False, "retried": False}
    contextvars.copy_context().run(pipeline._more_memory, ctx, plan)
    assert plan["heap_mb"] == 6000 - pipeline.get_settings().job_memory_default_mb


def test_a_heap_that_does_not_fit_the_free_memory_is_cut_and_batched(tmp_path, monkeypatch):
    # 4,000 MB free less 768 headroom and 512 for the worker, over the JVM's 1.2x: 2,266 MB
    monkeypatch.setattr(pipeline, "machine", lambda: _machine(available_mb=4000))
    ctx = _ctx(tmp_path, 1)
    ctx.job.stats = {"resources": {"jvm_floor_mb": 1024}}

    def plan():
        JVM_HEAP_MB.set(4096)
        plan = pipeline._plan_java(ctx)
        return plan, JVM_HEAP_MB.get(), JVM_BATCHED.get()

    plan, heap, batched = contextvars.copy_context().run(plan)
    assert plan["cut_to_fit"] and plan["heap_mb"] == heap == 2266 and batched and plan["batched"]


def test_a_heap_that_fits_is_left_alone(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "machine", lambda: _machine(available_mb=9000))
    plan = contextvars.copy_context().run(pipeline._plan_java, _ctx(tmp_path, 1))
    assert "cut_to_fit" not in plan and plan["heap_mb"] == 2048


def test_no_memory_to_spare_waits_then_says_what_was_needed(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "machine", lambda: _machine(available_mb=1500))
    monkeypatch.setattr(pipeline.get_settings(), "memory_wait_minutes", 0)
    with pytest.raises(pipeline.NotEnoughMemory) as caught:
        contextvars.copy_context().run(pipeline._plan_java, _ctx(tmp_path, 1))
    assert "1024 MB" in str(caught.value) and "732 MB is free" in str(caught.value)


def test_a_repository_that_needs_more_than_is_free_waits_instead_of_running_out(
    tmp_path, monkeypatch
):
    # Its live heap last time was 2,500 MB: a 2,000 MB heap would only run out again
    monkeypatch.setattr(pipeline, "machine", lambda: _machine(available_mb=4000))
    monkeypatch.setattr(pipeline.get_settings(), "memory_wait_minutes", 0)
    ctx = _ctx(tmp_path, 1)
    ctx.job.stats = {"resources": {"jvm_floor_mb": 2750}}

    def plan():
        JVM_HEAP_MB.set(4096)  # what the claim planned: 1.5 times its live heap, so above the floor
        return pipeline._plan_java(ctx)

    with pytest.raises(pipeline.NotEnoughMemory):
        contextvars.copy_context().run(plan)


def test_the_retry_after_running_out_is_bounded_by_the_free_memory(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "budget_mb", lambda m: 20000)
    ctx = _ctx(tmp_path, 0)

    def retry(available):
        monkeypatch.setattr(pipeline, "machine", lambda: _machine(available_mb=available))
        plan = {"heap_mb": 2048, "batched": False, "retried": False}
        return contextvars.copy_context().run(pipeline._more_memory, ctx, plan), plan["heap_mb"]

    assert retry(20000) == (True, 4096)  # room for double
    more, heap = retry(5000)  # (5000 - 768 - 512) / 1.2 = 3100: less than double, more than before
    assert more and heap == 3100
    assert (
        retry(3000)[0] is False
    )  # (3000 - 768 - 512) / 1.2 = 1433: less than the heap that failed


def test_a_workspace_emptied_under_the_job_fails_the_parse_instead_of_looking_empty(tmp_path):
    ctx = SimpleNamespace(
        workspace=tmp_path, job=SimpleNamespace(id=1, stats={}), state={"clone_files": 3}
    )
    with pytest.raises(RuntimeError, match="the workspace lost its files: the clone had 3"):
        contextvars.copy_context().run(pipeline.parse, ctx)
    ctx.state = {"clone_files": 0}  # a repository that really has nothing in it is still fine
    assert contextvars.copy_context().run(pipeline.parse, ctx)["files"] == 0
