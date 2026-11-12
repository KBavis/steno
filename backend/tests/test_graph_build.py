"""The graph plan: architecture nodes, code nodes, and each flow's trace and steps."""

import shutil
from pathlib import Path
from textwrap import dedent

import pytest

from steno.config import get_settings
from steno.extraction.facts import Extraction, NodeFact, Origin, split_url
from steno.graph.build import Context, build
from steno.graph.structure import detect
from steno.graph.trace import decode as decode_trace
from steno.ingestion.local import run_folder
from steno.rule_packs.packs import is_enabled, load_packs

needs_java = pytest.mark.skipif(
    not (get_settings().resolver_jvm_dir / "lib").is_dir() or shutil.which("java") is None,
    reason="the JVM helper isn't built (make resolver)",
)


J = "src/main/java/demo/"
# A small app: an endpoint, a service that writes a table, and a helper called from three
# places (a utility).
APP = {
    "build.gradle": """
        dependencies {
            implementation 'org.springframework.boot:spring-boot-starter-web'
            implementation 'org.springframework.boot:spring-boot-starter-data-jpa'
        }
    """,
    J + "Project.java": """
        package demo;
        import jakarta.persistence.*;
        @Entity
        @Table(name = "project")
        public class Project { @Id private Long id; }
    """,
    J + "ProjectRepository.java": """
        package demo;
        import org.springframework.data.jpa.repository.JpaRepository;
        public interface ProjectRepository extends JpaRepository<Project, Long> {}
    """,
    J + "Helpers.java": """
        package demo;
        public class Helpers {
            static String fmt(Object value) { return strip(String.valueOf(value)); }
            static String strip(String text) { return text.trim(); }
        }
    """,
    J + "ProjectService.java": """
        package demo;
        import org.springframework.stereotype.Service;
        @Service
        public class ProjectService {
            private final ProjectRepository db;
            public ProjectService(ProjectRepository db) { this.db = db; }
            public String describe(String name) { return Helpers.fmt(name); }
            public void create(String name) {
                Helpers.fmt(name);
                check(name);
                db.save(new Project());
                check(name);
            }
            String check(String name) { return Helpers.fmt(name); }
        }
    """,
    J + "ProjectController.java": """
        package demo;
        import org.springframework.web.bind.annotation.*;
        @RestController
        @RequestMapping("/projects")
        public class ProjectController {
            private final ProjectService svc;
            public ProjectController(ProjectService svc) { this.svc = svc; }
            @PostMapping("/")
            public void createProject(@RequestParam String name) {
                svc.describe(name);
                svc.create(name);
            }
        }
    """,
}
FLOW = "flow:demo:POST /projects/"


def _write(root: Path, files: dict[str, str]) -> Path:
    for name, code in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(dedent(code).lstrip())
    return root


def _plan(repo: Path, repo_name: str = "demo"):
    packs = [p for p in load_packs(get_settings().rule_packs_dir) if is_enabled(p, repo)]
    engine = run_folder(repo, packs)
    x = engine.out
    structure = detect(repo, {ep.origin.file for ep in x.entry_points})
    return build(x, engine.resolver, structure, Context(repo_name, "space:1", "org:1", 7, "abc123"))


def _app(tmp_path: Path) -> Path:
    return _write(tmp_path, APP)


def _trace(plan, flow=FLOW):
    return {t["path"]: t for t in decode_trace(plan.nodes[flow].props["trace"])}


@needs_java
def test_architecture_and_code_nodes_without_function_nodes(tmp_path):
    plan = _plan(_app(tmp_path))
    edges = {(e.type, e.src, e.dst) for e in plan.edges.values()}

    assert plan.nodes["app:demo"].labels[0] == "Application"
    assert ("BUILT_FROM", "app:demo", "module:demo:.") in edges
    assert ("STARTS", "endpoint:demo:POST:/projects/", FLOW) in edges
    assert not any("Function" in n.labels for n in plan.nodes.values())
    assert not any(e.type in ("INVOKES", "ENTRY", "RUNS") for e in plan.edges.values())
    # The flow's functions are its trace, starting at the handler
    assert _trace(plan)["1"]["symbol"] == "demo.ProjectController#createProject(java.lang.String)"


@needs_java
def test_trace_lists_every_call_but_never_expands_a_utility(tmp_path):
    trace = _trace(_plan(_app(tmp_path)))
    names = {path: t["name"] for path, t in trace.items()}

    assert names == {
        "1": "createProject",
        "1.1": "describe",
        "1.1.1": "fmt",
        "1.2": "create",
        "1.2.1": "fmt",
        "1.2.2": "check",
        "1.2.2.1": "fmt",
        "1.2.3": "check",
    }
    # fmt is called from three places and nothing architectural is below it: a utility,
    # listed at each call site, its own call to strip never expanded
    assert all(trace[p].get("utility") for p in ("1.1.1", "1.2.1", "1.2.2.1"))
    # check was expanded the first time; the second call is listed, not expanded again
    assert trace["1.2.3"].get("repeat") is True
    assert trace["1.2"]["effects"][0]["edge"] == "WRITES_TO"
    assert trace["1.2"]["call_line"] == 11


@needs_java
def test_significant_functions_become_steps_with_their_effects(tmp_path):
    plan = _plan(_app(tmp_path))
    edges = {(e.type, e.src, e.dst): e for e in plan.edges.values()}
    root = f"step:{FLOW}:demo.ProjectController#createProject(java.lang.String)"
    create = f"step:{FLOW}:demo.ProjectService#create(java.lang.String)"
    table = next(n.id for n in plan.nodes.values() if n.labels[0] == "Table")

    steps = {n.id: n.props for n in plan.nodes.values() if n.labels == ["Step"]}
    assert set(steps) == {root, create}
    assert steps[create]["path"] == "1.1"
    assert ("FIRST_STEP", FLOW, root) in edges
    assert ("SUBSTEP", root, create) in edges
    assert ("WRITES_TO", create, table) in edges
    assert edges[("WRITES_TO", FLOW, table)].props["rollup"] is True


def test_org_defined_channels_join_applications_by_name(tmp_path):
    """an Interface label no core rule knows is keyed by its name, not its application."""
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


@needs_java
def test_every_fact_records_its_provenance(tmp_path):
    nodes = _plan(_app(tmp_path)).nodes.values()
    assert nodes
    for node in nodes:
        assert node.props["repo"] == "demo"
        assert node.props["ingestion_job"] == 7
        assert node.props["commit"] == "abc123"


@needs_java
def test_unidentified_stores_belong_to_their_own_repository(tmp_path):
    """Two applications in one space whose databases aren't known yet don't share a store."""
    case = _app(tmp_path)
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


@needs_java
def test_an_application_is_a_service_by_the_triggers_of_its_flows(tmp_path):
    # kinds are labels from what starts the flows; the module that builds it is Deployable
    plan = _plan(_app(tmp_path))
    app = plan.nodes["app:demo"]
    assert "Service" in app.labels and app.props["kinds"] == ["Service"]
    assert "Deployable" in plan.nodes["module:demo:."].labels
    assert plan.stats["applications_without_kind"] == []


# ----------------------------------------------------------- cross-module HTTP joins


WEB = "dependencies { implementation 'org.springframework.boot:spring-boot-starter-web' }"


def _service_pair(tmp_path: Path) -> Path:
    """Two modules of one repository: `gateway` calls `orders` by its bare service name,
    the shape a gateway route's `uri` or a Feign `@FeignClient` name takes
    (`route_target`), with no scheme and no dots."""
    return _write(tmp_path, {
        "orders/build.gradle": WEB,
        "orders/src/main/java/orders/OrdersApi.java": """
            package orders;
            import java.util.List;
            import org.springframework.web.bind.annotation.*;
            @RestController
            public class OrdersApi {
                @GetMapping("/items")
                public List<String> listItems() { return List.of(); }
            }
        """,
        "gateway/build.gradle": WEB,
        "gateway/src/main/java/gateway/GatewayApi.java": """
            package gateway;
            import org.springframework.web.bind.annotation.*;
            import org.springframework.web.client.RestTemplate;
            @RestController
            public class GatewayApi {
                private final RestTemplate rest = new RestTemplate();
                @GetMapping("/gateway/items")
                public String proxyItems() {
                    return rest.getForObject("http://demo-orders/items", String.class);
                }
            }
        """,
    })  # fmt: skip


@needs_java
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


@needs_java
def test_a_bare_host_that_names_no_known_module_still_becomes_an_external_system(
    tmp_path: Path,
):
    _write(tmp_path, {
        "build.gradle": WEB,
        "src/main/java/demo/Main.java": """
            package demo;
            import org.springframework.web.client.RestTemplate;
            public class Main {
                private final RestTemplate rest = new RestTemplate();
                public String callUnknown() {
                    String url = "http://totally-unrelated-service/items";
                    return rest.getForObject(url, String.class);
                }
            }
        """,
    })  # fmt: skip
    plan = _plan(tmp_path)
    externals = [n for n in plan.nodes.values() if "ExternalSystem" in n.labels]
    assert any(n.props.get("host") == "totally-unrelated-service" for n in externals)


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
