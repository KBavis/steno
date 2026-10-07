"""Calls through base classes: an inherited method runs as the subclass it was called on, and a
call to an abstract method whose implementation is chosen at runtime goes to every candidate."""

import json
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


def _trace(plan) -> list[dict]:
    flow = next(n for n in plan.nodes.values() if n.labels[0] == "Flow")
    return json.loads(flow.props["trace"])


def _children(trace: list[dict], path: str) -> list[dict]:
    """The calls a trace entry makes, in order."""
    depth = path.count(".") + 1
    return [t for t in trace if t["path"].startswith(path + ".") and t["path"].count(".") == depth]


def test_inherited_method_runs_as_each_subclass_in_order(tmp_path: Path):
    trace = _trace(_plan(tmp_path))
    calls = _children(trace, "1")
    runs = [(t["symbol"], t.get("bound_to")) for t in calls if t["name"] == "run"]
    assert runs == [
        ("app.tasks.Task.run", "app.tasks.DiffTask"),
        ("app.tasks.Task.run", "app.tasks.EmbedTask"),
    ]
    # Each one calls its own subclass's execute, not the abstract one
    diff_run, embed_run = (t for t in calls if t["name"] == "run")
    assert [t["symbol"] for t in _children(trace, diff_run["path"])] == [
        "app.tasks.DiffTask.execute"
    ]
    assert [t["symbol"] for t in _children(trace, embed_run["path"])] == [
        "app.tasks.EmbedTask.execute"
    ]
    diff_execute = _children(trace, diff_run["path"])[0]
    assert "app.tasks.diff_work" in {t["symbol"] for t in _children(trace, diff_execute["path"])}


def test_abstract_call_chosen_at_runtime_goes_to_every_candidate(tmp_path: Path):
    trace = _trace(_plan(tmp_path))
    fetches = {
        (t["symbol"], bool(t.get("candidate")))
        for t in _children(trace, "1")
        if t["name"] == "fetch"
    }
    assert fetches == {
        ("app.providers.GitHub.fetch", True),
        ("app.providers.Jira.fetch", True),
    }


def test_no_binding_when_the_subclass_changes_nothing(tmp_path: Path):
    trace = _trace(_plan(tmp_path))
    assert not any(t.get("bound_to") for t in trace if "Provider.for_kind" in t["symbol"])
