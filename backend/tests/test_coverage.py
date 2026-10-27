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
    _unread_files(files, {"a/src/main/java/com/x/A.java"}, item, lambda f: "", _noise())
    assert sorted(found) == [".sql@resources/db/", ".xyz@resources/flows/"]
    assert len(found[".xyz@resources/flows/"].files) == 2


def test_code_in_a_language_no_pack_reads_is_one_pattern():
    from steno.coverage.collect import _unread_files

    found: dict = {}

    def item(kind, signal, target, label):
        return found.setdefault(target, type("I", (), {"add": lambda self, s: None})())

    _unread_files(["web/src/pages/a.js", "web/src/components/x/b.js", "web/a.css"], set(), item,
                  lambda f: "", _noise())  # fmt: skip
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


def _noise() -> list[str]:
    from steno.config import get_settings
    from steno.rule_packs.packs import load_pack

    return load_pack(get_settings().rule_packs_dir / "core" / "steno-pack-noise").noise_files


def test_the_noise_pack_lists_nothing_a_facet_reads():
    """D122: noise is checked against the facets. Migrations (Data), API contracts and schemas
    (Interfaces), deploy, topic, and catalog files (PLATFORM, DECLARED) are never noise."""
    from steno.coverage.collect import is_noise

    noise = _noise()
    for rel in [
        "svc/src/main/resources/db/migration/V1__init.sql",
        "svc/src/main/resources/db/changelog/changelog.xml",
        "svc/src/main/resources/application.yml",
        "api/openapi.yaml",
        "proto/orders.proto",
        "schemas/order.avsc",
        "catalog-info.yaml",
        "component_manifest.yaml",
        "deploy/k8s/cronjob.yaml",
        "helm/values.yaml",
        "kafka/topics.yaml",
    ]:
        assert not is_noise(rel, noise), rel
    for rel in ["README.md", "web/a.css", "svc/.github/workflows/ci.yml", "logo.png", "LICENSE"]:
        assert is_noise(rel, noise), rel


def test_config_no_rule_read_is_grouped_by_prefix():
    """D122: keys set in config that nothing read; framework noise prefixes aren't listed."""
    from steno.coverage.collect import Item, _unread_config
    from steno.extraction.facts import ClueFact, Extraction, Origin

    x = Extraction()
    for key, value in [
        ("acme.messaging.topics.orders", "orders.created"),
        ("acme.messaging.group", "billing"),
        ("app.kafka.topic", "payments"),  # read through ${app.kafka.topic}
        ("logging.level.root", "INFO"),  # noise
    ]:
        x.clues.append(ClueFact("property", {"key": key, "value": value},
                                Origin("cfg", "p", "application.yml", 1)))  # fmt: skip
    found: dict = {}

    def item(kind, signal, target, label):
        return found.setdefault(target, Item(kind, signal, target, label))

    _unread_config(x, {"app.kafka.topic"}, ["logging"], item, lambda f: "")
    assert sorted(found) == ["acme.messaging"]
    assert found["acme.messaging"].occurrences == 2
    assert found["acme.messaging"].values == {"orders.created", "billing"}


def test_the_property_map_remembers_what_was_read():
    from steno.resolvers.base import Properties

    props = Properties()
    props.update({"a.b": "x", "c.d": "y"})
    assert props["a.b"] == "x" and props.get("missing") is None
    assert props.used == {"a.b"}


def test_an_unread_value_naming_a_known_topic_is_evidence(tmp_path):
    """D122: config that names a topic already in the graph is probably wiring."""
    from steno.coverage.collect import Item, _names_known
    from steno.graph.build import GraphPlan

    plan = GraphPlan()
    plan.node("kafkatopic:orders.created", ["Interface", "KafkaTopic"], name="orders.created")
    plan.node("table:x::users", ["Table"], name="users")  # too short to count
    wired = Item("config", "unread_config", "acme.messaging", "x")
    wired.values.add("orders.created")
    other = Item("config", "unread_config", "acme.ui", "y")
    other.values.update({"dark", "users", "orders.created.v2"})
    (tmp_path / "flows").mkdir()
    (tmp_path / "flows" / "a.xyz").write_text("route orders.created -> billing")
    file = Item("config", "unread_file", ".xyz@flows/", "z")
    file.add({"file": "flows/a.xyz"})
    items = {1: wired, 2: other, 3: file}
    _names_known(items, plan, tmp_path)
    assert wired.evidence == {"names_known_things"}
    assert other.evidence == set()
    assert file.evidence == {"names_known_things"}


def test_a_pattern_inside_an_internal_library_has_evidence():
    from steno.coverage.collect import Item, _in_library

    lib = Item("unmet_join", "unknown_hook", "com.acme.Bus", "x")
    lib.add({"file": "plugins/messaging/Bus.java", "function": "Bus#start()"})
    app = Item("unmet_join", "unknown_hook", "com.acme.Other", "y")
    app.add({"file": "services/users/Other.java", "function": "Other#run()"})
    _in_library({1: lib, 2: app}, lambda f: f.startswith("plugins/"))
    assert lib.evidence == {"in_internal_library"} and app.evidence == set()


def test_a_file_name_ignored_within_its_pattern_is_skipped_only_there():
    from steno.coverage.collect import _unread_files

    found: dict = {}

    def item(kind, signal, target, label):
        return found.setdefault(
            target,
            type("I", (), {"files": [], "add": lambda self, s: self.files.append(s["file"])})(),
        )

    files = ["docker-compose.local.yml", "deploy.yml", "conf/docker-compose.local.yml"]
    _unread_files(
        files, set(), item, lambda f: "", [], {("unread_file:.yml@/", "docker-compose.local.yml")}
    )
    # Only that name, only in that pattern: deploy.yml stays, and so does the same name elsewhere
    assert found[".yml@/"].files == ["deploy.yml"]
    assert found[".yml@conf/"].files == ["conf/docker-compose.local.yml"]


def test_an_application_with_no_known_trigger_is_a_coverage_item_not_a_default_kind():
    from types import SimpleNamespace

    from steno.coverage.collect import _kindless

    found: dict = {}

    def item(kind, signal, target, label):
        return found.setdefault((signal, target), type("I", (), {"add": lambda self, s: None})())

    plan = SimpleNamespace(
        stats={"applications_without_kind": [{"application": "batch", "id": "app:x"}]}
    )
    _kindless(plan, item)
    assert list(found) == [("no_trigger_application", "app:x")]
