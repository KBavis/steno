"""Calls through base classes: an inherited method runs as the subclass it was called on, and a
call to an abstract method whose implementation is chosen at runtime goes to every candidate."""

import textwrap
from pathlib import Path

from steno.config import get_settings
from steno.graph.build import Context, build
from steno.graph.structure import detect
from steno.ingestion.local import run_folder
from steno.rule_packs.packs import is_enabled, load_packs

FILES = {
    "requirements.txt": "fastapi\n",
    "app/tasks.py": """
        from abc import ABC, abstractmethod

        class Task(ABC):
            async def run(self):
                await self.execute()

            @abstractmethod
            async def execute(self): ...

        class DiffTask(Task):
            async def execute(self):
                diff_work()

        class EmbedTask(Task):
            async def execute(self):
                embed_work()

        def diff_work(): ...
        def embed_work(): ...
    """,
    "app/providers.py": """
        class Provider:
            def fetch(self):
                raise NotImplementedError

            @staticmethod
            def for_kind(kind) -> "Provider":
                return GitHub() if kind == "github" else Jira()

        class GitHub(Provider):
            def fetch(self):
                return "github"

        class Jira(Provider):
            def fetch(self):
                return "jira"
    """,
    "app/api.py": """
        from fastapi import APIRouter
        from app.tasks import DiffTask, EmbedTask
        from app.providers import Provider

        router = APIRouter()

        @router.post("/run")
        async def run_all(kind: str):
            await DiffTask().run()
            await EmbedTask().run()
            Provider.for_kind(kind).fetch()
    """,
}


def _plan(tmp_path: Path):
    for rel, text in FILES.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(textwrap.dedent(text))
    packs = [p for p in load_packs(get_settings().rule_packs_dir) if is_enabled(p, tmp_path)]
    engine = run_folder(tmp_path, packs)
    x = engine.out
    structure = detect(tmp_path, {ep.origin.file for ep in x.entry_points})
    return build(x, engine.resolver, structure, Context("demo", None, None, 1, "abc"))


def _invokes(plan, src: str) -> list[tuple[int, str, bool]]:
    out = [
        (e.props.get("seq", 0), e.dst, bool(e.props.get("candidate")))
        for e in plan.edges.values()
        if e.type == "INVOKES" and e.src == src
    ]
    return sorted(out)


def test_inherited_method_runs_as_each_subclass_in_order(tmp_path: Path):
    plan = _plan(tmp_path)
    diff_run = "fn:demo:app.tasks.Task.run@app.tasks.DiffTask"
    embed_run = "fn:demo:app.tasks.Task.run@app.tasks.EmbedTask"
    calls = [dst for _, dst, _ in _invokes(plan, "fn:demo:app.api.run_all")]
    assert calls.index(diff_run) < calls.index(embed_run)
    # Each copy calls its own subclass's execute, not the abstract one
    assert [d for _, d, _ in _invokes(plan, diff_run)] == ["fn:demo:app.tasks.DiffTask.execute"]
    assert [d for _, d, _ in _invokes(plan, embed_run)] == ["fn:demo:app.tasks.EmbedTask.execute"]
    assert plan.nodes[diff_run].props["symbol"] == "app.tasks.Task.run"
    assert plan.nodes[diff_run].props["bound_to"] == "app.tasks.DiffTask"
    assert "fn:demo:app.tasks.diff_work" in {
        d for _, d, _ in _invokes(plan, "fn:demo:app.tasks.DiffTask.execute")
    }


def test_abstract_call_chosen_at_runtime_goes_to_every_candidate(tmp_path: Path):
    plan = _plan(tmp_path)
    fetches = {
        (d, cand) for _, d, cand in _invokes(plan, "fn:demo:app.api.run_all") if d.endswith("fetch")
    }
    assert fetches == {
        ("fn:demo:app.providers.GitHub.fetch", True),
        ("fn:demo:app.providers.Jira.fetch", True),
    }


def test_no_copy_when_the_subclass_changes_nothing(tmp_path: Path):
    plan = _plan(tmp_path)
    assert not any("@" in n for n in plan.nodes if "Provider.for_kind" in n)
