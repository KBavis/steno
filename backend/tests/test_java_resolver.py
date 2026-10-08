"""The Java resolver end to end (D69–D72): the JVM helper resolves a small Spring service from
source and the JDK, Lombok-generated members included, and flows follow it; calls through
library types Steno can't see stop visibly instead of silently.

Needs the JVM helper (`make resolver`); skipped without it."""

import json
import shutil
from pathlib import Path
from textwrap import dedent

import pytest

from steno.config import get_settings
from steno.coverage.collect import collect
from steno.graph.build import Context, build
from steno.graph.structure import detect
from steno.ingestion.local import run_folder
from steno.resolvers.java import JavaResolver, _literal, library_of
from steno.rule_packs.packs import Pack, Rule, declared_dependencies

pytestmark = pytest.mark.skipif(
    not (get_settings().resolver_jvm_dir / "lib").is_dir() or shutil.which("java") is None,
    reason="the JVM helper isn't built (make resolver)",
)

FILES = {
    "build.gradle": """
        dependencies {
            implementation 'org.springframework.boot:spring-boot-starter-web'
            implementation group: 'org.springframework.kafka', name: 'spring-kafka'
            compileOnly 'org.projectlombok:lombok'
        }
    """,
    "src/main/resources/application.yml": """
        app:
          topics:
            orders: orders-v1
    """,
    "src/main/java/com/x/web/OrderController.java": """
        package com.x.web;

        import com.x.service.OrderService;
        import lombok.RequiredArgsConstructor;
        import org.springframework.web.bind.annotation.*;

        @RestController
        @RequiredArgsConstructor
        public class OrderController {
            private final OrderService orderService;

            @PostMapping("/orders/{id}")
            public void create(@PathVariable String id) {
                orderService.place(id);
            }
        }
    """,
    "src/main/java/com/x/service/OrderService.java": """
        package com.x.service;

        public interface OrderService {
            void place(String id);
        }
    """,
    "src/main/java/com/x/service/OrderServiceImpl.java": """
        package com.x.service;

        import com.x.model.Item;
        import com.x.model.Order;
        import com.x.repo.OrderRepository;
        import lombok.RequiredArgsConstructor;
        import org.springframework.beans.factory.annotation.Value;
        import org.springframework.kafka.core.KafkaTemplate;
        import org.springframework.stereotype.Service;
        import org.springframework.web.reactive.function.client.WebClient;

        @Service
        @RequiredArgsConstructor
        public class OrderServiceImpl implements OrderService {
            private static final String CHARGE = "/charge";
            private final OrderRepository orders;
            private final KafkaTemplate<String, String> kafka;
            private final WebClient web;

            @Value("${app.topics.orders}")
            private String topic;

            @Override
            public void place(String id) {
                Order order = Order.builder().id(id).build();
                orders.save(order);
                order.getItems().stream().filter(i -> i.isGift()).forEach(i -> audit(i));
                kafka.send(topic, id);
                web.post().uri(CHARGE + "/" + id).retrieve();
            }

            private void audit(Item item) {
                item.getName();
            }
        }
    """,
    "src/main/java/com/x/model/Order.java": """
        package com.x.model;

        import java.util.List;
        import lombok.Builder;
        import lombok.Data;

        @Data
        @Builder
        public class Order {
            private String id;
            private List<Item> items;
        }
    """,
    "src/main/java/com/x/model/Item.java": """
        package com.x.model;

        import lombok.Data;

        @Data
        public class Item {
            private String name;
            private boolean gift;
        }
    """,
    "src/main/java/com/x/repo/OrderRepository.java": """
        package com.x.repo;

        import com.x.model.Order;
        import org.springframework.data.jpa.repository.JpaRepository;

        public interface OrderRepository extends JpaRepository<Order, String> {}
    """,
    "src/test/java/com/x/OrderServiceTest.java": """
        package com.x;
        class OrderServiceTest {}
    """,
}


def _rule(rid: str, **body: object) -> Rule:
    return Rule(
        id=rid,
        pack="test-java",
        language=str(body.pop("language", "java")),
        kind=str(body.pop("kind", "code")),
        match=body.pop("match", {}),  # type: ignore[arg-type]
        emit=body.pop("emit", []),  # type: ignore[arg-type]
        where=body.pop("where", {}),  # type: ignore[arg-type]
    )


PACK = Pack(
    name="test-java",
    version="0",
    language="java",
    source="core",
    description="",
    enabled_when={"always": True},
    path=Path("."),
    rules=[
        _rule(
            "post-mapping",
            match={"rule": {"pattern": "@PostMapping($PATH)"}},
            emit=[
                {"node": "HttpEndpoint", "method": "POST", "path": "$PATH", "as": "ep"},
                {"entry_point": {"trigger": "ep", "function": "@function"}},
            ],
        ),
        _rule(
            "jpa-save",
            match={"rule": {"pattern": "$REPO.save($E)"}},
            where={"$REPO": {"type": ["org.springframework.data.jpa.repository.JpaRepository"]}},
            emit=[{"edge": "WRITES_TO", "from": "@function", "to": {"Table": {"name": "orders"}}}],
        ),
        _rule(
            "kafka-send",
            match={"rule": {"pattern": "$K.send($TOPIC, $V)"}},
            where={"$K": {"type": ["org.springframework.kafka.core.KafkaTemplate"]}},
            emit=[
                {"edge": "PRODUCES", "from": "@function", "to": {"KafkaTopic": {"name": "$TOPIC"}}}
            ],
        ),
        _rule(
            "app-properties",
            kind="config",
            match={"key": "app.**"},
            emit=[{"clue": "property", "key": "$KEY", "value": "$VALUE"}],
        ),
    ],
)


@pytest.fixture(scope="module")
def repo(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("orders")
    for rel, text in FILES.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(dedent(text).lstrip())
    return root


@pytest.fixture(scope="module")
def engine(repo: Path):  # type: ignore[no-untyped-def]
    return run_folder(repo, [PACK])


def _flow(engine, repo: Path) -> dict:  # type: ignore[no-untyped-def]
    x = engine.out
    plan = build(
        x,
        engine.resolver,
        detect(repo, {ep.origin.file for ep in x.entry_points}),
        Context(repo="orders", space_id=None, org_id=None, job_id=1, commit=None),
    )
    flows = [n for n in plan.nodes.values() if n.labels[0] == "Flow"]
    assert len(flows) == 1
    return {**flows[0].props, "plan": plan}


def test_functions_are_symbols_with_parameter_types(engine) -> None:  # type: ignore[no-untyped-def]
    java = engine.resolver.by_language["java"]
    symbols = {f.symbol for f in java.functions()}
    assert "com.x.service.OrderServiceImpl#place(java.lang.String)" in symbols
    assert "com.x.service.OrderServiceImpl#audit(com.x.model.Item)" in symbols
    # Test code isn't read, and Lombok's generated members aren't functions of their own
    assert not any("OrderServiceTest" in s for s in symbols)
    assert not any(s.endswith("#getItems()") for s in symbols)


def test_flow_follows_interfaces_lombok_and_streams(engine, repo: Path) -> None:  # type: ignore[no-untyped-def]
    flow = _flow(engine, repo)
    trace = json.loads(flow["trace"])
    assert [t["symbol"] for t in trace] == [
        "com.x.web.OrderController#create(java.lang.String)",
        # One implementation of the interface: DI can only inject that one
        "com.x.service.OrderServiceImpl#place(java.lang.String)",
        # i.isGift() and audit(i) resolve through the @Data getter's List<Item>
        "com.x.service.OrderServiceImpl#audit(com.x.model.Item)",
    ]
    audit = trace[2]
    assert audit["in_loop"] is True
    assert audit["file"] == "src/main/java/com/x/service/OrderServiceImpl.java"


def test_effects_and_config_values(engine, repo: Path) -> None:  # type: ignore[no-untyped-def]
    plan = _flow(engine, repo)["plan"]
    effects = {
        (k[0], plan.nodes[k[2]].props.get("name"))
        for k in plan.edges
        if k[0] in ("WRITES_TO", "PRODUCES")
    }
    # A library type named by its import is enough for a rule's `where: type`
    assert ("WRITES_TO", "orders") in effects
    # @Value("${app.topics.orders}") reads application.yml through the shared property map
    assert ("PRODUCES", "orders-v1") in effects


def test_library_chain_stops_visibly(engine, repo: Path) -> None:  # type: ignore[no-untyped-def]
    flow = _flow(engine, repo)
    place = json.loads(flow["trace"])[1]
    stops = place.get("unresolved", [])
    # web.post() is a known library call; what it returns isn't, so the chain stops there.
    # uri() isn't listed in the trace (not I/O, not ours); retrieve() is named like I/O.
    assert [(s["reason"], s.get("library")) for s in stops] == [
        ("library type", "org.springframework.web")
    ]
    assert "retrieve" in stops[0]["call"]
    assert flow["incomplete"] is True and flow["unresolved_calls"] == 1


def test_coverage_counts_unresolved_library_types(engine, repo: Path) -> None:  # type: ignore[no-untyped-def]
    plan = _flow(engine, repo)["plan"]
    report = collect(engine.out, engine.resolver, plan, [PACK])
    gaps = [i for i in report.items if i.signal == "unresolved_type"]
    assert [i.target for i in gaps] == ["org.springframework.web"]
    assert gaps[0].occurrences == 2  # uri() and retrieve()
    assert report.metrics["unresolved_library_calls"] == 2


def test_url_template_keeps_constant_parts(engine, repo: Path) -> None:  # type: ignore[no-untyped-def]
    java = engine.resolver.by_language["java"]
    found = [e for e in java._exprs.values() if e.get("template", "").startswith("/charge/")]
    assert found and found[0]["template"] == "/charge/{id}"


def test_gradle_dependencies_enable_java_packs(repo: Path) -> None:
    deps = declared_dependencies(repo)
    assert "org-springframework-boot:spring-boot-starter-web" in deps
    assert "org-springframework-kafka:spring-kafka" in deps


def test_helpers() -> None:
    assert (
        library_of("org.springframework.kafka.core.KafkaTemplate#send")
        == "org.springframework.kafka"
    )
    assert library_of("reactor.core.publisher.Mono") == "reactor.core"
    assert library_of("okhttp3.OkHttpClient") == "okhttp3"
    assert _literal('"/orders"') == "/orders"
    assert _literal('{"/a", "/b"}') == ["/a", "/b"]
    assert _literal("42L") == 42


def test_missing_helper_is_an_error_not_a_crash(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "resolver_jvm_dir", repo / "nowhere")
    r = JavaResolver(repo)
    assert list(r.functions()) == []
    assert "Java resolver unavailable" in r.errors[0][1]
