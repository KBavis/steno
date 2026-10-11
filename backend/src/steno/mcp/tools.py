"""What Steno's MCP tools answer (docs/how-it-works/retrieval-and-mcp.md §5; first tools, D54).

Each tool returns a compact answer for an agent: the facts, a `citations` list (D29: each
claim with its file, lines, commit, and a link), `next` hints for the call after this one,
and, when the answer had to be cut to its `max_tokens`, what was left out. The server
(`server.py`) registers these and logs every call.

Search is full-text for now (D54); cards and Jev routing come later.
"""

import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from neo4j import Driver
from sqlalchemy import select
from sqlalchemy.orm import Session

from steno.api.links import source_url
from steno.connectors.source import SourceError, read_file, slice_lines
from steno.db.models import Connector, Repository
from steno.extraction.facts import split_url
from steno.graph.build import step_of
from steno.graph.views import GraphViews, kind_of

EFFECTS = ["READS_FROM", "WRITES_TO", "CALLS", "PRODUCES", "CONSUMES"]
VERB = {
    "READS_FROM": "reads",
    "WRITES_TO": "writes",
    "CALLS": "calls",
    "PRODUCES": "produces to",
    "CONSUMES": "consumes from",
}
# `via` filters for find_dependents
VIA = {
    "http": {"CALLS"},
    "messaging": {"PRODUCES", "CONSUMES"},
    "data": {"READS_FROM", "WRITES_TO"},
}
CHARS_PER_TOKEN = 4
OUTLINE = 25  # steps a flow's outline aims for, before folding deeper levels


class ToolError(ValueError):
    """A request the tool can't answer (unknown node, bad argument): shown to the agent."""


@dataclass
class Tools:
    driver: Driver
    database: str
    session: Session  # Postgres: repositories, for links and reading code
    _endpoint_cache: list[dict[str, Any]] | None = field(default=None, init=False, repr=False)

    # ------------------------------------------------------------------ helpers

    def _read(self, query: str, **params: Any) -> list[Any]:
        with self.driver.session(database=self.database) as s:
            return list(s.run(query, **params))

    @property
    def views(self) -> GraphViews:
        return GraphViews(self.driver, self.database)

    def _repo(self, name: str | None) -> Repository | None:
        if not name:
            return None
        return self.session.scalar(select(Repository).where(Repository.name == name))

    def _cite(
        self,
        claim: str,
        repo: str | None,
        commit: str | None,
        file: str | None,
        line: int | None = None,
        end: int | None = None,
    ) -> dict[str, Any]:
        row = self._repo(repo)
        lines = f"{line}-{end}" if line and end and end != line else (str(line) if line else None)
        return {
            k: v
            for k, v in {
                "claim": claim,
                "repo": repo,
                "file": file,
                "lines": lines,
                "commit": commit[:12] if commit else None,
                "url": source_url(row.clone_url if row else None, commit, file, line),
            }.items()
            if v is not None
        }

    def _resolve(self, ref: str) -> Any:
        """A node by stable ID, or by name (the most specific match)."""
        rows = self._read("MATCH (n {id: $ref}) RETURN n LIMIT 1", ref=ref)
        if not rows:
            rows = self._read(
                "MATCH (n) WHERE n.name = $ref AND n.id IS NOT NULL "
                "RETURN n ORDER BY CASE WHEN n:Searchable THEN 0 ELSE 1 END, size(n.id) LIMIT 1",
                ref=ref,
            )
        if not rows:
            raise ToolError(f"nothing in the graph is called {ref!r}; try search first")
        return rows[0]["n"]

    def _where(self, node_id: str) -> dict[str, str]:
        """The application and space a node lives in."""
        out: dict[str, str] = {}
        for c in self.views._breadcrumbs(node_id)[:-1]:
            if c["kind"] in ("space", "application") and c["kind"] not in out:
                out[c["kind"]] = c["label"]
            elif c["kind"] == "space":
                out["space"] = c["label"]  # the innermost space
        return out

    # ------------------------------------------------------------------- search

    def search(self, query: str, scope: str | None = None, limit: int = 10) -> dict[str, Any]:
        hits = self.views.search(query, limit=max(limit * 3, 20))
        out = []
        for h in hits:
            if not h.get("id"):
                continue
            where = self._where(h["id"])
            if scope and scope not in where.values() and scope != h["id"]:
                continue
            hit = {"id": h["id"], "kind": h["kind"], "name": h["label"], **where}
            if h.get("sub"):
                hit["matched"] = f"function {h['label']} {h['sub']}"
            out.append(hit)
            if len(out) >= limit:
                break
        return {
            "query": query,
            "searched": [scope] if scope else ["organization"],
            "results": out,
            "next": [
                "get_node(id) for an application, space, or interface",
                "get_flow(id) for a flow's steps",
            ]
            if out
            else ["Try a shorter or different word: search matches names, not meaning yet."],
        }

    # ----------------------------------------------------------------- get_node

    def get_node(self, ref: str) -> dict[str, Any]:
        node = self._resolve(ref)
        kind = kind_of(list(node.labels))
        if kind == "flow":
            return self.get_flow(node["id"], detail="significant", expand="none", summary=True)
        if kind == "application":
            return self._application(node)
        if kind in ("organization", "space"):
            return self._level(node, kind)
        return self._other(node, kind)

    def _application(self, app: Any) -> dict[str, Any]:
        app_id = app["id"]
        flows = []
        citations = []
        for row in self._read(
            """
            MATCH (fl:Flow)-[:BELONGS_TO]->(:Application {id: $id})
            OPTIONAL MATCH (t)-[:STARTS]->(fl)
            OPTIONAL MATCH (fl)-[r]->(x) WHERE r.rollup AND type(r) IN $effects
            RETURN fl, t, collect(DISTINCT {type: type(r), name: x.name}) AS touches
            ORDER BY fl.name
            """,
            id=app_id,
            effects=EFFECTS,
        ):
            fl, trig = row["fl"], row["t"]
            touches = defaultdict(list)
            for t in row["touches"]:
                if t["type"]:
                    touches[VERB[t["type"]]].append(t["name"])
            flows.append(
                {
                    "id": fl["id"],
                    "trigger": fl.get("trigger"),
                    "trigger_kind": kind_of(list(trig.labels)) if trig is not None else None,
                    "steps": fl.get("steps"),
                    **{verb: sorted(set(names)) for verb, names in touches.items()},
                }
            )
            citations.append(
                self._cite(
                    f"{fl.get('trigger')} is handled by {fl.get('entry_symbol')}",
                    fl.get("repo"),
                    fl.get("commit"),
                    fl.get("entry_file"),
                    fl.get("entry_start_line"),
                )
            )
        data = defaultdict(set)
        outbound = defaultdict(set)
        for f in flows:
            for t in f.get("reads", []):
                data["reads"].add(t)
            for t in f.get("writes", []):
                data["writes"].add(t)
            for t in f.get("calls", []):
                outbound["calls"].add(t)
        modules = [
            {"path": r["m"]["path"], "roles": [lbl for lbl in r["m"].labels if lbl != "Module"]}
            for r in self._read(
                "MATCH (:Application {id: $id})-[:BUILT_FROM]->(m:Module) RETURN m", id=app_id
            )
        ]
        return {
            "id": app_id,
            "kind": "application",
            "name": app.get("name"),
            **self._where(app_id),
            "flows": flows,
            "data": {k: sorted(v) for k, v in data.items()},
            "outbound": {k: sorted(v) for k, v in outbound.items()},
            "modules": modules,
            "citations": citations,
            "next": [
                "get_flow(flow id) for what a flow does, step by step",
                "find_dependents(id) for who depends on this application",
            ],
        }

    def _level(self, node: Any, kind: str) -> dict[str, Any]:
        level = self.views.level(node["id"] if kind == "space" else None)
        names = {n["id"]: n["label"] for n in level["nodes"]}
        children = [
            {
                "id": n["id"],
                "kind": n["kind"],
                "name": n["label"],
                **({"description": n["description"]} if n.get("description") else {}),
                **({"stats": n["stats"]} if n.get("stats") else {}),
            }
            for n in level["nodes"]
            if not n.get("outside") and n["kind"] != "external"
        ]
        links = [
            {
                "from": names.get(e["src"], e["src"]),
                "to": names.get(e["dst"], e["dst"]),
                "how": VERB.get(e["type"], e["type"].lower()),
                "count": e.get("n", 1),
            }
            for e in level["edges"]
        ]
        outside = [
            n["label"] for n in level["nodes"] if n.get("outside") or n["kind"] == "external"
        ]
        return {
            "id": node["id"],
            "kind": kind,
            "name": node.get("name"),
            **({"description": node.get("description")} if node.get("description") else {}),
            "contains": children,
            "communication": links,
            **({"talks_to_outside": outside} if outside else {}),
            "citations": [],
            "next": ["get_node(id) on any of these for its own view"],
        }

    def _other(self, node: Any, kind: str) -> dict[str, Any]:
        detail = self.views.node(node["id"])
        edges: dict[str, list[str]] = defaultdict(list)
        for n in detail["neighbors"]:
            label = n["type"].lower() if n["dir"] == "out" else f"{n['type'].lower()} (in)"
            edges[label].append(f"{n['node']['label']} [{n['node']['id']}]")
        prov = detail["provenance"]
        return {
            "id": node["id"],
            "kind": kind,
            "name": detail["label"],
            **self._where(node["id"]),
            "properties": detail["properties"],
            "edges": {k: sorted(v)[:40] for k, v in edges.items()},
            "citations": [
                self._cite(
                    f"{detail['label']} is defined here",
                    prov.get("repo"),
                    prov.get("commit"),
                    prov.get("source_file"),
                    prov.get("source_line"),
                )
            ]
            if prov.get("source_file")
            else [],
            "next": ["find_dependents(id) for everything that uses this"],
        }

    # ----------------------------------------------------------------- get_flow

    def get_flow(
        self,
        ref: str,
        detail: str = "significant",
        expand: str = "none",
        summary: bool = False,
        _depth: int = 0,
        levels: int | None = None,
        under: str | None = None,
    ) -> dict[str, Any]:
        """A flow's steps in order. `detail`: significant (its steps) or all (the whole
        trace). `expand`: none, sync (follow calls that wait for another app's flow), or all
        (also messages it produces). Like the UI, only `levels` levels are shown (by default,
        as many as fit in about OUTLINE steps): a deeper branch is folded into the step above
        it, which says how many steps are inside and what they touch. `under`: a step's
        path, to show only that branch."""
        if detail not in ("significant", "all") or expand not in ("none", "sync", "all"):
            raise ToolError("detail is significant|all; expand is none|sync|all")
        fl = self._resolve(ref)
        if "Flow" not in fl.labels:
            raise ToolError(f"{ref!r} is a {kind_of(list(fl.labels))}, not a flow")
        flow_id = fl["id"]
        trace = json.loads(fl.get("trace") or "[]")
        repo, commit = fl.get("repo"), fl.get("commit")

        # Effects with their call sites, per step
        effects: dict[str, list[dict[str, Any]]] = defaultdict(list)
        targets: dict[str, Any] = {}
        for row in self._read(
            """
            MATCH (s:Step)-[:BELONGS_TO]->(:Flow {id: $id})
            MATCH (s)-[r]->(t) WHERE type(r) IN $effects
            OPTIONAL MATCH (t)-[:STARTS]->(next:Flow)
            RETURN s.id AS step, type(r) AS type, t, r.operation AS op, r.url AS url,
                   r.method AS method,
                   r.source_file AS file, r.source_line AS line, r.call_sites AS sites,
                   collect(next.id) AS leads
            """,
            id=flow_id,
            effects=EFFECTS,
        ):
            t = row["t"]
            targets[t["id"]] = t
            effects[row["step"]].append(
                {
                    "type": row["type"],
                    "target": t.get("name"),
                    "target_id": t["id"],
                    "method": row["method"],
                    "unknown_host": bool(t.get("stub") and t.get("called_from")),
                    "host_note": t.get("host_note"),
                    "op": row["op"],
                    "url": row["url"],
                    "file": row["file"],
                    "line": row["line"],
                    "sites": _sites(row["sites"], row["line"], row["op"]),
                    "leads_to": [f for f in row["leads"] if f],
                }
            )

        steps = []
        citations = [
            self._cite(
                f"entry: {fl.get('entry_symbol')}",
                repo,
                commit,
                fl.get("entry_file"),
                fl.get("entry_start_line"),
                fl.get("entry_end_line"),
            )
        ]
        leads: list[dict[str, Any]] = []
        shown: dict[str, dict[str, Any]] = {}
        for t in trace:
            if detail == "significant" and ("step" not in t or t.get("repeat")):
                # A call Steno couldn't follow stays visible on the nearest step above (D71)
                holder = _nearest(shown, t["path"])
                if holder is not None:
                    holder.setdefault("stops", []).extend(_stops(t, of=t["name"]))
                continue
            entry: dict[str, Any] = {
                "path": t["path"],
                "function": t["symbol"] + (f" (as {t['bound_to']})" if t.get("bound_to") else ""),
                "at": f"{t['file']}:{t['start_line']}-{t['end_line']}",
            }
            flags = [
                name
                for name, on in (
                    ("if", t.get("conditional")),
                    ("loop", t.get("in_loop")),
                    ("async", t.get("async")),
                    ("one of several implementations", t.get("candidate")),
                    ("helper, not expanded", t.get("utility")),
                    ("called again, see above", t.get("repeat")),
                )
                if on
            ]
            if flags:
                entry["flags"] = flags
            if t.get("unresolved"):
                entry["stops"] = _stops(t)
            shown[t["path"]] = entry
            if "step" in t:
                for e in effects.get(step_of(flow_id, t), []):
                    does = f"{VERB[e['type']]} {e['target']}" + (f" ({e['op']})" if e["op"] else "")
                    if e["url"]:
                        does += f" {e['url']}"
                    entry.setdefault("does", []).append(does)
                    if e["unknown_host"]:
                        # Not a fact: what's known about the host, and endpoints elsewhere whose
                        # method and path match, for the reader to weigh
                        if e["host_note"]:
                            entry.setdefault("host_unknown", []).append(e["host_note"])
                        for hint in self._path_matches(e["method"], e["url"], flow_id):
                            if hint not in entry.setdefault("possible_targets", []):
                                entry["possible_targets"].append(hint)
                    for line, op in e["sites"]:  # every place it happens, not just the first
                        claim = f"{VERB[e['type']]} {e['target']}" + (f" ({op})" if op else "")
                        entry.setdefault("_cites", []).append(
                            self._cite(claim, repo, commit, e["file"], line)
                        )
                    for nxt in e["leads_to"]:
                        sync = e["type"] == "CALLS"
                        leads.append(
                            {"at": t["path"], "flow": nxt, "mode": "sync" if sync else "async"}
                        )
                        if (expand == "all" or (expand == "sync" and sync)) and _depth < 3:
                            entry.setdefault("then", []).append(
                                self.get_flow(nxt, detail, expand, summary=True, _depth=_depth + 1)
                            )
            steps.append(entry)

        all_steps = len(steps)
        if levels is None:  # as deep as fits in about OUTLINE steps
            levels = 1
            while levels < 12 and len(_fold(steps, levels + 1, under)) <= OUTLINE:
                levels += 1
        steps = _fold(steps, levels, under)
        if under and not steps:
            raise ToolError(f"no step at path {under!r}; get_flow lists the paths")
        for entry in steps:  # cite what's shown; folded steps are summarized, not cited
            citations += entry.pop("_cites", [])
        totals: dict[str, set[str]] = defaultdict(set)
        for es in effects.values():
            for e in es:
                totals[VERB[e["type"]]].add(e["target"])
        out: dict[str, Any] = {
            "id": flow_id,
            "kind": "flow",
            "trigger": fl.get("trigger"),
            **(
                {
                    "inferred": "nothing in the code starts this function; Steno treats it as an "
                    "entry point because it does real work (a worker loop, a command, a callback "
                    "a framework registers)"
                }
                if fl.get("inferred")
                else {}
            ),
            **self._where(flow_id),
            **({"purpose": fl.get("purpose")} if fl.get("purpose") else {}),
            "touches": {verb: sorted(names) for verb, names in totals.items()},
            "functions": fl.get("functions"),
            "shown": f"{len(steps)} of {all_steps} steps"
            + (f" under {under}" if under else "")
            + (
                f"; deeper ones folded (levels={levels})"
                if any("inside" in x for x in steps)
                else ""
            ),
            "steps": steps,
            **(
                {
                    "incomplete": f"{fl.get('unresolved_calls')} calls Steno couldn't follow; "
                    "the flow may continue past them (see each step's `stops`)"
                }
                if fl.get("incomplete")
                else {}
            ),
            **({"leads_to": leads} if leads else {}),
            "citations": citations,
        }
        if not summary:
            out["next"] = [
                "get_flow(id, under=path) to open a folded step (one with `inside`)",
                "view_flow_code(id, [paths]) for the code of the steps you care about",
                "get_flow(id, detail='all') for every function, helpers included",
                *(
                    ["get_flow(id, expand='sync') to follow calls into other applications"]
                    if leads
                    else []
                ),
            ]
        return out

    # ----------------------------------------------------------- view_flow_code

    def view_flow_code(self, ref: str, paths: list[str] | None = None) -> dict[str, Any]:
        """The code of a flow's steps (or of the trace entries at `paths`), read from the git
        host at the commit the flow was ingested from."""
        fl = self._resolve(ref)
        if "Flow" not in fl.labels:
            raise ToolError(f"{ref!r} isn't a flow")
        trace = json.loads(fl.get("trace") or "[]")
        wanted = set(paths or [])
        picked = [
            t
            for t in trace
            if (t["path"] in wanted if wanted else ("step" in t and not t.get("repeat")))
        ]
        if wanted and not picked:
            raise ToolError(f"no trace entries at {sorted(wanted)}; get_flow lists the paths")
        missing = sorted(wanted - {t["path"] for t in picked})
        repo = self._repo(fl.get("repo"))
        if repo is None:
            raise ToolError(f"repository {fl.get('repo')!r} isn't registered")
        connector = self.session.get(Connector, repo.connector_id)
        commit = fl.get("commit")
        code, seen, errors = [], set(), []
        for t in picked:
            key = (t["file"], t["start_line"])
            if key in seen:
                continue
            seen.add(key)
            try:
                text = read_file(
                    repo.clone_url,
                    connector.kind,  # type: ignore[union-attr]
                    connector.credentials_ref,  # type: ignore[union-attr]
                    commit,
                    t["file"],
                )
            except SourceError as exc:
                errors.append(str(exc))
                continue
            code.append(
                {
                    "path": t["path"],
                    "function": t["symbol"],
                    "file": t["file"],
                    "lines": f"{t['start_line']}-{t['end_line']}",
                    "code": slice_lines(text, t["start_line"], t["end_line"]),
                }
            )
        return {
            "id": fl["id"],
            "commit": commit,
            "code": code,
            **({"not_found": missing} if missing else {}),
            **({"errors": errors} if errors else {}),
            "citations": [
                self._cite(c["function"], fl.get("repo"), commit, c["file"], *_span(c["lines"]))
                for c in code
            ],
        }

    # ---------------------------------------------------------- find_dependents

    def find_dependents(
        self, ref: str, direction: str = "upstream", depth: int = 2, via: str | None = None
    ) -> dict[str, Any]:
        """Who depends on a node (upstream: who calls, reads, writes, or sends to it), or what
        it depends on (downstream: what its flows touch, and the flows that starts), grouped
        by application and space."""
        if direction not in ("upstream", "downstream"):
            raise ToolError("direction is upstream|downstream")
        if via is not None and via not in VIA:
            raise ToolError(f"via is one of {sorted(VIA)}")
        types = sorted(VIA[via]) if via else EFFECTS
        node = self._resolve(ref)
        kind = kind_of(list(node.labels))
        start = node["id"]

        # The flows to start from, and the targets a dependency is "through"
        found: dict[str, dict[str, Any]] = {}
        frontier: list[tuple[str, int]] = []
        citations: list[dict[str, Any]] = []

        def add(touch: dict[str, Any], hops: int) -> bool:
            """Record how a flow depends on its target. A flow that both reads and writes a
            table is a reader *and* a writer: every way it touches the target is kept."""
            flow = touch["flow"]
            new = flow not in found
            entry = found.setdefault(flow, {"how": [], "through": touch["target"], "hops": hops})
            if touch["how"] not in entry["how"]:
                entry["how"].append(touch["how"])
            if touch["file"]:
                claim = f"{touch['trigger']} {touch['how']} {touch['target']}"
                citations.append(
                    self._cite(claim, touch["repo"], touch["commit"], touch["file"], touch["line"])
                )
            return new

        if direction == "upstream":
            targets = self._targets_of(start, kind)
            for touch in self._flows_touching(targets, types):
                if add(touch, 1):
                    frontier.append((touch["flow"], 1))
            while frontier:
                flow, hops = frontier.pop()
                if hops >= depth:
                    continue
                for touch in self._flows_touching(self._triggers(flow), types):
                    if touch["flow"] not in found and add(touch, hops + 1):
                        frontier.append((touch["flow"], hops + 1))
                    elif found[touch["flow"]]["hops"] == hops + 1:
                        add(touch, hops + 1)
        else:
            flows = self._flows_of(start, kind)
            frontier = [(f, 0) for f in flows]
            seen = set(flows)
            touched: dict[str, dict[str, Any]] = {}
            while frontier:
                flow, hops = frontier.pop()
                for edge, target, started in self._touches(flow, types):
                    touched.setdefault(
                        f"{edge}|{target['id']}", {"name": target["name"], "how": VERB[edge]}
                    )
                    for nxt in started:
                        if nxt not in seen and hops + 1 <= depth:
                            seen.add(nxt)
                            found[nxt] = {
                                "how": [VERB[edge]],
                                "through": target["name"],
                                "hops": hops + 1,
                            }
                            frontier.append((nxt, hops + 1))
        grouped = self._group(found)
        out: dict[str, Any] = {
            "id": start,
            "kind": kind,
            "direction": direction,
            **({"via": via} if via else {}),
            "dependents" if direction == "upstream" else "downstream_flows": grouped,
            "citations": _unique(citations),
            "next": ["get_flow(flow id) for how a dependent uses it"],
        }
        if direction == "downstream":
            out["touches"] = sorted(
                (f"{v['how']} {v['name']}" for v in touched.values()), key=str.lower
            )
        return out

    def _targets_of(self, node_id: str, kind: str) -> list[str]:
        """What "depending on" a node means: the node itself, or for an application its
        interfaces, and for a flow the interface that starts it."""
        if kind == "application":
            return [
                r["i"]
                for r in self._read(
                    "MATCH (:Application {id: $id})-[:EXPOSES]->(i) RETURN i.id AS i", id=node_id
                )
            ]
        if kind == "flow":
            return self._triggers(node_id)
        return [node_id]

    def _triggers(self, flow_id: str) -> list[str]:
        return [
            r["t"]
            for r in self._read(
                "MATCH (t)-[:STARTS]->(:Flow {id: $id}) RETURN t.id AS t", id=flow_id
            )
        ]

    def _path_matches(self, method: str | None, url: str | None, flow_id: str) -> list[str]:
        """Endpoints in other applications whose method and path match a call whose host isn't
        known: `possibly POST /suggestion in budgeting-app-inference`. A hint, never an edge:
        a matching path alone doesn't say which service a call reaches (D64)."""
        if not method or not url:
            return []
        _, path = split_url(url)
        if not path or path == "/":
            return []
        own = self._where(flow_id).get("application")
        want = _path_shape(path)
        out = []
        for e in self._endpoints():
            if e["method"] != method.upper() or e["app"] == own:
                continue
            have = _path_shape(e["path"])
            exact = have == want
            if exact or (len(have) > 1 and want.endswith(have)):  # the base URL adds a prefix
                how = "path matches" if exact else "path ends the same way"
                flow = f"; its flow: {e['flow']}" if e["flow"] else ""
                out.append(
                    f"possibly {e['method']} {e['path']} in {e['app']} ({how}; host unknown){flow}"
                )
        return out

    def _endpoints(self) -> list[dict[str, Any]]:
        if self._endpoint_cache is None:
            self._endpoint_cache = [
                dict(r)
                for r in self._read(
                    """
                    MATCH (a:Application)-[:EXPOSES]->(e:HttpEndpoint)
                    OPTIONAL MATCH (e)-[:STARTS]->(f:Flow)
                    RETURN e.method AS method, e.path AS path, a.name AS app, f.id AS flow
                    """
                )
            ]
        return self._endpoint_cache

    def _flows_touching(self, targets: list[str], types: list[str]) -> list[dict[str, Any]]:
        """Every way a flow touches the targets: one row per operation and call site, from the
        flow's steps, which carry the file and line of the code that does it."""
        if not targets:
            return []
        out = []
        for r in self._read(
            """
            MATCH (fl:Flow)<-[:BELONGS_TO]-(:Step)-[r]->(t)
            WHERE t.id IN $ids AND type(r) IN $types
            RETURN DISTINCT fl.id AS fl, fl.trigger AS trigger, type(r) AS type,
                   r.operation AS op, t.name AS target, r.source_file AS file,
                   r.source_line AS line, r.call_sites AS sites, fl.repo AS repo,
                   fl.commit AS commit
            ORDER BY fl, type, file, line
            """,
            ids=targets,
            types=types,
        ):
            for line, op in _sites(r["sites"], r["line"], r["op"]):
                out.append(
                    {
                        "flow": r["fl"],
                        "trigger": r["trigger"],
                        "how": VERB[r["type"]] + (f" ({op})" if op else ""),
                        "target": r["target"],
                        "file": r["file"],
                        "line": line,
                        "repo": r["repo"],
                        "commit": r["commit"],
                    }
                )
        return out

    def _flows_of(self, node_id: str, kind: str) -> list[str]:
        if kind == "flow":
            return [node_id]
        if kind == "application":
            query = "MATCH (fl:Flow)-[:BELONGS_TO]->(:Application {id: $id}) RETURN fl.id AS f"
        else:
            query = "MATCH ({id: $id})-[:STARTS]->(fl:Flow) RETURN fl.id AS f"
        return [r["f"] for r in self._read(query, id=node_id)]

    def _touches(self, flow_id: str, types: list[str]) -> list[tuple[str, Any, list[str]]]:
        return [
            (
                r["type"],
                {"id": r["t"]["id"], "name": r["t"].get("name")},
                [n for n in r["next"] if n],
            )
            for r in self._read(
                """
                MATCH (:Flow {id: $id})-[r]->(t) WHERE r.rollup AND type(r) IN $types
                OPTIONAL MATCH (t)-[:STARTS]->(n:Flow)
                RETURN type(r) AS type, t, collect(n.id) AS next
                """,
                id=flow_id,
                types=types,
            )
        ]

    def _group(self, found: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
        """Flows grouped by application and space, nearest first."""
        if not found:
            return []
        groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for row in self._read(
            """
            MATCH (fl:Flow) WHERE fl.id IN $ids
            OPTIONAL MATCH (fl)-[:BELONGS_TO]->(a:Application)
            RETURN fl.id AS id, fl.trigger AS trigger, a.id AS app
            """,
            ids=list(found),
        ):
            where = self._where(row["id"])
            key = (where.get("application", row["app"] or "?"), where.get("space", "no space"))
            groups[key].append({"flow": row["id"], "trigger": row["trigger"], **found[row["id"]]})
        return [
            {"application": app, "space": space, "flows": sorted(fs, key=lambda f: f["hops"])}
            for (app, space), fs in sorted(
                groups.items(), key=lambda kv: min(f["hops"] for f in kv[1])
            )
        ]


def _path_shape(path: str) -> str:
    """A path with its parameters blanked, for matching a call to a route:
    `/users/{id}/` and `/users/{userId}` → `/users/{}`."""
    shape = re.sub(r"\{[^}]*\}", "{}", path.split("?", 1)[0]).rstrip("/")
    return shape if shape.startswith("/") else "/" + shape


def _sites(sites: list[str] | None, line: int | None, op: str | None) -> list[tuple[Any, Any]]:
    """An effect's call sites as (line, operation): from `call_sites` ("120 upsert"), or the
    single line and operation of an edge written before call sites were kept."""
    if sites:
        out = []
        for site in sites:
            at, _, how = site.partition(" ")
            out.append((int(at) if at.isdigit() else None, how or None))
        return out
    return [(line, op)]


def _unique(citations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen, out = set(), []
    for c in citations:
        key = (c.get("claim"), c.get("file"), c.get("lines"))
        if key not in seen:
            seen.add(key)
            out.append(c)
    return out


def _fold(steps: list[dict[str, Any]], levels: int, under: str | None) -> list[dict[str, Any]]:
    """Steps `levels` deep (from the top, or from `under`), each deeper one folded into the
    nearest shown step above it: `inside` counts them, `inside_does` sums what they do."""
    if under:
        steps = [s for s in steps if s["path"] == under or s["path"].startswith(under + ".")]
    paths = {s["path"] for s in steps}
    level: dict[str, int] = {}
    parent: dict[str, str | None] = {}
    for s in steps:  # in trace order, so a parent comes before its children
        p = s["path"].rsplit(".", 1)[0] if "." in s["path"] else None
        while p is not None and p not in paths:
            p = p.rsplit(".", 1)[0] if "." in p else None
        if under and s["path"] == under:
            p = None
        parent[s["path"]] = p
        level[s["path"]] = level[p] + 1 if p is not None else 0
    shown: list[dict[str, Any]] = []
    holder: dict[str, str] = {}  # path → the shown step it's folded into
    by_path = {}
    for s in steps:
        s = {k: v for k, v in s.items() if k != "_cites"} if level[s["path"]] >= levels else s
        p = parent[s["path"]]
        if level[s["path"]] < levels:
            entry = dict(s)
            shown.append(entry)
            by_path[s["path"]] = entry
            holder[s["path"]] = s["path"]
            continue
        into = holder[p] if p is not None else s["path"]
        holder[s["path"]] = into
        target = by_path[into]
        target["inside"] = target.get("inside", 0) + 1
        for d in s.get("does", []):
            if d not in target.setdefault("inside_does", []):
                target["inside_does"].append(d)
        for k in ("stops", "host_unknown", "possible_targets"):  # stay visible when folded
            for d in s.get(k, []):
                if d not in target.setdefault(k, []):
                    target[k].append(d)
    return shown


def _nearest(shown: dict[str, dict[str, Any]], path: str) -> dict[str, Any] | None:
    """The shown step nearest above a trace path."""
    while "." in path:
        path = path.rsplit(".", 1)[0]
        if path in shown:
            return shown[path]
    return None


def _stops(t: dict[str, Any], of: str | None = None) -> list[str]:
    """Where a function's calls stop being followed (D71): `webClient.post().uri(…) at line 61:
    library type (org.springframework.web)`."""
    out = []
    for g in t.get("unresolved", []):
        why = g["reason"] + (f" ({g['library']})" if g.get("library") else "")
        out.append(f"{f'in {of}: ' if of else ''}{g['call']} at line {g['line']}: {why}")
    return out


def _span(lines: str) -> tuple[int | None, int | None]:
    a, _, b = lines.partition("-")
    return (int(a) if a else None, int(b) if b else None)


def fit(result: dict[str, Any], max_tokens: int) -> dict[str, Any]:
    """Cut a result to about `max_tokens`: the longest lists lose items from the end until it
    fits, and `truncated` says what was left out."""
    budget = max_tokens * CHARS_PER_TOKEN

    def size(obj: Any) -> int:
        return len(json.dumps(obj, default=str))

    if size(result) <= budget:
        return result
    cut: dict[str, int] = {}
    lists = [k for k, v in result.items() if isinstance(v, list) and k not in ("next",)]
    while size(result) > budget and lists:
        longest = max(lists, key=lambda k: size(result[k]))
        if not result[longest]:
            lists.remove(longest)
            continue
        result[longest] = result[longest][:-1]
        cut[longest] = cut.get(longest, 0) + 1
    if cut:
        result["truncated"] = {k: f"{n} more not shown; raise max_tokens" for k, n in cut.items()}
    return result
