"""The graph plan for a small FastAPI app: architecture nodes, code nodes, and the bridges."""

from pathlib import Path

from steno.config import get_settings
from steno.extractors.engine import Engine
from steno.extractors.packs import is_enabled, load_packs
from steno.extractors.structure import detect
from steno.graph.build import Context, build

CASE = (
    get_settings().rule_packs_dir
    / "python/steno-pack-fastapi/tests/fastapi-background-task/after-response/input"
)


def _plan(case: Path = CASE, repo_name: str = "demo"):
    repo = Path(case)
    packs = [p for p in load_packs(get_settings().rule_packs_dir) if is_enabled(p, repo)]
    engine = Engine(repo, packs)
    x = engine.run()
    structure = detect(repo, {ep.origin.file for ep in x.entry_points})
    return build(x, engine.resolver, structure, Context(repo_name, "space:1", "org:1", 7, "abc123"))


def test_architecture_and_code_nodes_are_joined_by_bridges():
    plan = _plan()
    edges = {(e.type, e.src, e.dst) for e in plan.edges.values()}
    flow = "flow:demo:POST /jobs/projects/{project_id}"
    entry = "fn:demo:routers.run_project_jobs"
    task = "fn:demo:services.JobService.run_project_jobs"

    assert plan.nodes["app:demo"].labels[0] == "Application"
    assert ("BUILT_FROM", "app:demo", "module:demo:.") in edges
    assert ("STARTS", "endpoint:demo:POST:/jobs/projects/{project_id}", flow) in edges
    assert ("ENTRY", flow, entry) in edges
    # The background task is reached, so its function is a code node, linked async
    assert plan.nodes[task].props["reachable"] is True
    invokes = plan.edges[("INVOKES", entry, task)].props
    assert invokes["async"] is True


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
