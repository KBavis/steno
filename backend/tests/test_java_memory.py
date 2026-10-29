"""The Java resolver's memory within a job (D127): large repositories start in batches, and a
run out of memory retries once in the same job instead of failing until the next run."""

import contextvars
from types import SimpleNamespace

from steno.ingestion import pipeline
from steno.resolvers.java import JVM_BATCHED, JVM_HEAP_MB


def _ctx(tmp_path, java_files):
    for i in range(java_files):
        (tmp_path / f"A{i}.java").write_text("class A {}\n")
    return SimpleNamespace(workspace=tmp_path, job=SimpleNamespace(id=1, stats={}))


def test_a_large_repository_resolves_in_batches_from_its_first_run(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline.get_settings(), "resolver_jvm_batch_above_files", 3)
    big = contextvars.copy_context().run(pipeline._plan_java, _ctx(tmp_path, 4))
    assert big["batched"] and big["java_files"] == 4

    small = tmp_path / "small"
    small.mkdir()
    plan = contextvars.copy_context().run(pipeline._plan_java, _ctx(small, 2))
    assert not plan["batched"]


def test_out_of_memory_retries_in_batches_with_more_heap(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "machine", lambda: None)
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
