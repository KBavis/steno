"""The coverage report's signals, on a small app with one of each gap."""

from pathlib import Path
from textwrap import dedent

from steno.config import get_settings
from steno.coverage.collect import collect
from steno.graph.build import Context, build
from steno.graph.structure import detect
from steno.ingestion.local import run_folder
from steno.rule_packs.packs import is_enabled, load_packs

APP = {
    "app.py": """
        import httpx
        import redis
        from fastapi import APIRouter

        router = APIRouter()
        cache = redis.Redis()


        class Tenants:
            def __init__(self, url: str):
                self.url = url

            async def ping(self):
                async with httpx.AsyncClient() as client:
                    return await client.get(f"{self.url}/health")


        @router.post("/orders")
        async def create_order(tenant_url: str):
            cache.publish("orders", "created")          # redis: no pack covers it
            return await Tenants(tenant_url).ping()     # host is runtime data


        async def nightly_cleanup():                    # no entry point reaches it
            async with httpx.AsyncClient() as client:
                await client.delete("https://api.example.com/old")
    """,
    "broken.py": "def oops(:\n",
}


def _report(tmp_path: Path):
    for name, code in APP.items():
        (tmp_path / name).write_text(dedent(code).lstrip())
    (tmp_path / "requirements.txt").write_text("fastapi\nhttpx\nredis\n")
    all_packs = load_packs(get_settings().rule_packs_dir)
    packs = [p for p in all_packs if is_enabled(p, tmp_path)]
    engine = run_folder(tmp_path, packs)
    x = engine.out
    structure = detect(tmp_path, {ep.origin.file for ep in x.entry_points})
    plan = build(x, engine.resolver, structure, Context("demo", "space:1", "org:1", 1, "c"))
    report = collect(x, engine.resolver, plan, all_packs)
    return {(i.signal, i.target): i for i in report.items}, report.metrics


def test_each_gap_is_an_item(tmp_path):
    items, _ = _report(tmp_path)
    assert set(items) == {
        ("unknown_host", "app.Tenants"),
        ("external_call", "redis.Redis.publish"),
        ("library", "redis"),
        ("unreachable_effect", "app.nightly_cleanup"),
        ("unparsed", "broken.py"),
    }


def test_unknown_hosts_keep_their_url_template(tmp_path):
    items, _ = _report(tmp_path)
    host = items[("unknown_host", "app.Tenants")]
    assert host.label == "Unknown host · Tenants (app.py)"
    assert host.samples[0]["detail"] == "{url}/health"


def test_completeness_counts_explained_io(tmp_path):
    _, metrics = _report(tmp_path)
    # The two httpx calls are explained by the httpx rule; redis.publish isn't
    assert (metrics["io_calls"], metrics["io_calls_explained"]) == (3, 2)
