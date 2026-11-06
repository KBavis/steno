"""The graph plan: architecture nodes, code nodes, and each flow's trace and steps (D60, D63)."""

import shutil
from pathlib import Path
from textwrap import dedent

import pytest

from steno.config import get_settings
from steno.extraction.facts import Extraction, NodeFact, Origin, split_url
from steno.graph.build import Context, _reaches_effect, build
from steno.graph.structure import detect
from steno.graph.trace import decode as decode_trace
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
    return {t["path"]: t for t in decode_trace(plan.nodes[flow].props["trace"])}


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


def test_an_effect_keeps_every_operation_and_call_site():
    """A function that deletes and saves to the same table: both operations, every line."""
    from steno.graph.build import GraphPlan

    plan = GraphPlan()
    plan.edge("WRITES_TO", "step:a", "table:t", operation="delete", source_line=164)
    plan.edge("WRITES_TO", "step:a", "table:t", operation="upsert", source_line=120)
    plan.edge("WRITES_TO", "step:a", "table:t", operation="upsert", source_line=140)
    props = plan.edges[("WRITES_TO", "step:a", "table:t")].props
    assert props["operation"] == "delete, upsert"
    assert props["call_sites"] == ["120 upsert", "140 upsert", "164 delete"]


def test_an_application_is_a_service_by_the_triggers_of_its_flows():
    # D85: kinds are labels from what starts the flows; the module that builds it is Deployable
    plan = _plan()
    app = plan.nodes["app:demo"]
    assert "Service" in app.labels and app.props["kinds"] == ["Service"]
    assert "Deployable" in plan.nodes["module:demo:."].labels
    assert plan.stats["applications_without_kind"] == []


# ----------------------------------------------------------- cross-module HTTP joins


def _service_pair(tmp_path: Path) -> Path:
    """Two modules of one repository: `gateway` calls `orders` by its bare service name,
    the shape a gateway route's `uri` or a Feign `@FeignClient` name takes (D132's
    `route_target`), with no scheme and no dots."""
    (tmp_path / "orders").mkdir()
    (tmp_path / "orders/requirements.txt").write_text("fastapi\n")
    (tmp_path / "orders/__init__.py").write_text("")
    (tmp_path / "orders/orders_api.py").write_text(
        dedent(
            """
            from fastapi import FastAPI

            orders_app = FastAPI()


            @orders_app.get("/items")
            def list_items():
                return []
            """
        ).lstrip()
    )
    (tmp_path / "gateway").mkdir()
    (tmp_path / "gateway/requirements.txt").write_text("fastapi\nrequests\n")
    (tmp_path / "gateway/__init__.py").write_text("")
    (tmp_path / "gateway/gateway_api.py").write_text(
        dedent(
            """
            import requests
            from fastapi import FastAPI

            gateway_app = FastAPI()


            @gateway_app.get("/gateway/items")
            def proxy_items():
                return requests.get("http://demo-orders/items").json()
            """
        ).lstrip()
    )
    return tmp_path


def test_a_call_to_another_modules_bare_service_name_joins_its_endpoint(tmp_path: Path):
    plan = _plan(_service_pair(tmp_path))
    # Each module is its own application, named after the repository plus its folder (no
    # identity stage in this test, same fallback production uses)
    assert "app:demo-orders" in plan.nodes and "app:demo-gateway" in plan.nodes

    target = plan.nodes["endpoint:demo-orders:GET:/items"]
    assert "HttpEndpoint" in target.labels and target.props["path"] == "/items"
    # Written once, by orders' own build (not a second, fabricated copy from the caller)
    assert sum(1 for n in plan.nodes.values() if n.props.get("path") == "/items") == 1

    edges = {(e.type, e.src, e.dst) for e in plan.edges.values()}
    assert (
        "CALLS",
        "flow:demo-gateway:GET /gateway/items",
        "endpoint:demo-orders:GET:/items",
    ) in edges
    # Not also filed as an ExternalSystem keyed by the bare host
    assert "external:demo-orders" not in plan.nodes


def test_a_bare_host_that_names_no_known_module_still_becomes_an_external_system(
    tmp_path: Path,
):
    (tmp_path / "requirements.txt").write_text("fastapi\nrequests\n")
    (tmp_path / "main.py").write_text(
        dedent(
            """
            import requests


            def call_unknown():
                return requests.get("http://totally-unrelated-service/items").json()
            """
        ).lstrip()
    )
    plan = _plan(tmp_path)
    externals = [n for n in plan.nodes.values() if "ExternalSystem" in n.labels]
    assert any(n.props.get("host") == "totally-unrelated-service" for n in externals)


needs_java = pytest.mark.skipif(
    not (get_settings().resolver_jvm_dir / "lib").is_dir() or shutil.which("java") is None,
    reason="the JVM helper isn't built (make resolver)",
)


def _java_service(root: Path, name: str, java: str, config: str = "") -> None:
    """A Spring service in its own Gradle project, with config outside `src/main/resources`."""
    source = root / name / "src/main/java/demo" / name.replace("-", "")
    source.mkdir(parents=True)
    (source / "App.java").write_text(java)
    (root / name / "build.gradle").write_text(
        "plugins { id 'org.springframework.boot' version '3.2.0' }\n"
        "dependencies { implementation 'org.springframework.boot:spring-boot-starter-web' }\n"
    )
    settings = root / name / "src/main/config/runtime/common/app"
    settings.mkdir(parents=True)
    (settings / "application.yml").write_text(config)


def _caller() -> str:
    return dedent(
        """
        package demo.caller;
        import org.springframework.beans.factory.annotation.Value;
        import org.springframework.boot.autoconfigure.SpringBootApplication;
        import org.springframework.web.bind.annotation.*;
        import org.springframework.web.client.RestTemplate;

        @SpringBootApplication
        @RestController
        public class App {
            private final RestTemplate rest = new RestTemplate();
            @Value("${target.endpoint.items}") private String url;

            @PostMapping("/orders")
            public String order() { return rest.getForObject(url, String.class); }
        }
        """
    ).lstrip()


CLUSTER_URL = "http://{}.${{ENVIRONMENT}}-shop-${{ROLE}}${{SUFFIX}}.svc.cluster.local:8080"
TARGET = dedent(
    """
    package demo.inventory;
    import org.springframework.boot.autoconfigure.SpringBootApplication;
    import org.springframework.web.bind.annotation.*;

    @SpringBootApplication
    @RestController
    public class App {
        @GetMapping("/items")
        public String items() { return "x"; }
    }
    """
).lstrip()


def _config(service: str) -> str:
    return (
        f"target.rest.url: {CLUSTER_URL.format(service)}\n"
        "target.endpoint.items: ${target.rest.url}/items\n"
    )


@needs_java
def test_a_call_through_nested_config_to_a_cluster_host_joins_the_services_endpoint(tmp_path: Path):
    _java_service(tmp_path, "caller", _caller(), _config("demo-inventory"))
    _java_service(tmp_path, "inventory", TARGET)
    plan = _plan(tmp_path)
    edges = {(e.type, e.src, e.dst) for e in plan.edges.values()}
    assert ("CALLS", "flow:demo-caller:POST /orders", "endpoint:demo-inventory:GET:/items") in edges


@needs_java
def test_a_cluster_host_outside_the_repository_is_one_system_per_service(tmp_path: Path):
    _java_service(tmp_path, "caller", _caller(), _config("other-service"))
    plan = _plan(tmp_path)
    node = plan.nodes["external:other-service"]
    assert node.props["name"] == "other-service"
    assert "${ENVIRONMENT}" in node.props["host"]
    assert not node.props.get("stub")


@needs_java
def test_config_keys_that_only_name_each_other_are_left_unknown(tmp_path: Path):
    cycle = "target.endpoint.items: ${a}\na: ${b}\nb: ${a}\n"
    _java_service(tmp_path, "caller", _caller(), cycle)
    plan = _plan(tmp_path)
    assert any(n.startswith("external:unresolved") for n in plan.nodes)


@needs_java
def test_a_url_built_with_uri_components_builder_joins_the_services_endpoint(tmp_path: Path):
    """Constructor `@Value`, a builder chain, and a local URI, the shape real clients take."""
    client = dedent(
        """
        package demo.caller;
        import java.net.URI;
        import org.springframework.beans.factory.annotation.Value;
        import org.springframework.boot.autoconfigure.SpringBootApplication;
        import org.springframework.core.ParameterizedTypeReference;
        import org.springframework.http.*;
        import org.springframework.web.bind.annotation.*;
        import org.springframework.web.client.RestTemplate;
        import org.springframework.web.util.UriComponentsBuilder;

        @SpringBootApplication
        @RestController
        public class App {
            private final RestTemplate rest;
            private final String base;

            public App(RestTemplate rest, @Value("${target.endpoint.items}") String base) {
                this.rest = rest;
                this.base = base;
            }

            @PostMapping("/orders")
            public String order(String id) {
                URI uri = UriComponentsBuilder.fromUriString(base)
                    .queryParam("id", id).build().encode().toUri();
                return rest.exchange(uri, HttpMethod.GET, HttpEntity.EMPTY,
                    new ParameterizedTypeReference<String>() {}).getBody();
            }
        }
        """
    ).lstrip()
    _java_service(tmp_path, "caller", client, _config("demo-inventory"))
    _java_service(tmp_path, "inventory", TARGET)
    plan = _plan(tmp_path)
    edges = {(e.type, e.src, e.dst) for e in plan.edges.values()}
    assert ("CALLS", "flow:demo-caller:POST /orders", "endpoint:demo-inventory:GET:/items") in edges


def test_only_a_fixed_cluster_service_name_keeps_a_templated_host():
    assert split_url("http://orders.${ENV}-shop.svc.cluster.local:8080/items") == (
        "orders.${ENV}-shop.svc.cluster.local:8080",
        "/items",
    )
    assert split_url("http://orders.{env}.svc/items") == ("orders.{env}.svc", "/items")
    assert split_url("https://{domain}/rest/api/2/search")[0] is None
    assert split_url("http://{service}.shop.svc.cluster.local/items")[0] is None
    assert (
        split_url("http://shop-${ROLE}-orders.ns.svc:8080/items")[0]
        == "shop-${ROLE}-orders.ns.svc:8080"
    )
    assert split_url("https://api.${REGION}.example.com/items")[0] is None


@needs_java
def test_stream_bindings_join_a_producer_and_a_consumer_on_one_topic(tmp_path: Path):
    """Spring Cloud Stream: `StreamBridge` in one service, a functional bean in another. Their
    binding destinations name the same topic, so one flow leads to the other."""
    for name in ("publisher", "listener"):
        _java_service(tmp_path, name, "", "")
        (tmp_path / name / "build.gradle").write_text(
            "dependencies {\n"
            "  implementation 'org.springframework.boot:spring-boot-starter-web:3.2.0'\n"
            "  implementation 'org.springframework.cloud:spring-cloud-stream-binder-kafka:4.1.0'\n"
            "}\n"
        )
    source = tmp_path / "publisher/src/main/java/demo/publisher/App.java"
    source.write_text(
        dedent(
            """
            package demo.publisher;
            import org.springframework.boot.autoconfigure.SpringBootApplication;
            import org.springframework.cloud.stream.function.StreamBridge;
            import org.springframework.web.bind.annotation.*;

            @SpringBootApplication
            @RestController
            public class App {
                private final StreamBridge bridge;
                App(StreamBridge bridge) { this.bridge = bridge; }

                @PostMapping("/orders")
                public void order(String event) { bridge.send("orders-out-0", event); }
            }
            """
        ).lstrip()
    )
    source = tmp_path / "listener/src/main/java/demo/listener/App.java"
    source.write_text(
        dedent(
            """
            package demo.listener;
            import java.util.function.Consumer;
            import org.springframework.boot.autoconfigure.SpringBootApplication;
            import org.springframework.context.annotation.Bean;

            @SpringBootApplication
            public class App {
                @Bean
                public Consumer<String> onOrder() { return event -> { }; }
            }
            """
        ).lstrip()
    )
    config = "src/main/config/runtime/common/app/application.yml"
    (tmp_path / "publisher" / config).write_text(
        "spring.cloud.stream:\n  bindings:\n    orders-out-0:\n      destination: order-events\n"
    )
    (tmp_path / "listener" / config).write_text(
        "spring.cloud.stream:\n  bindings:\n    onOrder-in-0:\n      destination: order-events\n"
    )
    plan = _plan(tmp_path)
    topic = next(n for n, v in plan.nodes.items() if "KafkaTopic" in v.labels)
    assert plan.nodes[topic].props["name"] == "order-events"
    edges = {(e.type, e.src, e.dst) for e in plan.edges.values()}
    assert ("PRODUCES", "flow:demo-publisher:POST /orders", topic) in edges
    assert any(t == "STARTS" and s == topic and "demo-listener" in d for t, s, d in edges)


def test_significant_functions_are_found_through_chains_and_cycles():
    graph = {
        "entry": ["middle", "pure"],
        "middle": ["writes"],
        "writes": [],
        "pure": ["pure_too"],
        "pure_too": ["pure"],  # a cycle that never reaches an effect
        "loop_a": ["loop_b"],
        "loop_b": ["loop_a", "writes"],  # a cycle that does
    }

    assert _reaches_effect(set(graph), graph, {"writes", "not_reached"}) == {
        "entry",
        "middle",
        "writes",
        "loop_a",
        "loop_b",
    }
