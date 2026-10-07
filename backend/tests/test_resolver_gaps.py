"""Calls the call graph used to miss, all ordinary Python: tuple unpacking, a local
reassigned per branch, a method only subclasses define, and a function passed through a
helper's parameter."""

import json
from pathlib import Path
from textwrap import dedent

from steno.config import get_settings
from steno.graph.build import Context, build
from steno.graph.structure import detect
from steno.ingestion.local import run_folder
from steno.rule_packs.packs import is_enabled, load_packs

APP = """
    import asyncio
    from fastapi import APIRouter

    router = APIRouter()


    class Files:
        def save(self):
            return 1


    class Chunks:
        def store(self):
            return 2


    def build_services() -> tuple["Files", "Chunks"]:
        return Files(), Chunks()


    class Ollama:
        def is_available(self):
            return True


    class Azure:
        def is_available(self):
            return True


    class Tracker:
        pass


    class Jira(Tracker):
        def linked_prs(self):
            return []


    class Linear(Tracker):
        def linked_prs(self):
            return []


    def pick_tracker() -> Tracker:
        return Jira()


    def grep_search():
        return 3


    def register(fn):
        return asyncio.to_thread(fn)


    @router.post("/run")
    async def run(kind: str):
        files, chunks = build_services()
        chunks.store()
        match kind:
            case "ollama":
                llm = Ollama()
                llm.is_available()
            case _:
                llm = Azure()
                llm.is_available()
        pick_tracker().linked_prs()
        await register(grep_search)
"""


def _calls(tmp_path: Path) -> list[tuple[str, bool]]:
    (tmp_path / "app.py").write_text(dedent(APP).lstrip())
    (tmp_path / "requirements.txt").write_text("fastapi\n")
    packs = [p for p in load_packs(get_settings().rule_packs_dir) if is_enabled(p, tmp_path)]
    engine = run_folder(tmp_path, packs)
    structure = detect(tmp_path, {ep.origin.file for ep in engine.out.entry_points})
    plan = build(engine.out, engine.resolver, structure, Context("d", None, None, 1, "c"))
    flow = next(n for n in plan.nodes.values() if n.labels[0] == "Flow")
    trace = json.loads(flow.props["trace"])
    return [(t["symbol"], bool(t.get("candidate"))) for t in trace if t["path"].count(".") == 1]


def test_tuple_unpacking_takes_the_annotated_element(tmp_path):
    assert ("app.Chunks.store", False) in _calls(tmp_path)


def test_a_local_has_the_type_of_its_nearest_assignment(tmp_path):
    calls = _calls(tmp_path)
    assert ("app.Ollama.is_available", False) in calls
    assert ("app.Azure.is_available", False) in calls


def test_a_method_only_subclasses_define_links_to_each(tmp_path):
    calls = _calls(tmp_path)
    assert ("app.Jira.linked_prs", True) in calls
    assert ("app.Linear.linked_prs", True) in calls


def test_a_function_passed_through_a_helper_is_reached(tmp_path):
    assert ("app.grep_search", False) in _calls(tmp_path)
