"""What each summary is written from, read from the graph, bounded by construction.

An application's: its names, kinds, space, and module; its README (read from the clone or the
git host, never stored); every way in (each flow's trigger with what its code says about
itself, from the flow's digest); every way out (tables, other applications' interfaces,
external systems, topics), and who else calls or messages it; its entities. A space's: its
declared description and its applications' summaries. The organization's: its spaces'.

Each list is capped and says how many more there are, so a large application's input stays
a few thousand tokens.
"""

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from neo4j import Driver

README_CHARS = 4000
FLOWS = 150
TARGETS = 40
ENTITIES = 60
APPS = 80
README_NAMES = ("README.md", "README.rst", "README.txt", "README")

# Reads a file of a repository at the ingested commit: (repo, commit, path) → text, or None
ReadFile = Callable[[str, str | None, str], str | None]


@dataclass
class Inputs:
    node_id: str
    name: str
    prompt: str
    # What the summary is about, for the log and the report
    kind: str
    parents: list[str] = field(default_factory=list)  # the space (and organization) above it


def application(driver: Driver, database: str, app_id: str, read: ReadFile | None) -> Inputs:
    with driver.session(database=database) as s:
        app = s.run(
            "MATCH (a:Application {id: $id}) "
            "OPTIONAL MATCH (a)-[:BELONGS_TO]->(sp:Space) "
            "OPTIONAL MATCH (a)-[:BUILT_FROM]->(m:Module) "
            "RETURN a, sp.id AS space_id, sp.name AS space, collect(m.path) AS modules",
            id=app_id,
        ).single()
        if app is None:
            raise KeyError(app_id)
        a = app["a"]
        flows = s.run(
            "MATCH (f:Flow)-[:BELONGS_TO]->(:Application {id: $id}) "
            "RETURN f.name AS name, f.trigger_kind AS kind, f.digest AS digest ORDER BY f.name",
            id=app_id,
        ).data()
        out = s.run(
            "MATCH (f:Flow)-[:BELONGS_TO]->(:Application {id: $id}) "
            "MATCH (f)-[r]->(t) WHERE r.rollup AND type(r) IN $effects "
            "OPTIONAL MATCH (t)-[:BELONGS_TO]->(owner:Application) WHERE owner.id <> $id "
            "RETURN type(r) AS type, t.name AS target, owner.name AS owner, "
            "count(DISTINCT f) AS flows",
            id=app_id,
            effects=["WRITES_TO", "READS_FROM", "CALLS", "PRODUCES", "CONSUMES"],
        ).data()
        callers = s.run(
            "MATCH (i)-[:STARTS]->(:Flow)-[:BELONGS_TO]->(:Application {id: $id}) "
            "MATCH (f:Flow)-[r:CALLS|PRODUCES]->(i) WHERE r.rollup "
            "MATCH (f)-[:BELONGS_TO]->(other:Application) WHERE other.id <> $id "
            "RETURN DISTINCT other.name AS app, type(r) AS type, i.name AS via",
            id=app_id,
        ).data()
        entities = s.run(
            "MATCH (e:Entity)-[:BELONGS_TO]->(:Application {id: $id}) RETURN e.name AS name",
            id=app_id,
        ).value("name")

    lines = [f"Application: {a.get('name')}"]
    if a.get("display_name") and a.get("display_name") != a.get("name"):
        lines.append(f"Display name: {a['display_name']}")
    if a.get("alias_text"):
        lines.append(f"Also known as: {a['alias_text']}")
    if a.get("kinds"):
        lines.append(f"Kinds: {', '.join(a['kinds'])}")
    if app["space"]:
        lines.append(f"Space: {app['space']}")
    modules = [m for m in app["modules"] if m]
    lines.append(f"Repository: {a.get('repo')}" + (f", module {modules[0]}" if modules else ""))

    readme = _readme(read, a.get("repo"), a.get("commit"), modules[0] if modules else None)
    if readme:
        path, text = readme
        lines += ["", f"README ({path}):", text]

    lines += ["", f"Ways in ({len(flows)} flows):"]
    for f in flows[:FLOWS]:
        doc = _digest_line(f.get("digest"), "DOC")
        lines.append(f"- {f['name']}" + (f" — {doc}" if doc else ""))
    if len(flows) > FLOWS:
        lines.append(f"- … {len(flows) - FLOWS} more")

    by_type: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in out:
        by_type[row["type"]].append(row)
    words = {
        "WRITES_TO": "Writes",
        "READS_FROM": "Reads",
        "CALLS": "Calls",
        "PRODUCES": "Produces",
        "CONSUMES": "Consumes",
    }
    outs = []
    for edge_type, word in words.items():
        rows = sorted(by_type.get(edge_type, []), key=lambda r: (-r["flows"], str(r["target"])))
        if not rows:
            continue
        names = [
            f"{r['target']} ({r['owner']})" if r["owner"] else str(r["target"])
            for r in rows[:TARGETS]
        ]
        more = f", … {len(rows) - TARGETS} more" if len(rows) > TARGETS else ""
        outs.append(f"- {word}: {', '.join(names)}{more}")
    if outs:
        lines += ["", "Ways out (most used first):", *outs]
    if callers:
        lines += ["", "Other applications that use it:"]
        verb = {"CALLS": "calls", "PRODUCES": "messages it on"}
        lines += [f"- {c['app']} {verb.get(c['type'], c['type'])} {c['via']}" for c in callers]
    if entities:
        names = sorted({str(e).rsplit(".", 1)[-1] for e in entities})
        more = f", … {len(names) - ENTITIES} more" if len(names) > ENTITIES else ""
        lines += ["", f"Entities: {', '.join(names[:ENTITIES])}{more}"]
    return Inputs(
        app_id,
        str(a.get("name")),
        "\n".join(lines),
        "application",
        [app["space_id"]] if app["space_id"] else [],
    )


def space(driver: Driver, database: str, space_id: str) -> Inputs:
    with driver.session(database=database) as s:
        row = s.run(
            "MATCH (sp:Space {id: $id}) OPTIONAL MATCH (sp)-[:BELONGS_TO]->(o:Organization) "
            "RETURN sp, o.id AS org",
            id=space_id,
        ).single()
        if row is None:
            raise KeyError(space_id)
        apps = s.run(
            "MATCH (a:Application)-[:BELONGS_TO]->(:Space {id: $id}) "
            "RETURN a.name AS name, a.kinds AS kinds, a.summary AS summary ORDER BY a.name",
            id=space_id,
        ).data()
    sp = row["sp"]
    lines = [f"Space: {sp.get('name')}"]
    if sp.get("description"):
        lines.append(f"Declared description: {sp['description']}")
    lines += ["", f"Applications ({len(apps)}):"]
    for a in apps[:APPS]:
        kinds = f" [{', '.join(a['kinds'])}]" if a.get("kinds") else ""
        lines.append(f"- {a['name']}{kinds}: {a.get('summary') or '(no summary yet)'}")
    if len(apps) > APPS:
        lines.append(f"- … {len(apps) - APPS} more")
    return Inputs(space_id, str(sp.get("name")), "\n".join(lines), "space", [row["org"]])


def organization(driver: Driver, database: str) -> Inputs | None:
    with driver.session(database=database) as s:
        org = s.run("MATCH (o:Organization) RETURN o LIMIT 1").single()
        if org is None:
            return None
        spaces = s.run(
            "MATCH (sp:Space)-[:BELONGS_TO]->(:Organization) "
            "OPTIONAL MATCH (a:Application)-[:BELONGS_TO]->(sp) "
            "RETURN sp.name AS name, sp.summary AS summary, sp.summary_line AS line, "
            "count(a) AS apps ORDER BY sp.name"
        ).data()
    o = org["o"]
    lines = [f"Organization: {o.get('name')}"]
    if o.get("description"):
        lines.append(f"Declared description: {o['description']}")
    lines += ["", f"Spaces ({len(spaces)}):"]
    for sp in spaces:
        text = sp.get("summary") or "(no summary yet)"
        lines.append(f"- {sp['name']} ({sp['apps']} applications): {text}")
    return Inputs(o["id"], str(o.get("name")), "\n".join(lines), "organization")


def _readme(
    read: ReadFile | None, repo: str | None, commit: str | None, module: str | None
) -> tuple[str, str] | None:
    """The application's own README (its module's), else the repository's."""
    if read is None or not repo:
        return None
    folders = [module] if module and module != "." else []
    folders.append("")
    for folder in folders:
        for name in README_NAMES:
            path = f"{folder}/{name}" if folder else name
            text = read(repo, commit, path)
            if text and text.strip():
                text = text.strip()
                if len(text) > README_CHARS:
                    text = text[:README_CHARS] + "\n… (cut)"
                return path, text
    return None


def _digest_line(digest: str | None, word: str) -> str | None:
    for line in (digest or "").splitlines():
        if line.startswith(word + " "):
            return line[len(word) :].strip()
    return None
