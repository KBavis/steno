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
from steno.claims.store import active
from steno.connectors.source import SourceError, read_file, slice_lines
from steno.db.models import Connector, Repository
from steno.extraction.facts import split_url
from steno.graph.build import step_of
from steno.graph.views import GraphViews, kind_of, search_words

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
# What each agreement state means, for the agent (claims-and-reconcile.md §2)
AGREEMENT = {
    "confirmed": "the code and another witness both say so",
    "code_only": "in the code; no other witness states it (e.g. no ACL grants it): likely to "
    "fail where it's deployed, or the declaration is missing",
    "declared_only": "another witness states it; no code doing it was found: an unused "
    "grant, an application not ingested, or a rule that missed it",
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
        """A node by stable ID, or by name (the most specific match). `organization` is the
        organization, whatever it's called (a table named `organization` is not)."""
        if ref.strip().lower() in ("organization", "org"):
            rows = self._read("MATCH (n:Organization) RETURN n LIMIT 1")
            if rows:
                return rows[0]["n"]
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
        """Top-down (D2): first where the matches are (every space and application with any,
        and how many), then the hits, each application's best in turn, so one application
        with many matches can't crowd the others off the page."""
        names, functions = self.views.search_hits(query, names_limit=max(limit * 3, 30))
        words = search_words(query)
        hits: list[dict[str, Any]] = []
        seen: set[str] = set()
        for h in names:
            if not h.get("id") or h["id"] in seen:
                continue
            seen.add(h["id"])
            hits.append(
                {"id": h["id"], "kind": h["kind"], "name": h["label"], **self._where(h["id"])}
            )
        for f in functions:
            if f["id"] in seen:  # named and running a matching function: one hit
                continue
            seen.add(f["id"])
            hits.append(
                {
                    "id": f["id"],
                    "kind": "flow",
                    "name": f["label"],
                    **({"space": f["space"]} if f["space"] else {}),
                    **({"application": f["application"]} if f["application"] else {}),
                    "matched": _matched(f, words),
                }
            )
        if scope:
            hits = [h for h in hits if scope in (h.get("space"), h.get("application"), h["id"])]
        shown = _interleave(hits, limit)
        rest = len(hits) - len(shown)
        return {
            "query": query,
            "searched": [scope] if scope else ["organization"],
            **({"where": _where_summary(hits)} if hits else {}),
            "results": shown,
            **(
                {
                    "more": f"{rest} more match{'es' if rest != 1 else ''}: `where` shows every "
                    "application and space with matches; narrow with scope=<application or space>"
                }
                if rest
                else {}
            ),
            "next": [
                "get_node(id) for an application, space, or interface",
                "get_flow(id) for a flow's steps",
            ]
            if shown
            else ["Try a shorter or different word: search matches names, not meaning yet."],
        }

    # ----------------------------------------------------------------- get_node

    def get_node(self, ref: str, claims: bool = False) -> dict[str, Any]:
        node = self._resolve(ref)
        kind = kind_of(list(node.labels))
        if kind == "flow":
            out = self.get_flow(node["id"], detail="significant", expand="none", summary=True)
        elif kind == "application":
            out = self._application(node)
        elif kind in ("organization", "space"):
            out = self._level(node, kind)
        else:
            out = self._other(node, kind)
        if claims:
            out["claims"] = self._claims(node)
        if node["id"] != ref:  # found by name: say so when other nodes have that name too
            others = [
                {"id": r["id"], "kind": kind_of(r["labels"])}
                for r in self._read(
                    "MATCH (n) WHERE n.name = $name AND n.id <> $id AND n.id IS NOT NULL "
                    "RETURN n.id AS id, labels(n) AS labels LIMIT 10",
                    name=ref,
                    id=node["id"],
                )
            ]
            if others:
                out["also_named"] = {
                    "note": f"other nodes are named {ref!r} too; get_node(id) on the one meant",
                    "nodes": others,
                }
        return out

    # ------------------------------------------------- witnesses (D109, claims)

    def _uses(self, node_id: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Reconciled uses of channels by applications (an application's, or a channel's):
        each with its agreement and one citation per witness."""
        uses, citations = [], []
        for r in self._read(
            "MATCH (a:Application)-[r]->(c:Interface) WHERE r.repo = 'reconcile' "
            "AND (a.id = $id OR c.id = $id) "
            "RETURN a.id AS app_id, a.name AS app, a.stub AS stub, type(r) AS type, "
            "c.id AS channel_id, c.name AS channel, r.agreement AS agreement, "
            "r.sources AS sources ORDER BY channel, app",
            id=node_id,
        ):
            role = "produces to" if r["type"] == "PRODUCES" else "consumes from"
            sources = json.loads(r["sources"] or "[]")
            uses.append(
                {
                    "application": r["app"],
                    **({"application_ingested": False} if r["stub"] else {}),
                    "role": role,
                    "channel": r["channel"],
                    "agreement": r["agreement"],
                    "witnesses": sorted({_witness(s) for s in sources}),
                }
            )
            for s in sources:
                citations.append(
                    self._cite(
                        f"{r['app']} {role} {r['channel']}: {s['witness_type']}",
                        s.get("repo"),
                        s.get("commit"),
                        s.get("file"),
                        s.get("line"),
                    )
                )
        return uses, citations

    def _claims(self, node: Any) -> list[dict[str, Any]]:
        """The raw claims behind a node: what other witnesses state about it."""
        kind = kind_of(list(node.labels))
        if kind == "application":
            names = {node.get("name"), *(node.get("aliases") or [])} - {None}
            subjects = {f"application:{n}" for n in names}
        else:
            subjects = {f"channel:{node['id']}"}
        repos = {r.id: r.name for r in self.session.scalars(select(Repository))}
        return [
            {
                "claim": c.claim_type,
                **c.fields,
                "witness_type": c.witness_type,
                "witness": c.witness,
                "citation": self._cite(
                    c.claim_type, repos.get(c.repository_id), c.commit, c.file, c.line
                ),
            }
            for c in active(self.session, subjects)
        ]

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
                    **({"access": fl.get("access")} if fl.get("access") else {}),
                    **({"inferred": True} if fl.get("inferred") else {}),
                    **({"incomplete": True} if fl.get("incomplete") else {}),
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
        uses, use_citations = self._uses(app_id)
        citations += use_citations
        names = {
            k: v
            for k, v in {
                "display": app.get("display_name"),
                "aliases": app.get("aliases"),
                # Named only by its folder: nothing states a real name yet
                "provisional": app.get("provisional_name"),
            }.items()
            if v and v != app.get("name")
        }
        return {
            "id": app_id,
            "kind": "application",
            "name": app.get("name"),
            **({"names": names} if names else {}),
            **(
                {
                    "ingested": False,
                    "note": app.get("note") or "known only from another witness",
                }
                if app.get("stub")
                else {}
            ),
            **self._where(app_id),
            "flows": flows,
            **({"messaging": uses, "agreement_means": _meanings(uses)} if uses else {}),
            "data": {k: sorted(v) for k, v in data.items()},
            "outbound": {k: sorted(v) for k, v in outbound.items()},
            "modules": modules,
            "completeness": {
                **_hooks_note(app.get("unknown_hooks") or []),
                "incomplete_flows": sum(1 for f in flows if f.get("incomplete")),
                "inferred_flows": sum(1 for f in flows if f.get("inferred")),
            },
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
        channel = kind == "interface" and not ({"HttpEndpoint", "GrpcMethod"} & set(node.labels))
        uses, use_citations = self._uses(node["id"]) if channel else ([], [])
        return {
            "id": node["id"],
            "kind": kind,
            "name": detail["label"],
            **self._where(node["id"]),
            "properties": detail["properties"],
            **(
                {"uses": uses, "agreement_means": _meanings(uses)}
                if uses
                else {
                    "agreement": "not checked: no platform or declared witness mentions this "
                    "channel, so its producers and consumers come from the code alone"
                }
                if channel
                else {}
            ),
            "edges": {k: sorted(v)[:40] for k, v in edges.items()},
            "citations": (
                [
                    self._cite(
                        f"{detail['label']} is defined here",
                        prov.get("repo"),
                        prov.get("commit"),
                        prov.get("source_file"),
                        prov.get("source_line"),
                    )
                ]
                if prov.get("source_file")
                else []
            )
            + use_citations,
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
                    (f"runs before the handler ({t.get('interceptor')})", t.get("interceptor")),
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
            **({"access": fl.get("access")} if fl.get("access") else {}),
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
            "completeness": self._flow_completeness(
                fl,
                sorted({e["target"] for es in effects.values() for e in es if e["unknown_host"]}),
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
        # What the answer can't vouch for: unknown framework hooks in the applications found
        # (a request filter reading this table would be missing from the list)
        hooks: list[str] = []
        for row in self._read(
            "MATCH (fl:Flow)-[:BELONGS_TO]->(a:Application) WHERE fl.id IN $ids "
            "RETURN DISTINCT a.name AS app, a.unknown_hooks AS h",
            ids=list(found),
        ):
            hooks += [f"{row['app']}: {h}" for h in row["h"] or []]
        incomplete = sum(
            1
            for r in self._read(
                "MATCH (fl:Flow) WHERE fl.id IN $ids AND fl.incomplete RETURN fl.id AS id",
                ids=list(found),
            )
        )
        out: dict[str, Any] = {
            "id": start,
            "kind": kind,
            "direction": direction,
            **({"via": via} if via else {}),
            "dependents" if direction == "upstream" else "downstream_flows": grouped,
            "completeness": {
                **_hooks_note(hooks),
                **(
                    {
                        "incomplete_flows": f"{incomplete} of these flows have calls Steno "
                        "couldn't follow (see get_flow)"
                    }
                    if incomplete
                    else {}
                ),
                "complete": not hooks and not incomplete,
            },  # fmt: skip
            "citations": _unique(citations),
            "next": ["get_flow(flow id) for how a dependent uses it"],
        }
        if direction == "upstream" and kind == "interface":
            # What other witnesses say about who uses this channel (D109): agreement for each
            # application, and the ones only a witness states (no code found)
            uses, use_citations = self._uses(start)
            if uses:
                out["agreement"] = uses
                out["agreement_means"] = _meanings(uses)
                out["citations"] = _unique(out["citations"] + use_citations)
                declared = [u for u in uses if u["agreement"] == "declared_only"]
                if declared:
                    out["completeness"]["declared_only"] = (
                        f"{len(declared)} application{'s' if len(declared) != 1 else ''} "
                        "another witness says use this, with no code found doing it (see "
                        "`agreement`): not ingested, an unused grant, or a missed rule"
                    )
                    out["completeness"]["complete"] = False
        if direction == "downstream":
            out["touches"] = sorted(
                (f"{v['how']} {v['name']}" for v in touched.values()), key=str.lower
            )
        else:
            possible = self._possible_callers(self._targets_of(start, kind))
            if possible:
                # Not facts: calls whose host Steno doesn't know, with a matching method and
                # path (D100). Listed so "nothing calls this" is never said when something may
                out["possible_callers"] = possible
                out["possible_callers_note"] = (
                    "calls from flows whose host is set outside the repository and whose method "
                    "and path match; not confirmed"
                )
                out["completeness"]["unconfirmed_callers"] = (
                    f"{len(possible)} possible caller{'s' if len(possible) != 1 else ''} can't be "
                    "confirmed or ruled out (see "
                    "`possible_callers`)"
                )
                out["completeness"]["complete"] = False
        return out

    def _possible_callers(self, targets: list[str]) -> list[dict[str, Any]]:
        endpoints = [
            dict(r)
            for r in self._read(
                "MATCH (e:HttpEndpoint) WHERE e.id IN $ids "
                "RETURN e.method AS method, e.path AS path",
                ids=targets,
            )
        ]
        if not endpoints:
            return []
        out = []
        for r in self._read(
            """
            MATCH (fl:Flow)<-[:BELONGS_TO]-(:Step)-[c:CALLS]->(x:ExternalSystem)
            WHERE x.called_from IS NOT NULL
            OPTIONAL MATCH (fl)-[:BELONGS_TO]->(a:Application)
            RETURN DISTINCT fl.id AS flow, fl.trigger AS trigger, a.name AS app,
                   c.method AS method, c.url AS url
            """
        ):
            _, path = split_url(r["url"])
            if not path or not r["method"]:
                continue
            want = _path_shape(path)
            for e in endpoints:
                have = _path_shape(e["path"])
                if r["method"].upper() == e["method"] and (
                    have == want or (len(have) > 1 and want.endswith(have))
                ):
                    out.append(
                        {
                            "flow": r["flow"],
                            "trigger": r["trigger"],
                            "application": r["app"],
                            "call": f"{r['method']} {r['url']}",
                        }
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

    def _flow_completeness(self, fl: Any, unknown_hosts: list[str] | None = None) -> dict[str, Any]:
        """What this answer can't vouch for (D71, D101): calls in the flow Steno couldn't follow,
        calls whose host is set where the app runs (D100), whether its trigger is known, and
        framework hooks in its application (filters, validators, callbacks) that may run around
        it without the graph showing it."""
        rows = self._read(
            "MATCH (:Flow {id: $id})-[:BELONGS_TO]->(a:Application) RETURN a.unknown_hooks AS h",
            id=fl["id"],
        )
        hooks = (rows[0]["h"] if rows else None) or []
        out: dict[str, Any] = {}
        if fl.get("incomplete"):
            out["calls_not_followed"] = (
                f"{fl.get('unresolved_calls')} calls Steno couldn't follow or explain; the flow "
                "may do more past them (see each step's `stops`)"
            )
        if unknown_hosts:
            out["hosts_unknown"] = (
                f"calls to {', '.join(unknown_hosts)}: the host is set outside the repository, so "
                "which application or system they reach isn't confirmed (see `host_unknown` and "
                "`possible_targets` on the steps)"
            )
        if fl.get("inferred"):
            out["trigger"] = (
                "not known: nothing in the code starts this; "
                + (f"the framework calls it ({fl.get('framework')})" if fl.get("framework")
                   else "something outside the code runs it")
            )  # fmt: skip
        out.update(_hooks_note(hooks))
        out["complete"] = not out
        return out

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


def _witness(source: dict[str, Any]) -> str:
    """`PLATFORM: platform-config (steno-pack-strimzi)`: the witness type, the repository it
    came from, and the rule or pack that read it."""
    where = " ".join(x for x in (source.get("repo"), f"({source['witness']})") if x)
    return f"{source['witness_type']}: {where}"


def _where_summary(hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every space with matches, most first, and within it every application and how many of
    its nodes and flows matched; matches outside any application (tables, stores) as `other`."""
    spaces: dict[str, dict[str, Any]] = {}
    for h in hits:
        space = spaces.setdefault(
            h.get("space") or "no space",
            {"matches": 0, "applications": defaultdict(int), "other": 0},
        )
        space["matches"] += 1
        if h.get("application"):
            space["applications"][h["application"]] += 1
        elif h.get("kind") != "application":
            space["other"] += 1
    out = []
    for name, s in sorted(spaces.items(), key=lambda kv: -kv[1]["matches"]):
        apps = dict(sorted(s["applications"].items(), key=lambda kv: -kv[1]))
        out.append(
            {
                "space": name,
                "matches": s["matches"],
                **({"applications": apps} if apps else {}),
                **({"other": s["other"]} if s["other"] else {}),
            }
        )
    return out


def _interleave(hits: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    """Each application's best hit in turn (and each space's hits outside any application), in
    the order the groups first appear: recall across applications before depth in one."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for h in hits:
        # An application and its flows are one group; other nodes group by space
        if h.get("kind") == "application":
            key = h["name"]
        else:
            key = h.get("application") or f"space:{h.get('space', '')}"
        groups.setdefault(key, []).append(h)
    out: list[dict[str, Any]] = []
    queues = list(groups.values())
    while len(out) < limit and any(queues):
        for q in queues:
            if q and len(out) < limit:
                out.append(q.pop(0))
    return out


def _matched(f: dict[str, Any], words: list[str]) -> str:
    """Why a flow matched: the function's short name, or its full name when the words are in
    its module path (`app.ingestion.files._write_file`), not its own name."""
    short = f["label"]
    named = all(w in short.lower() for w in words)
    return f"function {short if named else f['symbol']} in {f['flow']}"


def _meanings(uses: list[dict[str, Any]]) -> dict[str, str]:
    return {a: AGREEMENT[a] for a in sorted({u["agreement"] for u in uses}) if a in AGREEMENT}


def _hooks_note(hooks: list[str]) -> dict[str, Any]:
    if not hooks:
        return {}
    shown = hooks[:8]
    return {
        "framework_hooks": shown + ([f"…and {len(hooks) - 8} more"] if len(hooks) > 8 else []),
        "framework_hooks_note": (
            "code the framework runs that no rule explains (request filters, validators, "
            "callbacks): it may run around this application's flows and read or write things "
            "the graph doesn't show; read it with view_flow_code on its 'Framework callback' flow"
        ),
    }


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
    """Cut a result to about `max_tokens` without ever making it look empty: citations go
    first (down to a few), then the longest lists lose items from the end, but no list goes
    below one item; inside a kept item (a group of flows), its own list is trimmed instead.
    `truncated` says what was left out. An answer may run over budget rather than lie."""
    budget = max_tokens * CHARS_PER_TOKEN

    def size(obj: Any) -> int:
        return len(json.dumps(obj, default=str))

    if size(result) <= budget:
        return result
    cut: dict[str, int] = defaultdict(int)

    def trim(key: str, keep: int) -> bool:
        if isinstance(result.get(key), list) and len(result[key]) > keep:
            result[key] = result[key][:-1]
            cut[key] += 1
            return True
        return False

    while size(result) > budget and trim("citations", 3):
        pass
    lists = [k for k, v in result.items() if isinstance(v, list) and k not in ("next",)]
    while size(result) > budget:
        candidates = [k for k in lists if len(result[k]) > 1]
        if not candidates:
            break
        trim(max(candidates, key=lambda k: size(result[k])), 1)
    # Still over: trim the lists inside what's left (each group keeps one flow)
    for key in lists:
        for item in result[key]:
            if not isinstance(item, dict):
                continue
            for inner, value in item.items():
                while size(result) > budget and isinstance(value, list) and len(value) > 1:
                    value.pop()
                    cut[f"{key}.{inner}"] += 1
    if cut:
        result["truncated"] = {
            k: f"{n} more not shown; raise max_tokens to see them" for k, n in cut.items()
        }
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
