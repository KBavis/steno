"""The coverage report on the surface: library gaps, quiet signals, silent repositories
and witnesses, hooks that start work, and drops that lost a surface fact."""

import shutil
from pathlib import Path
from textwrap import dedent

import pytest

from steno.config import get_settings
from steno.coverage.collect import (
    Item,
    _dropped,
    _quiet,
    _silent_witnesses,
    collect,
    is_internal,
)
from steno.extraction.facts import Extraction, Origin
from steno.graph.build import Context, build
from steno.graph.structure import detect
from steno.ingestion.local import run_folder
from steno.rule_packs.packs import Pack, Rule, is_enabled, load_packs
from steno.rule_packs.surface import on_surface

needs_jvm = pytest.mark.skipif(
    not (get_settings().resolver_jvm_dir / "lib").is_dir() or shutil.which("java") is None,
    reason="the JVM helper isn't built (make resolver)",
)
J = "src/main/java/com/x/"
APP = {
    "build.gradle": """
        dependencies { implementation 'org.springframework.boot:spring-boot-starter-web' }
    """,
    J + "Checkout.java": """
        package com.x;
        import com.acme.orders.OrdersClient;
        import com.stripe.Charge;
        import java.nio.file.Files;
        import java.nio.file.Path;
        import org.springframework.web.bind.annotation.*;
        @RestController
        public class Checkout {
            private final OrdersClient orders = new OrdersClient();
            @PostMapping("/checkout")
            public void checkout(@RequestParam String orderId) throws Exception {
                orders.create(orderId);                  // the organization's wrapper: not followed
                Charge.create(100);                      // a vendor SDK no list knows
                // a local file: ranked low
                Files.writeString(Path.of("/tmp/receipt.txt"), orderId);
            }
        }
    """,
}


def _report(tmp_path: Path, prefixes: list[str] | None = None, **kw):
    for name, code in APP.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(dedent(code).lstrip())
    all_packs = load_packs(get_settings().rule_packs_dir)
    packs = [p for p in all_packs if is_enabled(p, tmp_path)]
    engine = run_folder(tmp_path, packs)
    x = engine.out
    structure = detect(tmp_path, {ep.origin.file for ep in x.entry_points})
    ctx = Context("demo", "space:1", "org:1", 1, "c")
    plan = build(x, engine.resolver, structure, ctx, packs=all_packs)
    report = collect(x, engine.resolver, plan, all_packs, internal_prefixes=prefixes, **kw)
    return {(i.signal, i.target): i for i in report.items}, report


@needs_jvm
def test_an_internal_wrapper_and_an_unknown_sdk_are_library_gaps(tmp_path):
    items, _ = _report(tmp_path, ["com.acme"])
    internal = items[("internal_library", "com.acme.orders")]
    assert internal.label == "Internal library not followed · com.acme.orders"
    assert internal.samples[0]["detail"].startswith("com.acme.orders.")
    assert ("external_library", "com.stripe") in items
    # A local file is still a gap, below the network, messaging, and databases
    assert ("file_call", "java.nio") in items
    assert not any(s == "external_call" and t == "java.nio" for s, t in items)


@needs_jvm
def test_without_prefixes_the_organizations_library_is_external(tmp_path):
    items, _ = _report(tmp_path)
    assert ("external_library", "com.acme.orders") in items
    assert not any(s == "internal_library" for s, _ in items)


@needs_jvm
def test_library_gaps_rank_internal_before_external(tmp_path):
    _, report = _report(tmp_path, ["com.acme"])
    order = [i.signal for i in report.items]
    assert order.index("internal_library") < order.index("external_library")
    assert order.index("external_library") < order.index("file_call")


def test_internal_means_a_prefix_or_the_repositorys_own_root():
    assert is_internal("com.acme.client.Orders#create", "com.acme.client", ["com.acme"], set())
    assert is_internal("com.acme.client.Orders#create", "com.acme.client", ["com.acme:*"], set())
    assert is_internal("acme_orders.client.create", "acme_orders", ["acme_"], set())
    assert is_internal("io.kb.common.Http#get", "io.kb.common", [], {"io.kb"})
    assert not is_internal("com.acmex.Thing#do", "com.acmex", ["com.acme"], set())
    assert not is_internal("com.stripe.Charge#create", "com.stripe", [], {"io.kb"})


def test_a_repository_that_produced_nothing_is_one_item(tmp_path):
    (tmp_path / "deploy").mkdir()
    (tmp_path / "deploy" / "orders.xml").write_text("<deployment/>")
    (tmp_path / "deploy" / "billing.xml").write_text("<deployment/>")
    all_packs = load_packs(get_settings().rule_packs_dir)
    engine = run_folder(tmp_path, [p for p in all_packs if is_enabled(p, tmp_path)])
    structure = detect(tmp_path, set())
    plan = build(engine.out, engine.resolver, structure, Context("xld", "space:1", "org:1", 1, "c"))
    files = ["deploy/orders.xml", "deploy/billing.xml"]
    report = collect(
        engine.out, engine.resolver, plan, all_packs, files=files, read=set(),
        repository="xld", library_modules=0,
    )  # fmt: skip
    silent = next(i for i in report.items if i.signal == "silent_repository")
    assert silent.target == "xld" and not silent.quiet
    assert silent.samples[0]["detail"] == "holds 2 *.xml under deploy/"
    assert report.items[0] is silent  # a whole missing witness ranks first


@needs_jvm
def test_a_repository_with_an_application_isnt_silent(tmp_path):
    items, _ = _report(tmp_path, files=[J + "Checkout.java"], read={J + "Checkout.java"},
                       repository="demo",
                       library_modules=0)  # fmt: skip
    assert not any(s == "silent_repository" for s, _ in items)


def test_a_witness_pack_that_stated_nothing_is_an_item():
    x = Extraction()
    platform = Pack("acme-xld", "0", "any", "org", "", {}, Path("."), [_rule("xld-topic")],
                    witness_type="PLATFORM")  # fmt: skip
    code = Pack("spring-web", "0", "java", "core", "", {}, Path("."), [_rule("m")])
    found: dict = {}

    def item(kind, signal, target, label):
        return found.setdefault(target, Item(kind, signal, target, label))

    _silent_witnesses(x, [platform, code], item)
    assert list(found) == ["acme-xld"]
    assert found["acme-xld"].signal == "silent_witness"


def test_unread_settings_are_quiet_unless_they_name_a_connection():
    def it(signal: str, target: str, key: str = "") -> Item:
        i = Item("config", signal, target, target)
        i.add({"file": "application.yml", "function": key})
        return i

    items = {
        1: it("unread_config", "acme.cache", "acme.cache.ttl"),
        2: it("unread_config", "acme.billing", "acme.billing.base-url"),
        3: it(
            "unread_config", "spring.cloud", "spring.cloud.stream.bindings.orders-in.destination"
        ),
        4: it("unread_file", ".xyz@flows/"),
        5: it("version_untested", "spring-kafka · 4.0"),
        6: it("unknown_host", "app.Tenants"),
    }
    _quiet(items)
    assert [k for k, i in items.items() if i.quiet] == [1, 4, 5]


def test_a_drop_is_an_item_only_when_the_rule_states_a_surface_fact():
    topic = _rule("send", [{"edge": "PRODUCES", "from": "@function", "to": {"KafkaTopic": {}}}])
    detail = _rule("label", [{"clue": "property", "key": "$K"}])
    assert on_surface(topic) and not on_surface(detail)
    x = Extraction()
    x.dropped += [(Origin("send", "p", "a.py", 3), "where: couldn't tell"),
                  (Origin("label", "p", "a.py", 9), "where: couldn't tell")]  # fmt: skip
    found: dict = {}

    def item(kind, signal, target, label):
        return found.setdefault(target, Item(kind, signal, target, label))

    counted = _dropped(x, item, {("p", "send"): topic, ("p", "label"): detail})
    assert list(found) == ["send: where"] and counted == 1


def test_a_hook_that_can_start_work_carries_evidence(tmp_path):
    from steno.coverage.collect import _hooks

    class _Plan:
        stats = {"unknown_hooks": [
            {"target": "com.acme.messaging.PxMessageHandler", "file": "A.java", "line": 3,
             "symbol": "A#on", "kind": "annotation"},
            {"target": "com.acme.util.Audited", "file": "A.java", "line": 9,
             "symbol": "A#x", "kind": "annotation"},
        ]}  # fmt: skip

    found: dict = {}

    def item(kind, signal, target, label):
        return found.setdefault(target, Item(kind, signal, target, label))

    _hooks(_Plan(), item)
    assert found["com.acme.messaging.PxMessageHandler"].evidence == {"can_start_work"}
    assert found["com.acme.util.Audited"].evidence == set()


def _rule(rule_id: str, emit: list | None = None) -> Rule:
    return Rule(rule_id, "p", "any", "code", {}, emit or [])


def test_a_specific_io_listing_wins_over_a_harmless_prefix():
    """`java.` is harmless, but `java.net.http` and `java.nio.file.Files` do I/O: a call into
    them that no rule explains must be a gap, not silent."""
    from steno.accounting import Ledger

    ledger = Ledger(Extraction(), None)
    assert ledger.is_io("java.net.http.HttpClient#send")
    assert ledger.is_io("java.nio.file.Files#write")
    assert not ledger.is_io("java.util.List#add")
    assert not ledger.is_io("jakarta.transaction.Transactional")


def test_one_sided_joins_name_the_side_that_exists():
    from steno.coverage.joins import items

    found = items(
        [
            {"id": "t1", "name": "kafka:orders.created", "producers": ["orders-svc"],
             "consumers": [], "produced": True},
            {"id": "t2", "name": "payments.settled", "producers": [],
             "consumers": ["billing", "ledger"], "produced": False},
        ],
        [{"id": "e1", "method": "POST", "path": "/v1/clients", "callers": ["onboarding"]}],
    )  # fmt: skip
    by = {f["target"]: f for f in found}
    assert by["t1"]["label"] == "Produced, never consumed · orders.created"
    assert by["t1"]["apps"] == ["orders-svc"]
    assert by["t2"]["label"] == "Consumed, never produced · payments.settled"
    assert by["e1"]["label"] == "Called, not exposed by any application · POST /v1/clients"
    assert by["e1"]["fingerprint"] == "one_sided:e1"
    assert found[0]["target"] == "t2"  # the most applications first
