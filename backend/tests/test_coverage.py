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


        async def nightly_cleanup():                    # nothing calls it: an inferred flow
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


def _plan(tmp_path: Path):
    for name, code in APP.items():
        (tmp_path / name).write_text(dedent(code).lstrip())
    (tmp_path / "requirements.txt").write_text("fastapi\nhttpx\nredis\n")
    packs = [p for p in load_packs(get_settings().rule_packs_dir) if is_enabled(p, tmp_path)]
    engine = run_folder(tmp_path, packs)
    structure = detect(tmp_path, {ep.origin.file for ep in engine.out.entry_points})
    ctx = Context("demo", "space:1", "org:1", 1, "c")
    return build(engine.out, engine.resolver, structure, ctx)


def test_uncalled_work_is_an_inferred_flow(tmp_path):
    plan = _plan(tmp_path)
    flows = {n.props["name"]: n for n in plan.nodes.values() if n.labels[0] == "Flow"}
    inferred = flows["Background work · app.nightly_cleanup"]
    assert inferred.props["inferred"] is True and inferred.props["trigger_kind"] == "inferred"
    assert inferred.props["confidence"] == 0.5
    # No trigger is known, so nothing STARTS it
    assert not any(k[0] == "STARTS" and k[2] == inferred.id for k in plan.edges)
    # Called functions (Tenants.ping) and the constructor aren't starting points of their own
    assert set(flows) == {"POST /orders", "Background work · app.nightly_cleanup"}


def test_each_gap_is_an_item(tmp_path):
    items, _ = _report(tmp_path)
    assert set(items) == {
        ("unknown_host", "app.Tenants"),
        ("external_call", "redis.Redis.publish"),
        ("library", "redis"),
        # Nothing in the code starts it, but it does real work: a flow, flagged (D74)
        ("inferred_entry", "app.nightly_cleanup"),
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


def test_unread_files_are_grouped_by_convention_and_noise_is_skipped():
    """D122: files no rule read are one pattern per extension and place; noise isn't listed."""
    from steno.coverage.collect import _unread_files, convention

    assert convention("svc-a/src/main/resources/flows/order.xyz") == "resources/flows/"
    assert convention("tools/run.sh") == "tools/"
    found: dict = {}

    class _It:
        def __init__(self, key):
            self.key, self.files = key, []

        def add(self, sample):
            self.files.append(sample["file"])

    def item(kind, signal, target, label):
        return found.setdefault(target, _It((kind, signal, label)))

    files = [
        "a/src/main/resources/flows/order.xyz",
        "b/src/main/resources/flows/pay.xyz",
        "a/src/main/resources/db/V1__init.sql",
        "a/src/main/java/com/x/A.java",  # read by a rule
        "README.md",
        "a/pom.xml",
        ".github/workflows/ci.yml",
        "a/src/test/resources/flows/fixture.xyz",
    ]
    _unread_files(files, {"a/src/main/java/com/x/A.java"}, item, lambda f: "")
    assert sorted(found) == [".sql@resources/db/", ".xyz@resources/flows/"]
    assert len(found[".xyz@resources/flows/"].files) == 2


def test_code_in_a_language_no_pack_reads_is_one_pattern():
    from steno.coverage.collect import _unread_files

    found: dict = {}

    def item(kind, signal, target, label):
        return found.setdefault(target, type("I", (), {"add": lambda self, s: None})())

    _unread_files(["web/src/pages/a.js", "web/src/components/x/b.js", "web/a.css"], set(), item,
                  lambda f: "")  # fmt: skip
    assert sorted(found) == [".js@*"]


def test_an_unread_file_the_code_names_has_evidence(tmp_path):
    """D122: `getResource("flows/order.xyz")` makes order.xyz's pattern rank higher."""
    from steno.coverage.collect import Item, _referenced

    (tmp_path / "A.java").write_text('class A { void f() { load("flows/order.xyz"); } }')
    named = Item("config", "unread_file", ".xyz@resources/flows/", "x")
    named.add({"file": "src/main/resources/flows/order.xyz"})
    other = Item("config", "unread_file", ".conf@nginx/", "y")
    other.add({"file": "nginx/site.conf"})
    items = {("config", named.target): named, ("config", other.target): other}
    _referenced(items, tmp_path, {"A.java"})
    assert named.evidence == {"referenced_by_code"}
    assert other.evidence == set()
