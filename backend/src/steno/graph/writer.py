"""Send a graph plan to Neo4j in bounded transactions, writing only what changed (D59, D135).

See docs/how-it-works/ingestion/README.md §5, stages.md §7, and large-repositories.md §8a.

Read what the graph holds for this repository and compare it with the plan by stable ID
(`delta.py`). Then write the difference as many small transactions, never one big one, so
Neo4j's memory depends on the size of one chunk, not of the repository:

1. a `GraphWrite` marker says this repository is being written;
2. the nodes outside any flow, then the edges among them;
3. the flows in size-bounded groups: a group's nodes, then every edge whose later end is in it,
   so both ends exist when the edge is written;
4. only if all of that worked: what this repository no longer has, labels a node lost, and the
   shared nodes nothing references any more;
5. the marker says complete, or incomplete with what failed.

Every write merges on the stable ID, so doing one twice is harmless. A run that stopped partway
is finished by the next: its comparison with the graph finds only what is left. Nothing is
removed until everything has been written, so a failure leaves old facts in place, never a hole,
and the marker says the repository is mid-update.
"""

import logging
import re
import time
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from neo4j import Driver, Session
from neo4j.exceptions import DatabaseError, DriverError, Neo4jError, TransientError

from steno.config import get_settings
from steno.graph.build import SHARED, GraphPlan
from steno.graph.delta import OWNER, RECONCILE, Delta, EdgeKey, Existing, diff, nulls_for
from steno.graph.schema import ARCHITECTURE_LABELS, CODE_LABELS

log = logging.getLogger(__name__)
OUT_OF_MEMORY = "Neo.TransientError.General.MemoryPoolOutOfMemoryError"
RETRIES = 3  # for a transient error that isn't Neo4j running out of memory (a deadlock)
ABORT_AFTER = 5  # consecutive failed batches: the server is unwell, stop hammering it
_CONSTRAINED = set(ARCHITECTURE_LABELS) | set(CODE_LABELS)
_LABEL = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
WRITING, COMPLETE, INCOMPLETE = "writing", "complete", "incomplete"


class GraphWriteTooLarge(RuntimeError):
    """One row didn't fit in a Neo4j transaction even on its own."""


class GraphWriteIncomplete(RuntimeError):
    """Some of the graph couldn't be written. What was written stays, and the next run, which
    compares the plan with the graph again, writes the rest."""

    def __init__(self, message: str, report: "WriteReport"):
        super().__init__(message)
        self.report = report
        self.metrics = {"write": report.as_dict(), "error": message}


class _TooBig(Exception):
    """A batch ran out of Neo4j's transaction memory: halve it."""


class _Abort(Exception):
    """Too many batches in a row failed, or the database itself broke."""


@dataclass
class WriteReport:
    """What the write did, for the stage's metrics."""

    batches: int = 0
    nodes_written: int = 0
    edges_written: int = 0
    nodes_removed: int = 0
    edges_removed: int = 0
    splits: int = 0  # batches that ran out of Neo4j memory and were halved
    retries: int = 0
    max_batch_mb: float = 0.0  # the heaviest batch's properties
    edges_missing: int = 0  # edges whose end wasn't in the graph (a gap, not a failure)
    missing_unidentified: int = 0  # of those, how many couldn't be named (their lookup failed)
    missing_samples: list[dict[str, str]] = field(default_factory=list)
    failed: list[dict[str, Any]] = field(default_factory=list)
    removals_skipped: bool = False
    seconds: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        out = {k: v for k, v in vars(self).items() if v or v == 0}
        out["failed"] = self.failed[:20]
        out["missing_samples"] = self.missing_samples[:10]
        return out


@dataclass
class _Batch:
    """One transaction's worth of rows. `shape` says what query it needs: a node's labels, an
    edge's (type, from label, to label), or the label a removal is about."""

    kind: str  # nodes | edges | unlink | delete | unlabel | orphans
    label: str  # for reports: the application, "skeleton", "removals"
    ids: list[Any]
    shape: Any = None

    def half(self) -> tuple["_Batch", "_Batch"]:
        mid = len(self.ids) // 2
        return (
            _Batch(self.kind, self.label, self.ids[:mid], self.shape),
            _Batch(self.kind, self.label, self.ids[mid:], self.shape),
        )


def _primary(labels: list[str]) -> str:
    """The label with a uniqueness constraint on `id`, so MERGE uses its index."""
    return next((lbl for lbl in labels if lbl in _CONSTRAINED), labels[0])


def _safe(label: str) -> str:
    if not _LABEL.match(label):  # labels and types can't be query parameters
        raise ValueError(f"invalid label or relationship type {label!r}")
    return label


def _weight(props: dict[str, Any] | None) -> int:
    """About how many bytes a node's properties take: what a transaction has to hold."""
    total = 0
    for k, v in (props or {}).items():
        total += len(k)
        if isinstance(v, (str, bytes)):
            total += len(v)
        elif isinstance(v, list):
            total += sum(len(x) if isinstance(x, (str, bytes)) else 8 for x in v) + 8 * len(v)
        else:
            total += 8
    return total


def marker_id(repo: str) -> str:
    return f"graphwrite:{repo}"


# ------------------------------------------------------------------ planning (pure)


def plan_batches(
    plan: GraphPlan,
    delta: Delta,
    *,
    chunk_bytes: int,
    node_rows: int,
    edge_rows: int,
) -> list[_Batch]:
    """The writes, in order. A flow and its steps stay together; groups of flows are packed
    into batches up to `chunk_bytes` of properties and `node_rows` nodes; an edge is written
    with the later of its two ends, so both exist by then."""
    after = {nid: a for nid, _, a in delta.nodes if a is not None}
    labels = delta.labels
    flow_of: dict[str, str] = {}  # a step's flow
    app_of: dict[str, str] = {}  # a flow's application
    for etype, src, dst in plan.edges:
        if etype != "BELONGS_TO":
            continue
        s, d = plan.nodes.get(src), plan.nodes.get(dst)
        if s is None or d is None:
            continue
        if "Step" in s.labels and "Flow" in d.labels:
            flow_of[src] = dst
        elif "Flow" in s.labels and "Application" in d.labels:
            app_of[src] = dst

    skeleton: list[str] = []
    groups: dict[str, list[str]] = defaultdict(list)  # flow → its nodes to write
    for nid in sorted(delta.nodes_to_write()):
        node_labels = labels.get(nid, [])
        flow = nid if "Flow" in node_labels else flow_of.get(nid) if "Step" in node_labels else None
        (groups[flow] if flow else skeleton).append(nid)

    buckets: list[list[str]] = []
    now: list[str] = []
    now_bytes = now_rows = 0
    for flow in sorted(groups, key=lambda f: (app_of.get(f, ""), f)):
        size = sum(_weight(after[n]) for n in groups[flow])
        rows = len(groups[flow])
        if now and (now_bytes + size > chunk_bytes or now_rows + rows > node_rows):
            buckets.append(now)
            now, now_bytes, now_rows = [], 0, 0
        now.append(flow)
        now_bytes += size
        now_rows += rows
    if now:
        buckets.append(now)
    bucket_of = {n: i for i, flows in enumerate(buckets) for f in flows for n in groups[f]}

    edges_in: dict[int, list[EdgeKey]] = defaultdict(list)  # -1: among the skeleton
    for key in sorted(delta.edges_to_write()):
        _, src, dst = key
        edges_in[max(bucket_of.get(src, -1), bucket_of.get(dst, -1))].append(key)

    def app_name(flows: list[str]) -> str:
        names = {_app_label(plan, app_of.get(f)) for f in flows}
        return next(iter(names)) if len(names) == 1 else f"{len(names)} applications"

    out = _node_batches(skeleton, "skeleton", labels, after, chunk_bytes, node_rows)
    out += _edge_batches(edges_in[-1], "skeleton", labels, edge_rows)
    for i, flows in enumerate(buckets):
        label = app_name(flows)
        ids = [n for f in flows for n in groups[f]]
        out += _node_batches(ids, label, labels, after, chunk_bytes, node_rows)
        out += _edge_batches(edges_in[i], label, labels, edge_rows)
    return out


def plan_removals(delta: Delta, *, node_rows: int, edge_rows: int) -> list[_Batch]:
    """What this repository no longer has, labels a node lost: after everything is written."""
    labels = delta.labels
    out: list[_Batch] = []
    out += _edge_batches(delta.edges_to_remove(), "removals", labels, edge_rows, kind="unlink")
    by_label: dict[str, list[str]] = defaultdict(list)
    for nid in delta.nodes_to_remove():
        by_label[_label_of(labels, nid)].append(nid)
    step = max(node_rows // 4, 1)  # a removed flow's properties are as heavy as a written one's
    for label, node_ids in sorted(by_label.items()):
        for i in range(0, len(node_ids), step):
            out.append(_Batch("delete", "removals", node_ids[i : i + step], label))
    stale: dict[tuple[str, str], list[str]] = defaultdict(list)
    for nid, gone in delta.stale_labels.items():
        for lbl in gone:
            stale[(_primary(labels[nid]), lbl)].append(nid)
    for shape, node_ids in sorted(stale.items()):
        for i in range(0, len(node_ids), node_rows):
            out.append(_Batch("unlabel", "removals", node_ids[i : i + node_rows], shape))
    return out


def _app_label(plan: GraphPlan, app_id: str | None) -> str:
    node = plan.nodes.get(app_id) if app_id else None
    return str((node.props.get("name") if node else None) or app_id or "no application")


def _label_of(labels: dict[str, list[str]], node_id: str) -> str:
    return _primary(labels[node_id]) if node_id in labels else _existing_label(node_id)


def _node_batches(
    ids: list[str],
    label: str,
    labels: dict[str, list[str]],
    after: dict[str, dict[str, Any]],
    chunk_bytes: int,
    node_rows: int,
) -> list[_Batch]:
    by_labels: dict[tuple[str, ...], list[str]] = defaultdict(list)
    for nid in ids:
        by_labels[tuple(labels[nid])].append(nid)
    out = []
    for shape, members in sorted(by_labels.items()):
        part: list[str] = []
        size = 0
        for nid in members:
            w = _weight(after[nid])
            if part and (size + w > chunk_bytes or len(part) >= node_rows):
                out.append(_Batch("nodes", label, part, shape))
                part, size = [], 0
            part.append(nid)
            size += w
        if part:
            out.append(_Batch("nodes", label, part, shape))
    return out


def _edge_batches(
    keys: Any,
    label: str,
    labels: dict[str, list[str]],
    edge_rows: int,
    kind: str = "edges",
) -> list[_Batch]:
    by_type: dict[tuple[str, str, str], list[EdgeKey]] = defaultdict(list)
    for key in keys:
        etype, src, dst = key
        by_type[(etype, _label_of(labels, src), _label_of(labels, dst))].append(key)
    out = []
    for shape, members in sorted(by_type.items()):
        for i in range(0, len(members), edge_rows):
            out.append(_Batch(kind, label, members[i : i + edge_rows], shape))
    return out


# ------------------------------------------------------------------ writing


def write_plan(
    driver: Driver,
    database: str,
    plan: GraphPlan,
    repo: str,
    *,
    job_id: int | None = None,
    on_written: Callable[[Delta], None] | None = None,
) -> tuple[Delta, WriteReport]:
    """Write the plan's differences from the graph. `on_written` is called with the part of the
    delta each transaction wrote, once it has committed, so the change history follows the
    graph even if a later batch fails. Raises `GraphWriteIncomplete` if some of it couldn't be
    written; what was written stays."""
    with driver.session(database=database) as session:
        existing = session.execute_read(_read_existing, plan, repo)
        delta = diff(plan, existing)
        return delta, apply_delta(session, plan, delta, existing, repo, job_id, on_written)


def apply_delta(
    session: Session,
    plan: GraphPlan,
    delta: Delta,
    existing: Existing,
    repo: str,
    job_id: int | None = None,
    on_written: Callable[[Delta], None] | None = None,
) -> WriteReport:
    settings = get_settings()
    writes = plan_batches(
        plan,
        delta,
        chunk_bytes=settings.graph_write_chunk_mb * 1024 * 1024,
        node_rows=settings.graph_write_node_rows,
        edge_rows=settings.graph_write_edge_rows,
    )
    removals = plan_removals(
        delta, node_rows=settings.graph_write_node_rows, edge_rows=settings.graph_write_edge_rows
    )
    run = _Run(session, plan, delta, existing, repo, on_written)
    started = time.monotonic()
    _mark(session, repo, state=WRITING, job=job_id, started=_now(), batches=len(writes))
    try:
        for batch in writes:
            run.go(batch)
        if run.report.failed:
            run.report.removals_skipped = True
        else:
            for batch in removals:
                run.go(batch)
            run.go(_Batch("orphans", "removals", _orphan_candidates(delta, existing)))
    except _Abort:
        run.report.removals_skipped = True
        if not run.report.failed:  # an abort is always a failure, whatever recorded it
            run.report.failed.append(
                {"label": "write", "kind": "abort", "rows": 0, "error": "the write was aborted"}
            )
    except BaseException as exc:
        # Anything else: the history's database, a bug, the worker being stopped. The graph may
        # already hold part of this write, so the marker must not go on saying "writing"
        _mark_quietly(
            session,
            repo,
            state=INCOMPLETE,
            finished=_now(),
            failed=[f"write stopped: {type(exc).__name__}: {exc}"[:300]],
            edges_missing=run.report.edges_missing,
        )
        raise
    report = run.report
    report.seconds = round(time.monotonic() - started, 1)
    if report.failed:
        _mark(
            session,
            repo,
            state=INCOMPLETE,
            finished=_now(),
            failed=_failed_summary(report),
            edges_missing=report.edges_missing,
        )
        raise GraphWriteIncomplete(_incomplete_message(repo, report, len(writes)), report)
    _mark(
        session,
        repo,
        state=COMPLETE,
        finished=_now(),
        failed=[],
        edges_missing=report.edges_missing,
    )
    return report


def _failed_summary(report: WriteReport) -> list[str]:
    return [
        f"{f['label']}: {f['kind']} ({f['rows']} rows): {f['error']}"[:300]
        for f in report.failed[:10]
    ]


def _incomplete_message(repo: str, report: WriteReport, total: int) -> str:
    first = report.failed[0]
    return (
        f"the graph for {repo} was only partly written: {len(report.failed)} of {total} "
        f"batches failed (first: {first['label']}, {first['kind']}, {first['error']}). What was "
        "written stays, nothing was removed, and the next run writes the rest."
    )


class _Run:
    """The writing of one repository's batches."""

    def __init__(
        self,
        session: Session,
        plan: GraphPlan,
        delta: Delta,
        existing: Existing,
        repo: str,
        on_written: Callable[[Delta], None] | None,
    ):
        self.session, self.plan, self.delta, self.repo = session, plan, delta, repo
        self.existing, self.on_written = existing, on_written
        self.node_item = {nid: (b, a) for nid, b, a in delta.nodes}
        self.edge_item = {key: (b, a) for key, b, a in delta.edges}
        self.report = WriteReport()
        self.failed_ids: set[str] = set()  # nodes that couldn't be written
        self.missing_keys: set[EdgeKey] = set()  # edges that wrote nothing: not in the history
        self.consecutive = 0

    # -- one batch: send it, halve it if Neo4j runs out of memory, say what happened ----------

    def go(self, batch: _Batch) -> None:
        if batch.kind == "edges":  # an edge to a node that couldn't be written can't be either
            batch.ids = [
                k for k in batch.ids if k[1] not in self.failed_ids and k[2] not in self.failed_ids
            ]
        if not batch.ids and batch.kind != "orphans":
            return
        try:
            written = self._send(batch)
        except _TooBig:
            if len(batch.ids) > 1:
                self.report.splits += 1
                for part in batch.half():
                    self.go(part)
                return
            message = (
                f"one {batch.kind} row for {batch.label} doesn't fit in a Neo4j transaction: "
                "raise server.memory.heap.max_size (and dbms.memory.transaction.total.max)"
            )
            self._fail(batch, GraphWriteTooLarge(message))
            return
        except (Neo4jError, DriverError) as exc:
            self._fail(batch, exc)
            return
        self.consecutive = 0
        self.report.batches += 1
        self._count(batch, written)
        self._record(batch)

    def _fail(self, batch: _Batch, exc: Exception) -> None:
        self.consecutive += 1
        self.report.failed.append(
            {
                "label": batch.label,
                "kind": batch.kind,
                "rows": len(batch.ids),
                "error": f"{type(exc).__name__}: {exc}"[:240],
            }
        )
        log.error(
            "graph write: a %s batch of %s rows for %s failed: %s",
            batch.kind, len(batch.ids), batch.label, exc,
        )  # fmt: skip
        if batch.kind == "nodes":
            self.failed_ids.update(batch.ids)
        if self.consecutive >= ABORT_AFTER or isinstance(exc, DatabaseError):
            raise _Abort from exc

    def _count(self, batch: _Batch, written: int) -> None:
        r = self.report
        if batch.kind == "nodes":
            r.nodes_written += written
        elif batch.kind == "edges":
            r.edges_written += written
        elif batch.kind == "unlink":
            r.edges_removed += written
        elif batch.kind == "delete":
            r.nodes_removed += written

    def _record(self, batch: _Batch) -> None:
        """Tell the caller what this transaction committed."""
        if self.on_written is None or batch.kind in ("unlabel", "orphans"):
            return
        sub = Delta()
        if batch.kind in ("nodes", "delete"):
            sub.nodes = [(nid, *self.node_item[nid]) for nid in batch.ids]
            sub.labels = {nid: self.delta.labels.get(nid, []) for nid in batch.ids}
        else:
            sub.edges = [
                (key, *self.edge_item[key]) for key in batch.ids if key not in self.missing_keys
            ]
        self.on_written(sub)

    # -- the queries ---------------------------------------------------------------------------

    def _send(self, batch: _Batch) -> int:
        query, rows = self._query(batch)
        self.report.max_batch_mb = max(self.report.max_batch_mb, _mb(batch, rows))
        written = self._attempt(query, rows)
        if batch.kind == "edges" and written < len(rows):
            self._missing(batch, rows, len(rows) - written)
        return written

    def _attempt(self, query: str, rows: list[Any]) -> int:
        """One transaction. Neo4j running out of memory is for the caller to halve; other
        transient errors (a deadlock) are tried again. Run as its own auto-commit statement, not
        through the driver's managed transaction, which would retry running out of memory too,
        for 30 seconds, and only repeat the work."""
        for attempt in range(1, RETRIES + 1):
            try:
                record = self.session.run(query, rows=rows, reconcile=RECONCILE).single()
                return int(record["c"]) if record else 0
            except TransientError as exc:
                if exc.code == OUT_OF_MEMORY:
                    raise _TooBig from exc
                if attempt == RETRIES:
                    raise
                self.report.retries += 1
                time.sleep(0.5 * attempt)
        return 0

    def _query(self, batch: _Batch) -> tuple[str, list[Any]]:
        if batch.kind == "nodes":
            labels = list(batch.shape)
            main = _safe(_primary(labels))
            extra = "".join(f":{_safe(lbl)}" for lbl in labels if lbl != main)
            return (
                f"UNWIND $rows AS row MERGE (n:{main} {{id: row.id}}) "
                "ON CREATE SET n.first_seen = row.props.last_seen "
                "SET n += row.props "
                # A shared node's owner is the first repository to write it; reconcile's stubs
                # are taken over by the first one whose code names them. Decided here, in the
                # database, because another job may have written it since this one looked
                "SET n += CASE WHEN n.repo IS NULL OR n.repo IN [row.owner.repo, $reconcile] "
                "THEN row.owner ELSE {} END "
                f"{('SET n' + extra + ' ') if extra else ''}RETURN count(n) AS c",
                [self._node_row(nid) for nid in batch.ids],
            )
        if batch.kind == "edges":
            etype, src, dst = batch.shape
            return (
                f"UNWIND $rows AS row MATCH (a:{_safe(src)} {{id: row.src}}) "
                f"MATCH (b:{_safe(dst)} {{id: row.dst}}) "
                f"MERGE (a)-[r:{_safe(etype)}]->(b) SET r += row.props RETURN count(r) AS c",
                [self._edge_row(key) for key in batch.ids],
            )
        if batch.kind == "unlink":
            etype, src, dst = batch.shape
            return (
                f"UNWIND $rows AS row MATCH (a:{_safe(src)} {{id: row.src}})"
                f"-[r:{_safe(etype)}]->(b:{_safe(dst)} {{id: row.dst}}) DELETE r "
                "RETURN count(r) AS c",
                [{"src": key[1], "dst": key[2]} for key in batch.ids],
            )
        if batch.kind == "delete":
            return (
                f"UNWIND $rows AS id MATCH (n:{_safe(batch.shape)} {{id: id}}) DETACH DELETE n "
                "RETURN count(*) AS c",
                list(batch.ids),
            )
        if batch.kind == "unlabel":
            main, lbl = batch.shape
            return (
                f"UNWIND $rows AS id MATCH (n:{_safe(main)} {{id: id}}) REMOVE n:{_safe(lbl)} "
                "RETURN count(n) AS c",
                list(batch.ids),
            )
        shared = " OR ".join(f"n:{lbl}" for lbl in sorted(SHARED))
        return (  # shared nodes this write stopped referencing, if nothing else does either
            f"UNWIND $rows AS id MATCH (n) WHERE n.id = id AND ({shared} OR n.shared) "
            "AND NOT (n)--() DELETE n RETURN count(n) AS c",
            list(batch.ids),
        )

    def _node_row(self, nid: str) -> dict[str, Any]:
        before, after = self.node_item[nid]
        props = _clean(after or {})
        nulls = nulls_for(before, props)
        labels = self.delta.labels.get(nid, [])
        shared = bool(SHARED & set(labels)) or bool(props.get("shared"))
        owner = {k: props[k] for k in OWNER if k in props} if shared else {}
        rest = {k: v for k, v in props.items() if k not in owner}
        return {"id": nid, "props": {**rest, **nulls}, "owner": owner}

    def _edge_row(self, key: EdgeKey) -> dict[str, Any]:
        before, _ = self.edge_item[key]
        props = _clean(self.plan.edges[key].props)
        return {"src": key[1], "dst": key[2], "props": {**props, **nulls_for(before, props)}}

    def _missing(self, batch: _Batch, rows: list[Any], count: int) -> None:
        """Edges that wrote nothing because an end isn't in the graph: a gap to report, not a
        failure (the next run writes them once the end exists). Named, so the history doesn't
        record them as added. The batch did commit, so a lookup that fails here doesn't fail it:
        the history then keeps all of them, and the count says how many couldn't be named."""
        etype, src, dst = batch.shape
        self.report.edges_missing += count
        log.warning(
            "graph write: %s %s edges weren't written: an end isn't in the graph", count, etype
        )
        try:
            found = self.session.run(
                f"UNWIND $rows AS row OPTIONAL MATCH (a:{_safe(src)} {{id: row.src}}) "
                f"OPTIONAL MATCH (b:{_safe(dst)} {{id: row.dst}}) WITH row, a, b "
                "WHERE a IS NULL OR b IS NULL RETURN row.src AS src, row.dst AS dst",
                rows=rows,
            ).data()
        except (Neo4jError, DriverError):
            log.warning("graph write: couldn't name the %s missing %s edges", count, etype)
            self.report.missing_unidentified += count
            return
        self.missing_keys.update((etype, f["src"], f["dst"]) for f in found)
        room = 10 - len(self.report.missing_samples)
        self.report.missing_samples += [{"type": etype, **f} for f in found[: max(room, 0)]]


def _mb(batch: _Batch, rows: list[Any]) -> float:
    if batch.kind != "nodes":
        return 0.0
    return round(sum(_weight(r["props"]) for r in rows) / 1024 / 1024, 1)


def _orphan_candidates(delta: Delta, existing: Existing) -> list[str]:
    """The shared nodes this write may have left unreferenced: ones this repository wrote that
    its plan no longer has, and the shared ends of edges it removed or that went with a removed
    node. Not every unreferenced node in the graph: another job may have just created one and
    not yet written its edges."""

    def shared(nid: str) -> bool:
        labels = delta.labels.get(nid) or (existing.nodes.get(nid) or ([], {}))[0]
        props = (existing.nodes.get(nid) or ([], {}))[1]
        return bool(SHARED & set(labels)) or bool(props.get("shared"))

    out = {nid for nid in existing.repo_nodes if nid not in delta.labels and shared(nid)}
    removed = set(delta.nodes_to_remove())
    unlinked = set(delta.edges_to_remove())
    for key in existing.edges:
        _, src, dst = key
        if key in unlinked or src in removed or dst in removed:
            out.update(nid for nid in (src, dst) if shared(nid))
    return sorted(out)


def _mark(session: Session, repo: str, **props: Any) -> None:
    """The repository's write state, on a node of its own that the comparison can't see: the
    graph says when a repository is mid-update so no answer treats it as settled."""
    session.run(
        "MERGE (w:GraphWrite {id: $id}) SET w += $props, w.repo_name = $repo",
        id=marker_id(repo),
        repo=repo,
        props={k: v for k, v in props.items() if v is not None},
    ).consume()


def _mark_quietly(session: Session, repo: str, **props: Any) -> None:
    """`_mark`, while another error is already on its way out: never hide that one."""
    try:
        _mark(session, repo, **props)
    except Exception:
        log.exception("graph write: couldn't mark %s's write %s", repo, props.get("state"))


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _read_existing(tx: Any, plan: GraphPlan, repo: str) -> Existing:
    """This repository's nodes and edges, plus plan nodes another repository wrote (shared
    tables, stores, channels), so they aren't mistaken for new ones."""
    existing = Existing()
    for row in tx.run(
        "MATCH (n) WHERE n.repo = $repo RETURN n.id AS id, labels(n) AS labels, "
        "properties(n) AS props",
        repo=repo,
    ):
        existing.nodes[row["id"]] = (row["labels"], row["props"])
        existing.repo_nodes.add(row["id"])
    by_label: dict[str, list[str]] = defaultdict(list)
    for nid, node in plan.nodes.items():
        if nid not in existing.nodes:
            by_label[_primary(node.labels)].append(nid)
    for label, node_ids in by_label.items():
        for row in tx.run(
            f"MATCH (n:{_safe(label)}) WHERE n.id IN $ids "
            "RETURN n.id AS id, labels(n) AS labels, properties(n) AS props",
            ids=node_ids,
        ):
            existing.nodes[row["id"]] = (row["labels"], row["props"])
    for row in tx.run(
        "MATCH (a)-[r]->(b) WHERE r.repo = $repo "
        "RETURN type(r) AS type, a.id AS src, b.id AS dst, properties(r) AS props",
        repo=repo,
    ):
        existing.edges[(row["type"], row["src"], row["dst"])] = row["props"]
    return existing


def _existing_label(node_id: str) -> str:
    """Nodes this plan points at but doesn't write (the declared org and spaces, another
    repository's nodes), labeled from their stable-ID prefix. Channels are the only IDs
    without a fixed prefix (`kafkatopic:orders`, `pxchannel:billing`), and they're Interfaces."""
    prefix = node_id.split(":", 1)[0]
    return ID_PREFIXES.get(prefix, "Interface")


ID_PREFIXES = {
    "org": "Organization",
    "space": "Space",
    "repo": "Repository",
    "module": "Module",
    "file": "File",
    "app": "Application",
    "flow": "Flow",
    "step": "Step",
    "endpoint": "Interface",
    "entity": "Entity",
    "table": "Table",
    "datastore": "DataStore",
    "external": "ExternalSystem",
}


def _clean(props: dict[str, Any]) -> dict[str, Any]:
    """Neo4j properties are scalars, lists of scalars, or bytes (a flow's compressed trace)."""
    out = {}
    for k, v in props.items():
        if isinstance(v, (str, int, float, bool, bytes)) or (
            isinstance(v, list) and all(isinstance(x, (str, int, float, bool)) for x in v)
        ):
            out[k] = v
    return out
