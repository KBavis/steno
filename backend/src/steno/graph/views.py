"""Layer views of the graph: what the UI's Visualize tab draws (D53), and what MCP's
`get_node` will return. Each view is one layer of the containment tree, with counts and
aggregated edges instead of everything below it (progressive disclosure).

Every view returns the same shape:
    {"nodes": [...], "edges": [...], "groups": [...], "breadcrumbs": [...], ...}
Groups are boxes that contain nodes (a space, a resource such as /api/jobs, a folder).
"""

import json
from collections import defaultdict
from typing import Any

from neo4j import Driver

from steno.graph import ids
from steno.graph.build import step_of

# The most specific label wins
KINDS = [
    ("Organization", "organization"),
    ("Space", "space"),
    ("Application", "application"),
    ("Flow", "flow"),
    ("Step", "step"),
    ("HttpEndpoint", "endpoint"),
    ("Interface", "interface"),
    ("Entity", "entity"),
    ("Table", "table"),
    ("DataStore", "datastore"),
    ("ExternalSystem", "external"),
    ("Repository", "repository"),
    ("Module", "module"),
    ("File", "file"),
]
# Stable-ID prefix → label with a uniqueness index, so lookups by ID use it
ID_LABELS = {
    "org": "Organization",
    "space": "Space",
    "app": "Application",
    "flow": "Flow",
    "step": "Step",
    "endpoint": "Interface",
    "entity": "Entity",
    "table": "Table",
    "datastore": "DataStore",
    "external": "ExternalSystem",
    "repo": "Repository",
    "module": "Module",
    "file": "File",
}
PROVENANCE = {
    "repo",
    "ingestion_job",
    "commit",
    "extracted_by",
    "source_file",
    "source_line",
    "confidence",
    "last_seen",
    "first_seen",
    "id",
    "name",
}
# Shown through the flow view, not as raw properties
HIDDEN = {"trace", "trace_files", "trace_symbols", "shared"}
EFFECT_TYPES = ["READS_FROM", "WRITES_TO", "CALLS", "PRODUCES", "CONSUMES"]
LEVEL_UNITS = {"CALLS": "calls", "READS_FROM": "tables", "WRITES_TO": "tables"}


def kind_of(labels: list[str]) -> str:
    return next(
        (k for label, k in KINDS if label in labels), labels[0].lower() if labels else "node"
    )


def _label_for(node_id: str) -> str:
    return ID_LABELS.get(node_id.split(":", 1)[0], "")


def _node(n: Any, sub: str | None = None, **extra: Any) -> dict[str, Any]:
    props = dict(n)
    kind = kind_of(list(n.labels))
    return {
        "id": props.get("id"),
        "kind": kind,
        "label": props.get("name") or props.get("path") or props.get("id"),
        "sub": sub,
        "stub": bool(props.get("stub")),
        **extra,
    }


class GraphViews:
    def __init__(self, driver: Driver, database: str):
        self.driver = driver
        self.database = database

    def _read(self, query: str, **params: Any) -> list[Any]:
        with self.driver.session(database=self.database) as s:
            return list(s.run(query, **params))

    # ---------------------------------------------------------- organization

    def overview(self) -> dict[str, Any]:
        return self.level(None)

    def level(self, container_id: str | None) -> dict[str, Any]:
        """One level of the containment tree: the organization or a space, its direct children,
        and how they communicate.

        Everything below a child rolls up into it, so the organization level shows only its
        spaces, and a space shows its sub-spaces, applications, and data stores. Applications
        connect by calling each other's interfaces and by reading or writing data stores; those
        links are counted between the children they fall under. Things outside the container
        that talk to it appear as neighbors, at the highest level that is still outside it.
        """
        tree = self._tree()
        if not tree.nodes:
            return {
                "view": "organization",
                "nodes": [],
                "edges": [],
                "groups": [],
                "breadcrumbs": [],
                "empty": True,
            }
        container = container_id or tree.root
        if container not in tree.nodes or tree.kind[container] not in ("organization", "space"):
            raise KeyError(container)

        def rep(node_id: str) -> tuple[str, bool]:
            """The node a deep element is drawn as at this level, and whether it's outside."""
            path = tree.path(node_id)
            if container in path:
                i = path.index(container)
                return (path[i + 1] if i + 1 < len(path) else node_id), False
            here = tree.path(container)
            common = 0
            while common < min(len(path), len(here)) and path[common] == here[common]:
                common += 1
            return path[min(common, len(path) - 1)], True

        links = self._links()
        shown: dict[str, bool] = {}  # node id → outside?
        for child in tree.children.get(container, []):
            shown[child] = False
        edges: list[dict[str, Any]] = []
        internal: dict[str, int] = defaultdict(int)
        for link in links:
            src, src_out = rep(link["src"])
            if link["dst_kind"] == "external":
                dst, dst_out = link["dst"], True
            else:
                dst, dst_out = rep(link["dst"])
            if src_out and dst_out:
                continue  # neither end is in this container
            if src == dst:
                internal[src] += link["n"]
                continue
            shown.setdefault(src, src_out)
            shown.setdefault(dst, dst_out)
            edges.append({"src": src, "dst": dst, "type": link["type"], "n": link["n"]})

        externals = {n for n in shown if n in links.externals}
        nodes = []
        for node_id, outside in shown.items():
            if node_id in externals:
                ext = links.externals[node_id]
                nodes.append(
                    {
                        "id": node_id,
                        "kind": "external",
                        "label": ext["name"],
                        "sub": ext.get("host"),
                        "stub": ext["stub"],
                        "parent": "group:external",
                    }
                )
                continue
            nodes.append(tree.describe(node_id, outside=outside, internal=internal.get(node_id, 0)))
        groups = []
        if externals:
            groups.append(
                {
                    "id": "group:external",
                    "kind": "externals",
                    "label": "Outside the organization",
                    "parent": None,
                }
            )
        crumbs = self._breadcrumbs(container)
        return {
            "view": "organization" if container == tree.root else "space",
            "focus": container,
            "container": tree.describe(container),
            "nodes": nodes,
            "groups": groups,
            "edges": _merge_edges(edges, unit=LEVEL_UNITS),
            "breadcrumbs": crumbs,
        }

    def _tree(self) -> "_Tree":
        """The containment tree above flows: organization, spaces, applications, data stores."""
        tree = _Tree()
        for row in self._read(
            """
            MATCH (x) WHERE x:Organization OR x:Space OR x:Application OR x:DataStore
            OPTIONAL MATCH (x)-[:BELONGS_TO]->(p)
            RETURN x, labels(x) AS labels, p.id AS parent
            """
        ):
            tree.add(row["x"], kind_of(row["labels"]), row["parent"])
        for row in self._read(
            """
            MATCH (a:Application)
            OPTIONAL MATCH (fl:Flow)-[:BELONGS_TO]->(a)
            WITH a, count(DISTINCT fl) AS flows
            OPTIONAL MATCH (a)-[:EXPOSES]->(i:Interface)
            RETURN a.id AS id, flows, count(DISTINCT i) AS interfaces
            """
        ):
            tree.stats[row["id"]].update(flows=row["flows"], interfaces=row["interfaces"])
        for row in self._read(
            "MATCH (t:Table)-[:BELONGS_TO]->(d:DataStore) RETURN d.id AS id, count(t) AS tables"
        ):
            tree.stats[row["id"]]["tables"] = row["tables"]
        tree.finish()
        return tree

    def _links(self) -> "_Links":
        """How applications communicate, counted per pair: calls to another application's
        interfaces or an external system, and reads and writes of a data store's tables."""
        links = _Links()
        for row in self._read(
            """
            MATCH (a:Application)<-[:BELONGS_TO]-(fl:Flow)-[:CALLS]->(:Interface)
                  <-[:EXPOSES]-(b:Application)
            WHERE a <> b
            RETURN a.id AS src, b.id AS dst, 'application' AS dst_kind, 'CALLS' AS type,
                   count(DISTINCT fl) AS n
            UNION ALL
            MATCH (a:Application)<-[:BELONGS_TO]-(fl:Flow)-[:CALLS]->(e:ExternalSystem)
            RETURN a.id AS src, e.id AS dst, 'external' AS dst_kind, 'CALLS' AS type,
                   count(DISTINCT fl) AS n
            UNION ALL
            MATCH (a:Application)<-[:BELONGS_TO]-(:Flow)-[r:READS_FROM|WRITES_TO]->(t:Table)
                  -[:BELONGS_TO]->(d:DataStore)
            RETURN a.id AS src, d.id AS dst, 'datastore' AS dst_kind, type(r) AS type,
                   count(DISTINCT t) AS n
            UNION ALL
            MATCH (a:Application)<-[:BELONGS_TO]-(:Flow)-[r:READS_FROM|WRITES_TO]->(d:DataStore)
            RETURN a.id AS src, d.id AS dst, 'datastore' AS dst_kind, type(r) AS type, 1 AS n
            """
        ):
            links.append(row.data())
        for row in self._read("MATCH (e:ExternalSystem) RETURN e"):
            e = row["e"]
            links.externals[e["id"]] = {
                "name": e.get("name") or e["id"],
                "host": e.get("host"),
                "stub": bool(e.get("stub")),
            }
        return links

    # ------------------------------------------------------------ application

    def application(self, app_id: str) -> dict[str, Any]:
        """An application's flows, grouped by resource, and what each one touches."""
        rows = self._read("MATCH (a:Application {id: $id}) RETURN a", id=app_id)
        if not rows:
            raise KeyError(app_id)
        nodes, groups, edges = [], [], []
        resources: set[str] = set()
        for row in self._read(
            """
            MATCH (fl:Flow)-[:BELONGS_TO]->(:Application {id: $id})
            RETURN fl, last(split(fl.entry_symbol, '.')) AS entry ORDER BY fl.name
            """,
            id=app_id,
        ):
            fl = row["fl"]
            method, _, path = (fl.get("name") or "").partition(" ")
            resource = _resource(path)
            resources.add(resource)
            nodes.append(
                _node(
                    fl,
                    row["entry"],
                    parent=f"group:{resource}",
                    method=method,
                    path=path,
                    functions=fl.get("functions"),
                    drill="flow",
                )
            )
        groups += [
            {"id": f"group:{r}", "kind": "resource", "label": r, "parent": None}
            for r in sorted(resources)
        ]

        targets: dict[str, dict[str, Any]] = {}
        for row in self._read(
            """
            MATCH (fl:Flow)-[:BELONGS_TO]->(:Application {id: $id})
            MATCH (fl)-[r]->(t) WHERE r.rollup AND type(r) IN $types
            OPTIONAL MATCH (t)-[:BELONGS_TO]->(d:DataStore)
            OPTIONAL MATCH (owner:Application)-[:EXPOSES]->(t)
            RETURN fl.id AS src, t, type(r) AS type, d, owner
            """,
            id=app_id,
            types=EFFECT_TYPES,
        ):
            t, d, owner = row["t"], row["d"], row["owner"]
            if t["id"] not in targets:
                parent, sub = _place_target(t, d, owner, groups)
                targets[t["id"]] = _node(t, sub, parent=parent)
            edges.append({"src": row["src"], "dst": t["id"], "type": row["type"], "n": 1})

        # What each flow does and who uses each target, so reads and writes show without lines
        verbs = {"WRITES_TO": "writes", "READS_FROM": "reads", "CALLS": "calls"}
        for e in edges:
            verb = verbs.get(e["type"])
            if not verb:
                continue
            flow_node = next(n for n in nodes if n["id"] == e["src"])
            flow_node.setdefault("effects", {}).setdefault(verb, 0)
            flow_node["effects"][verb] += 1
            usage = targets[e["dst"]].setdefault("usage", {})
            usage[verb] = usage.get(verb, 0) + 1
        nodes += list(targets.values())
        return {
            "view": "application",
            "focus": app_id,
            "nodes": nodes,
            "groups": groups,
            "edges": _merge_edges(edges),
            "breadcrumbs": self._breadcrumbs(app_id),
            "summary": {
                "flows": sum(1 for n in nodes if n["kind"] == "flow"),
                "touches": len(targets),
            },
        }

    def application_code(self, app_id: str) -> dict[str, Any]:
        """The code behind an application: the files its flows run through, grouped by folder,
        and the calls between them, counted from the flows' traces."""
        nodes = []
        folders: set[str] = set()
        repo = None
        for row in self._read(
            """
            MATCH (:Application {id: $id})-[:BUILT_FROM]->(m:Module)<-[:BELONGS_TO]-(fi:File)
            RETURN fi, m.path AS module ORDER BY fi.path
            """,
            id=app_id,
        ):
            fi = row["fi"]
            repo = fi.get("repo")
            rel = (
                fi["path"][len(row["module"]) + 1 :]
                if fi["path"].startswith(row["module"] + "/")
                else fi["path"]
            )
            folder = rel.rsplit("/", 1)[0] if "/" in rel else "."
            folders.add(folder)
            nodes.append(
                _node(
                    fi,
                    f"{fi.get('functions', 0)} functions · {fi.get('significant', 0)} with effects",
                    parent=f"group:{folder}",
                    label=rel.rsplit("/", 1)[-1],
                )
            )
        # A call between files: a trace entry whose caller is in another file. Each pair of
        # functions counts once, however many flows make that call.
        pairs: dict[tuple[str, str], set[tuple[str, str]]] = defaultdict(set)
        for row in self._read(
            "MATCH (fl:Flow)-[:BELONGS_TO]->(:Application {id: $id}) RETURN fl.trace AS trace",
            id=app_id,
        ):
            trace = _trace(row["trace"])
            by_path = {t["path"]: t for t in trace}
            for t in trace:
                if "." not in t["path"]:
                    continue
                caller = by_path.get(t["path"].rsplit(".", 1)[0])
                if caller and caller["file"] != t["file"]:
                    pairs[(caller["file"], t["file"])].add((caller["symbol"], t["symbol"]))
        shown = {n["id"] for n in nodes}
        edges = []
        for (f1, f2), calls in pairs.items():
            src, dst = ids.file_id(repo or "", f1), ids.file_id(repo or "", f2)
            if src in shown and dst in shown:
                edges.append({"src": src, "dst": dst, "type": "INVOKES", "n": len(calls)})
        groups = [
            {"id": f"group:{f}", "kind": "folder", "label": f, "parent": None}
            for f in sorted(folders)
        ]
        return {
            "view": "code",
            "focus": app_id,
            "nodes": nodes,
            "groups": groups,
            "edges": _merge_edges(edges, unit={"INVOKES": "calls"}),
            "breadcrumbs": self._breadcrumbs(app_id),
        }

    # ------------------------------------------------------------------ flow

    def flow(self, flow_id: str, significant_only: bool = True) -> dict[str, Any]:
        """A flow as a sequence: a header (its trigger and what it does overall), its steps in
        the order they run, and the data each step reads and writes.

        Read from the flow's trace (D60): every function it calls, depth-first in source
        order. With `significant_only`, only its Steps are shown (functions with an effect at
        or below them), without repeated calls (their effects are shown where the function
        first runs); the others pass their calls through. Significant entries are drawn as
        their Step node; the rest are named by their place in the trace. Each one names its
        `parent_step` (the nearest shown caller), so the UI can fold a branch into it.
        """
        rows = self._read(
            """
            MATCH (fl:Flow {id: $id})
            OPTIONAL MATCH (t:Interface)-[:STARTS]->(fl)
            RETURN fl, t
            """,
            id=flow_id,
        )
        if not rows:
            raise KeyError(flow_id)
        fl, trigger = rows[0]["fl"], rows[0]["t"]
        trace = _trace(fl.get("trace"))

        steps: list[dict[str, Any]] = []
        shown_depth: dict[str, int] = {}  # trace path → depth it's drawn at
        shown_id: dict[str, str] = {}  # trace path → the id it's drawn as
        conditional: dict[str, bool] = {}
        for t in trace:
            path = t["path"]
            parent = path.rsplit(".", 1)[0] if "." in path else None
            conditional[path] = bool(t.get("conditional")) or conditional.get(parent or "", False)
            # Depth = how many shown ancestors it has; its parent is the nearest one
            depth, p, parent_step = 0, parent, None
            while p is not None:
                if p in shown_depth:
                    depth, parent_step = shown_depth[p] + 1, shown_id[p]
                    break
                p = p.rsplit(".", 1)[0] if "." in p else None
            if significant_only and ("step" not in t or t.get("repeat")):
                continue
            node_id = step_of(flow_id, t) if "step" in t else ids.trace_entry_id(flow_id, path)
            shown_depth[path], shown_id[path] = depth, node_id
            steps.append(
                {
                    "id": node_id,
                    "parent_step": parent_step,
                    "kind": "function",
                    "label": t["name"],
                    "sub": t["file"],
                    "stub": False,
                    "container": t.get("container"),
                    "order": len(steps) + 1,
                    "depth": depth,
                    "conditional": conditional[path],
                    "is_async": bool(t.get("async")),
                    "significant": bool(t.get("significant")),
                    "bound_to": t.get("bound_to"),
                    "candidate": bool(t.get("candidate")),
                    "utility": bool(t.get("utility")),
                    "repeat": bool(t.get("repeat")),
                    "effects_of": t.get("effects", []),
                }
            )

        target_ids = sorted({e["target"] for st in steps for e in st["effects_of"]})
        groups: list[dict[str, Any]] = []
        targets: dict[str, dict[str, Any]] = {}
        for row in self._read(
            """
            MATCH (t) WHERE t.id IN $ids
            OPTIONAL MATCH (t)-[:BELONGS_TO]->(d:DataStore)
            OPTIONAL MATCH (owner:Application)-[:EXPOSES]->(t)
            RETURN t, d, owner
            """,
            ids=target_ids,
        ):
            t = row["t"]
            parent, sub = _place_target(t, row["d"], row["owner"], groups)
            targets[t["id"]] = _node(t, sub, parent=parent)
        edges = []
        for st in steps:
            for e in st.pop("effects_of"):
                if e["target"] not in targets:
                    continue
                op = e.get("operation")
                edges.append(
                    {"src": st["id"], "dst": e["target"], "type": e["edge"], "n": 1, "op": op}
                )
                st.setdefault("does", []).append(
                    {"type": e["edge"], "op": op, "target": targets[e["target"]]["label"]}
                )

        # Each target sits next to the first step that uses it
        order = {st["id"]: st["order"] for st in steps}
        first_use: dict[str, int] = {}
        for e in edges:
            first_use[e["dst"]] = min(first_use.get(e["dst"], 10**6), order[e["src"]])
        for tid, target in targets.items():
            target["first_use"] = first_use.get(tid)

        summary = {
            "writes": len({e["dst"] for e in edges if e["type"] == "WRITES_TO"}),
            "reads": len({e["dst"] for e in edges if e["type"] == "READS_FROM"}),
            "calls": len({e["dst"] for e in edges if e["type"] == "CALLS"}),
        }
        method, _, path = (fl.get("name") or "").partition(" ")
        head = _node(
            fl,
            (fl.get("entry_symbol") or "").rsplit(".", 1)[-1] or None,
            method=method,
            path=path,
            trigger=_node(trigger)["kind"] if trigger is not None else None,
            effects=summary,
            purpose=fl.get("purpose"),
        )
        return {
            "view": "flow",
            "focus": flow_id,
            "nodes": [head, *steps, *targets.values()],
            "groups": groups,
            "edges": _merge_edges(edges),
            "breadcrumbs": self._breadcrumbs(flow_id),
            "summary": {
                "functions": len({t["symbol"] for t in trace}),
                "shown": len(steps),
                "significant_only": significant_only,
            },
        }

    # ---------------------------------------------------------------- details

    def node(self, node_id: str) -> dict[str, Any]:
        if node_id.startswith("trace:"):
            return self._trace_entry(node_id)
        label = _label_for(node_id)
        match = f"MATCH (n:{label} {{id: $id}})" if label else "MATCH (n {id: $id})"
        rows = self._read(f"{match} RETURN n", id=node_id)
        if not rows:
            raise KeyError(node_id)
        n = rows[0]["n"]
        props = dict(n)
        neighbors = []
        for row in self._read(
            f"""
            {match}
            CALL (n) {{
              MATCH (n)-[r]->(m) RETURN type(r) AS type, 'out' AS dir, m, properties(r) AS rp
              UNION ALL
              MATCH (n)<-[r]-(m) RETURN type(r) AS type, 'in' AS dir, m, properties(r) AS rp
            }}
            RETURN type, dir, m, rp LIMIT 400
            """,
            id=node_id,
        ):
            m = row["m"]
            neighbors.append(
                {
                    "type": row["type"],
                    "dir": row["dir"],
                    "node": {
                        "id": m.get("id"),
                        "kind": kind_of(list(m.labels)),
                        "label": m.get("name") or m.get("path"),
                    },
                    "props": {
                        k: v
                        for k, v in row["rp"].items()
                        if k
                        in ("operation", "seq", "async", "conditional", "url", "method", "rollup")
                    },
                }
            )
        return {
            "id": node_id,
            "kind": kind_of(list(n.labels)),
            "labels": list(n.labels),
            "label": props.get("name") or props.get("path") or node_id,
            "properties": {
                k: v
                for k, v in props.items()
                if k not in PROVENANCE and k not in HIDDEN and not k.startswith("card")
            },
            "provenance": {
                k: props.get(k)
                for k in (
                    "repo",
                    "commit",
                    "extracted_by",
                    "source_file",
                    "source_line",
                    "confidence",
                    "ingestion_job",
                )
                if props.get(k) is not None
            },
            "neighbors": neighbors,
            "breadcrumbs": self._breadcrumbs(node_id),
        }

    def _trace_entry(self, entry_id: str) -> dict[str, Any]:
        """One function in a flow's trace. Not a node: read from the flow (D60)."""
        flow_id, _, path = entry_id.removeprefix("trace:").rpartition("#")
        rows = self._read("MATCH (fl:Flow {id: $id}) RETURN fl", id=flow_id)
        entry = (
            next((t for t in _trace(rows[0]["fl"].get("trace")) if t["path"] == path), None)
            if rows
            else None
        )
        if entry is None:
            raise KeyError(entry_id)
        fl = rows[0]["fl"]
        shown = (
            "symbol",
            "bound_to",
            "container",
            "doc",
            "start_line",
            "end_line",
            "call_line",
            "conditional",
            "in_loop",
            "async",
            "candidate",
            "utility",
            "repeat",
            "recursive",
        )
        return {
            "id": entry_id,
            "kind": "function",
            "labels": ["TraceEntry"],
            "label": entry["name"],
            "properties": {k: entry[k] for k in shown if k in entry},
            "provenance": {
                k: v
                for k, v in {
                    "repo": fl.get("repo"),
                    "commit": fl.get("commit"),
                    "extracted_by": "call-graph",
                    "source_file": entry["file"],
                    "source_line": entry.get("start_line"),
                }.items()
                if v is not None
            },
            "neighbors": [
                {
                    "type": "IN_TRACE_OF",
                    "dir": "out",
                    "node": {"id": flow_id, "kind": "flow", "label": fl.get("name")},
                    "props": {},
                }
            ],
            "breadcrumbs": [
                *self._breadcrumbs(flow_id),
                {"id": entry_id, "label": entry["name"], "kind": "function"},
            ],
        }

    def search(self, text: str, limit: int = 12) -> list[dict[str, Any]]:
        words = [
            w for w in "".join(c if c.isalnum() or c in "_-/" else " " for c in text).split() if w
        ]
        if not words:
            return []
        query = " AND ".join(f"{_escape(w)}*" for w in words)
        out = []
        for row in self._read(
            "CALL db.index.fulltext.queryNodes('searchable_text', $q) YIELD node, score "
            "RETURN node, score LIMIT $limit",
            q=query,
            limit=limit,
        ):
            n = row["node"]
            out.append(
                {
                    "id": n.get("id"),
                    "kind": kind_of(list(n.labels)),
                    "label": n.get("name"),
                    "score": row["score"],
                }
            )
        # Functions aren't searchable architecture nodes, but their names help find a flow:
        # a match in a flow's trace opens that flow
        for row in self._read(
            "MATCH (fl:Flow) "
            "WITH fl, [s IN fl.trace_symbols WHERE toLower(s) CONTAINS toLower($t)] AS hits "
            "WHERE size(hits) > 0 "
            "RETURN fl, hits[0] AS symbol ORDER BY size(hits[0]) LIMIT 5",
            t=words[0],
        ):
            fl = row["fl"]
            out.append(
                {
                    "id": fl.get("id"),
                    "kind": "flow",
                    "label": row["symbol"].rsplit(".", 1)[-1],
                    "sub": f"in {fl.get('name')}",
                }
            )
        return out

    def _breadcrumbs(self, node_id: str) -> list[dict[str, Any]]:
        """The node's owners, from the organization down: Org › Space › … › App › Flow."""
        label = _label_for(node_id)
        match = f"MATCH (n:{label} {{id: $id}})" if label else "MATCH (n {id: $id})"
        rows = self._read(
            f"{match} MATCH p = (n)-[:BELONGS_TO*0..8]->(top) WHERE NOT (top)-[:BELONGS_TO]->() "
            "RETURN [x IN nodes(p) | {id: x.id, label: coalesce(x.name, x.path), "
            "labels: labels(x)}] "
            "AS chain "
            "ORDER BY length(p) DESC LIMIT 1",
            id=node_id,
        )
        if not rows:
            return []
        chain = list(reversed(rows[0]["chain"]))
        return [{"id": c["id"], "label": c["label"], "kind": kind_of(c["labels"])} for c in chain]


class _Tree:
    """Organization → spaces → applications and data stores, with roll-up counts."""

    def __init__(self) -> None:
        self.nodes: dict[str, Any] = {}
        self.kind: dict[str, str] = {}
        self.parent: dict[str, str | None] = {}
        self.children: dict[str, list[str]] = defaultdict(list)
        self.stats: dict[str, dict[str, int]] = defaultdict(dict)
        self.root = ""

    def add(self, node: Any, kind: str, parent: str | None) -> None:
        self.nodes[node["id"]] = node
        self.kind[node["id"]] = kind
        self.parent[node["id"]] = parent
        if kind == "organization":
            self.root = node["id"]

    def finish(self) -> None:
        for node_id, parent in self.parent.items():
            if parent in self.nodes:
                self.children[parent].append(node_id)
        order = {"space": 0, "application": 1, "datastore": 2}
        for kids in self.children.values():
            kids.sort(
                key=lambda k: (
                    order.get(self.kind[k], 3),
                    (self.nodes[k].get("name") or "").lower(),
                )
            )
        # Each node carries the index of its top-level space, so the UI can keep its color
        tops = [k for k in self.children.get(self.root, []) if self.kind[k] == "space"]
        self.hue = {top: i for i, top in enumerate(tops)}

    def path(self, node_id: str) -> list[str]:
        """The chain from the organization down to the node."""
        chain = [node_id]
        while (p := self.parent.get(chain[-1])) and p in self.nodes and len(chain) < 32:
            chain.append(p)
        return list(reversed(chain))

    def descendants(self, node_id: str) -> list[str]:
        out, todo = [], list(self.children.get(node_id, []))
        while todo:
            n = todo.pop()
            out.append(n)
            todo.extend(self.children.get(n, []))
        return out

    def describe(self, node_id: str, outside: bool = False, internal: int = 0) -> dict[str, Any]:
        n, kind = self.nodes[node_id], self.kind[node_id]
        path = self.path(node_id)
        top = path[1] if len(path) > 1 else None
        out: dict[str, Any] = {
            "id": node_id,
            "kind": kind,
            "label": n.get("name") or node_id,
            "stub": bool(n.get("stub")),
            "parent": None,
            "outside": outside,
            "hue": self.hue.get(top) if top else None,
            "description": n.get("description"),
        }
        if outside and len(path) > 2:
            out["sub"] = " › ".join(self.nodes[p].get("name") or p for p in path[1:-1])
        st = self.stats.get(node_id, {})
        if kind in ("organization", "space"):
            below = self.descendants(node_id)
            apps = [d for d in below if self.kind[d] == "application"]
            out["stats"] = {
                "spaces": sum(1 for d in below if self.kind[d] == "space"),
                "applications": len(apps),
                "flows": sum(self.stats.get(a, {}).get("flows", 0) for a in apps),
                "datastores": sum(1 for d in below if self.kind[d] == "datastore"),
                "internal": internal,
            }
            out["preview"] = [
                self.nodes[a].get("name")
                for a in sorted(apps, key=lambda a: -self.stats.get(a, {}).get("flows", 0))[:6]
            ]
            out["drill"] = "space" if kind == "space" else None
        elif kind == "application":
            out["stats"] = {"flows": st.get("flows", 0), "interfaces": st.get("interfaces", 0)}
            out["sub"] = (
                out.get("sub")
                or f"{st.get('interfaces', 0)} endpoints · {st.get('flows', 0)} flows"
            )
            out["drill"] = "app"
        elif kind == "datastore":
            out["sub"] = out.get("sub") or (
                f"{st['tables']} tables" if st.get("tables") else n.get("vendor")
            )
        return out


class _Links(list):
    """Application-level links: dicts of src, dst, dst_kind, type, n."""

    def __init__(self) -> None:
        super().__init__()
        self.externals: dict[str, dict[str, Any]] = {}


def _place_target(
    t: Any, d: Any, owner: Any, groups: list[dict[str, Any]]
) -> tuple[str, str | None]:
    """The card a flow's target goes in (its data store, the application that exposes it, or
    External systems), created on first use, and the target's subtitle."""

    def group(gid: str, kind: str, label: str | None, stub: bool = False) -> str:
        if not any(g["id"] == gid for g in groups):
            groups.append({"id": gid, "kind": kind, "label": label, "parent": None, "stub": stub})
        return gid

    if owner is not None:  # another application's interface
        return group(f"group:{owner['id']}", "application", owner.get("name")), None
    if d is not None:  # a table
        return group(f"group:{d['id']}", "datastore", d.get("name"), bool(d.get("stub"))), None
    if kind_of(list(t.labels)) == "datastore":  # the store as a whole, not one of its tables
        whole = group(f"group:{t['id']}", "datastore", t.get("name"), bool(t.get("stub")))
        return whole, "the whole store"
    return group("group:external", "externals", "External systems"), t.get("host")


def _trace(raw: Any) -> list[dict[str, Any]]:
    return json.loads(raw) if isinstance(raw, str) else []


def _resource(path: str) -> str:
    """`/api/jobs/projects/{project_id}` → `/api/jobs`: the router a route belongs to."""
    parts = [p for p in path.split("/") if p]
    head = [p for p in parts[:2] if not p.startswith("{")]
    return "/" + "/".join(head) if head else "/"


def _merge_edges(
    edges: list[dict[str, Any]], unit: dict[str, str] | None = None
) -> list[dict[str, Any]]:
    merged: dict[tuple[str, str, str], dict[str, Any]] = {}
    for e in edges:
        key = (e["src"], e["dst"], e["type"])
        if key in merged:
            merged[key]["n"] += e.get("n", 1)
        else:
            merged[key] = {**e, "id": f"{e['type']}|{e['src']}|{e['dst']}"}
    out = list(merged.values())
    if unit:
        for e in out:
            if e["type"] in unit and e.get("n", 1) > 1:
                e["label"] = f"{e['n']} {unit[e['type']]}"
    return out


def _escape(word: str) -> str:
    special = '+-&|!(){}[]^"~*?:\\/'
    return "".join(f"\\{c}" if c in special else c for c in word)
