"""Where HTTP calls go: hosts followed through parameters and attributes, services named by
HTTP signatures, and calls that can't be named kept apart (one placeholder per class)."""

from pathlib import Path
from textwrap import dedent

from steno.config import get_settings
from steno.graph.build import Context, build
from steno.graph.structure import detect
from steno.ingestion.local import run_folder
from steno.rule_packs.packs import is_enabled, load_packs

CLIENTS = """
    import httpx


    class GitHubClient:
        def __init__(self, owner: str):
            self.owner = owner
            self._build_urls()

        def _build_urls(self):
            self.api = f"https://api.github.com/repos/{self.owner}"

        async def contents(self):
            return await self._get(f"{self.api}/contents")

        async def _get(self, url: str):
            async with httpx.AsyncClient() as client:
                return await client.get(url)


    class JiraClient:
        def __init__(self, url: str):
            self.url = url

        async def search(self):
            base = self.url.rstrip("/")
            async with httpx.AsyncClient() as client:
                return await client.post(f"{base}/rest/api/2/search")


    class Downloader:
        async def fetch(self, item: dict):
            async with httpx.AsyncClient() as client:
                return await client.get(item["href"])


    class Uploader:
        async def push(self, item: dict):
            async with httpx.AsyncClient() as client:
                return await client.post(item["href"])
"""


def _plan(tmp_path: Path):
    (tmp_path / "clients.py").write_text(dedent(CLIENTS).lstrip())
    (tmp_path / "requirements.txt").write_text("httpx\n")
    packs = [p for p in load_packs(get_settings().rule_packs_dir) if is_enabled(p, tmp_path)]
    engine = run_folder(tmp_path, packs)
    structure = detect(tmp_path, set())
    ctx = Context("demo", "space:1", "org:1", 1, "x")
    systems = [s for p in packs for s in p.http_systems]
    return build(engine.out, engine.resolver, structure, ctx, systems)


def _systems(plan) -> dict[str, dict]:
    return {n.id: n.props for n in plan.nodes.values() if n.labels[0] == "ExternalSystem"}


def test_a_host_is_followed_through_parameters_and_attributes(tmp_path):
    """The URL reaches the call as a parameter, built from an attribute a helper sets, and the
    host is one of GitHub's own."""
    systems = _systems(_plan(tmp_path))
    assert systems["external:GitHub"]["host"] == "api.github.com"


def test_a_runtime_host_isnt_guessed_from_its_path(tmp_path):
    """`/rest/api/2/search` looks like Jira, but only a host is proof: the call goes to a node
    for the class that makes it, and keeps its URL template."""
    plan = _plan(tmp_path)
    jira = _systems(plan)["external:unresolved:clients.JiraClient"]
    assert jira["name"] == "Unknown host · JiraClient (clients.py)"
    assert "Jira" not in {p["name"] for p in _systems(plan).values()}
    (call,) = [u for u in plan.stats["unmet_joins"] if u["function"] == "clients.JiraClient.search"]
    assert call["url"] == "{base}/rest/api/2/search"


def test_calls_that_cant_be_named_are_not_merged(tmp_path):
    plan = _plan(tmp_path)
    unknown = {k: v for k, v in _systems(plan).items() if k.startswith("external:unresolved:")}
    assert set(unknown) == {
        "external:unresolved:clients.JiraClient",
        "external:unresolved:clients.Downloader",
        "external:unresolved:clients.Uploader",
    }
    assert all(v["stub"] for v in unknown.values())
    assert {u["function"] for u in plan.stats["unmet_joins"]} == {
        "clients.JiraClient.search",
        "clients.Downloader.fetch",
        "clients.Uploader.push",
    }
