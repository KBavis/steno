"""The graph plan: architecture nodes, code nodes, and each flow's trace and steps (D60, D63)."""

import json
from pathlib import Path
from textwrap import dedent

from steno.config import get_settings
from steno.extraction.facts import Extraction, NodeFact, Origin
from steno.graph.build import Context, build
from steno.graph.structure import detect
from steno.ingestion.local import run_folder
from steno.rule_packs.packs import is_enabled, load_packs

CASE = (
    get_settings().rule_packs_dir
    / "python/steno-pack-fastapi/tests/fastapi-background-task/after-response/input"
)

# A small app: an endpoint, a service that writes a table, and a helper called from three
# places (a utility).
APP = {
    "models.py": """
        from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


        class Base(DeclarativeBase):
            pass


        class Project(Base):
            __tablename__ = "project"
            id: Mapped[int] = mapped_column(primary_key=True)
    """,
    "helpers.py": """
        def fmt(value):
            return _strip(str(value))


        def _strip(text):
            return text.strip()
    """,
    "service.py": """
        from sqlalchemy.ext.asyncio import AsyncSession

        from helpers import fmt
        from models import Project


        class ProjectService:
            def __init__(self, db: AsyncSession):
                self.db = db

            def describe(self, name):
                return fmt(name)

            async def create(self, name):
                fmt(name)
                self._check(name)
                self.db.add(Project())
                self._check(name)

            def _check(self, name):
                return fmt(name)
    """,
    "routers.py": """
        from fastapi import APIRouter

        from service import ProjectService

        router = APIRouter(prefix="/projects")
        svc = ProjectService(None)


        @router.post("/")
        async def create_project(name: str):
            svc.describe(name)
            await svc.create(name)
    """,
}
FLOW = "flow:demo:POST /projects/"


def _plan(case: Path = CASE, repo_name: str = "demo"):
    repo = Path(case)
    packs = [p for p in load_packs(get_settings().rule_packs_dir) if is_enabled(p, repo)]
    engine = run_folder(repo, packs)
    x = engine.out
    structure = detect(repo, {ep.origin.file for ep in x.entry_points})
    return build(x, engine.resolver, structure, Context(repo_name, "space:1", "org:1", 7, "abc123"))


def _app(tmp_path: Path) -> Path:
    for name, code in APP.items():
        (tmp_path / name).write_text(dedent(code).lstrip())
    (tmp_path / "requirements.txt").write_text("fastapi\nsqlalchemy\n")
    return tmp_path


def _trace(plan, flow=FLOW):
    return {t["path"]: t for t in json.loads(plan.nodes[flow].props["trace"])}


def test_architecture_and_code_nodes_without_function_nodes():
    plan = _plan()
    edges = {(e.type, e.src, e.dst) for e in plan.edges.values()}
    flow = "flow:demo:POST /jobs/projects/{project_id}"

    assert plan.nodes["app:demo"].labels[0] == "Application"
    assert ("BUILT_FROM", "app:demo", "module:demo:.") in edges
    assert ("STARTS", "endpoint:demo:POST:/jobs/projects/{project_id}", flow) in edges
    assert not any("Function" in n.labels for n in plan.nodes.values())
    assert not any(e.type in ("INVOKES", "ENTRY", "RUNS") for e in plan.edges.values())
    # The background task a rule found is in the trace, called async
    trace = _trace(plan, flow)
    assert trace["1"]["symbol"] == "routers.run_project_jobs"
    assert trace["1.1"]["symbol"] == "services.JobService.run_project_jobs"
    assert trace["1.1"]["async"] is True
    assert plan.nodes[flow].props["trace_files"] == ["routers.py", "services.py"]


def test_trace_lists_every_call_but_never_expands_a_utility(tmp_path):
    trace = _trace(_plan(_app(tmp_path)))
    names = {path: t["name"] for path, t in trace.items()}

    assert names == {
        "1": "create_project",
        "1.1": "describe",
        "1.1.1": "fmt",
        "1.2": "create",
        "1.2.1": "fmt",
        "1.2.2": "_check",
        "1.2.2.1": "fmt",
        "1.2.3": "_check",
    }
    # fmt is called from three places and nothing architectural is below it: a utility,
    # listed at each call site, its own call to _strip never expanded
    assert all(trace[p].get("utility") for p in ("1.1.1", "1.2.1", "1.2.2.1"))
    # _check was expanded the first time; the second call is listed, not expanded again
    assert trace["1.2.3"].get("repeat") is True
    assert trace["1.2"]["effects"][0]["edge"] == "WRITES_TO"
    assert trace["1.2"]["call_line"] == 12


def test_significant_functions_become_steps_with_their_effects(tmp_path):
    plan = _plan(_app(tmp_path))
    edges = {(e.type, e.src, e.dst): e for e in plan.edges.values()}
    root = f"step:{FLOW}:routers.create_project"
    create = f"step:{FLOW}:service.ProjectService.create"
    table = next(n.id for n in plan.nodes.values() if n.labels[0] == "Table")

    steps = {n.id: n.props for n in plan.nodes.values() if n.labels == ["Step"]}
    assert set(steps) == {root, create}
    assert steps[create]["path"] == "1.1"
    assert ("FIRST_STEP", FLOW, root) in edges
    assert ("SUBSTEP", root, create) in edges
    assert ("WRITES_TO", create, table) in edges
    assert edges[("WRITES_TO", FLOW, table)].props["rollup"] is True


def test_org_defined_channels_join_applications_by_name(tmp_path):
    """D61: an Interface label no core rule knows is keyed by its name, not its application."""
    channel = NodeFact(
        ["Interface", "PxChannel"], {"name": "orders"}, Origin("org-send", "org", "a.py", 1)
    )
    structure = detect(tmp_path, set())
    ids = []
    for repo in ("billing", "catalog"):
        plan = build(
            Extraction(nodes=[channel]), None, structure, Context(repo, "space:1", None, 1, "x")
        )
        node = next(n for n in plan.nodes.values() if "PxChannel" in n.labels)
        assert {"Interface", "PxChannel"} <= set(node.labels)
        assert node.props["shared"] is True
        ids.append(node.id)
    assert ids == ["pxchannel:orders", "pxchannel:orders"]


def test_every_fact_records_its_provenance():
    for node in _plan().nodes.values():
        assert node.props["repo"] == "demo"
        assert node.props["ingestion_job"] == 7
        assert node.props["commit"] == "abc123"


def test_unidentified_stores_belong_to_their_own_repository():
    """Two applications in one space whose databases aren't known yet don't share a store."""
    case = (
        get_settings().rule_packs_dir
        / "python/steno-pack-sqlalchemy/tests/sqlalchemy-dml/update-statement/input"
    )
    stores = []
    for repo in ("billing", "catalog"):
        plan = _plan(case, repo)
        stores.append({n.id for n in plan.nodes.values() if "DataStore" in n.labels})
    assert stores[0] and stores[1]
    assert stores[0].isdisjoint(stores[1])
