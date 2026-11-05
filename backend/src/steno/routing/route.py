"""Build the `routing` block of a `search` answer (D142). See `steno.routing`."""

import logging
import time
from collections import Counter
from collections.abc import Callable
from typing import Any

from neo4j import Driver
from neo4j.exceptions import Neo4jError
from sqlalchemy.orm import Session

from steno.config import get_settings
from steno.db.models import JevDecision
from steno.decisions.base import (
    Answer,
    Choice,
    DecisionBackend,
    DecisionUnavailable,
    DisabledBackend,
    Noul,
    Question,
)
from steno.decisions.jev import JevBackend
from steno.decisions.standin import StandInBackend
from steno.graph.query import forms
from steno.routing.semantic import EmbeddingsUnavailable, similar_flows

log = logging.getLogger(__name__)

# Q0: what kind of question it is, and the words that suggest each (the stand-in's signal)
INTENTS = {
    "find a flow": (),
    "what depends on X": ("depend", "depends", "uses", "use", "calls", "consumes", "who", "impact",
                          "affect", "affects", "break", "breaks"),
    "what is X": ("what", "describe", "explain", "purpose", "about", "overview"),
    "how do I call X": ("call", "invoke", "request", "payload", "send", "trigger", "start"),
    "how are A and B connected": ("between", "connected", "connect", "path", "talk", "talks"),
}  # fmt: skip
# Where the agent goes next, by intent
NEXT = {
    "find a flow": "get_flow(id) on the chosen flow",
    "what depends on X": "find_dependents on the chosen node",
    "what is X": "get_node(id) on the chosen application or node",
    "how do I call X": "get_flow(id) for its trigger and payload, then view_flow_code",
    "how are A and B connected": "get_node on each, then find_dependents between them",
}
# What an uncalibrated answer's confidence is, in the answer itself
NOTES = {
    "stand_in": "the stand-in ranks by search matches; its confidences are rankings, not "
    "probabilities: treat every choice as medium (proceed, and say it's unverified)",
    "jev": "Jev read each candidate, but its probabilities aren't yet checked on this "
    "organization's questions: treat every choice as medium (proceed, and say it's unverified)",
}
# The wording matters: "does it do what was asked?" scored the right flow first for 10 of 15
# questions and said nothing fit for 4 that had an answer; this scored 14 of 15
FLOW_QUESTION = (
    "Is this flow part of what the user's question is about? A flow is one operation in the "
    "software."
)
SHORTLIST = 25  # flows in a shortlist, at most
SHORTLIST_JUDGED = 10  # the same, when they're ranked by what Jev read
MIN_NAME = 4  # a shorter name isn't taken as named in a question ("api")
DOES_CHARS = 110  # each shortlisted flow's "does", at most
CANDIDATES = 8  # flows the stand-in ranks, at most: it can't read, so more only adds noise
JUDGED = 200  # flows Jev reads per question, at most
ALL_FLOWS = 300  # at or below this many flows in the graph, Jev simply reads them all
SEMANTIC = 100  # flows semantic search proposes
CHOSEN = 8  # flows an answer names, at most
FITS = 0.5  # the score at which a flow reads as what was asked; tuned on real questions
SURE = 0.9  # above this, one flow is named without a shortlist
CLOSE = 0.15  # confidences this close are a close call: keep both branches


def backend() -> DecisionBackend:
    name = get_settings().decision_backend
    if name == "disabled":
        return DisabledBackend()
    if name == "stand_in":
        return StandInBackend()
    if name == "jev":
        return JevBackend()
    raise ValueError(f"decision backend {name!r} isn't built: stand_in | jev | disabled")


def band(confidence: float, calibrated: bool) -> str | None:
    """high above 0.9, medium 0.5–0.9, low below 0.5 (D142); none when uncalibrated."""
    if not calibrated:
        return None
    return "high" if confidence > 0.9 else "medium" if confidence >= 0.5 else "low"


class _Log:
    """Every decision, with its input, output, and confidence, linked to the request."""

    def __init__(self, session: Session | None, request_id: str | None, decider: DecisionBackend):
        self.session, self.request_id, self.decider = session, request_id, decider
        self.unavailable: str | None = None  # why the backend was given up on, this request

    def decide(
        self, decision: str, state: dict[str, Any], questions: list[Question]
    ) -> list[Answer]:
        started = time.perf_counter()
        try:
            answers = self.decider.decide(decision, state, questions)
        except DecisionUnavailable as exc:  # a search still answers: from search's own signals
            log.warning("%s unavailable for %s: %s", self.decider.name, decision, exc)
            self.unavailable = f"{self.decider.name}: {exc}"
            self.decider = StandInBackend()
            answers = self.decider.decide(decision, state, questions)
        if self.session is not None:
            self.session.add(
                JevDecision(
                    request_id=self.request_id,
                    decision=decision,
                    input={"backend": self.decider.name, "text": state.get("text", "")[:20000]},
                    output={
                        "answers": [
                            {
                                "value": a.value,
                                "confidence": round(a.confidence, 4),
                                **({"distribution": a.distribution} if a.distribution else {}),
                            }
                            for a in answers
                        ]
                    },  # fmt: skip
                    confidence=max((a.confidence for a in answers), default=None),
                    latency_ms=round((time.perf_counter() - started) * 1000),
                )
            )
        return answers


def route(
    driver: Driver,
    database: str,
    query: str,
    words: list[str],
    hits: list[dict[str, Any]],
    dropped: list[str],
    session: Session | None = None,
    request_id: str | None = None,
    decider: DecisionBackend | None = None,
    trace: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    decider = decider or backend()
    calibrated = bool(getattr(decider, "calibrated", decider.name == "jev"))
    decisions = _Log(session, request_id, decider)
    read = _reader(driver, database)
    question = query.strip()

    # Q0: what kind of question
    intents = list(INTENTS)
    signal = {i: 1.0 + sum(w in INTENTS[i] for w in question.lower().split()) for i in intents}
    signal["find a flow"] += 0.5  # the most common kind, ahead on a tie
    [q0] = decisions.decide(
        "Q0",
        {
            "text": f"Question: {question}",
            "question": question,
            "signals": {"kind of question": signal},
        },
        [Choice("kind of question", tuple(intents))],
    )
    intent = str(q0.value or "find a flow")

    judged = decisions.decider.name == "jev"

    # Facts and proposals: nothing here decides, it only must not lose the right answer
    candidates, semantic = _propose(driver, database, read, question, words, hits, judged)
    named = {c["id"] for c in candidates if "named in the question" in c["why"]}

    # Q4: does each candidate do what was asked, each read on its own
    flow_scores: list[dict[str, Any]] = []
    if candidates:
        q4 = decisions.decide(
            "Q4",
            {
                "text": f"Question: {question}\nCandidates: "
                + ", ".join(c["name"] for c in candidates),
                "question": question,
                "signals": {
                    _asks(c): 1.0 / (1 + c["keyword_rank"])
                    for c in candidates
                    if c["keyword_rank"] is not None
                },
            },  # fmt: skip
            [Noul(FLOW_QUESTION, c.get("digest") or c["name"], _asks(c)) for c in candidates],
        )
        flow_scores = sorted(
            ({"id": c["id"], "name": c["name"], "application": c.get("application"),
              "application_id": c.get("application_id"), "space": c.get("space"),
              "confidence": 1.0 if c["id"] in named else a.confidence,
              "why": c["why"], "digest": c.get("digest")}
             for c, a in zip(candidates, q4, strict=True)),
            key=lambda f: -f["confidence"],
        )  # fmt: skip
    judged = decisions.decider.name == "jev"  # it may have just become the stand-in
    if trace is not None:
        trace.append({"step": "proposed", "semantic": semantic, "candidates": len(candidates)})
        trace += [
            {"flow": f["name"], "application": f["application"],
             "score": round(f["confidence"], 3), "why": f["why"]}
            for f in flow_scores
        ]  # fmt: skip
    chosen_flows = _keep(flow_scores, threshold=FITS, at_most=CHOSEN)
    nothing_fits = None
    if judged and flow_scores and flow_scores[0]["confidence"] < FITS:
        chosen_flows = []
        nothing_fits = {
            "why": "no flow reads as what was asked, so this may not be in what Steno has "
            "ingested, or it may be worded differently",
            "suggest": "rephrase with what the flow does, or ask the user",
            "closest": [{"name": f["name"], "application": f["application"],
                         "confidence": round(f["confidence"], 2)} for f in flow_scores[:3]],
        }  # fmt: skip

    # Applications and spaces follow from the flows
    if judged:
        app_scores = _roll_up(chosen_flows, "application", "application_id")
        space_scores = _roll_up(chosen_flows, "space", "space")
    elif decisions.decider.name == "disabled":
        app_scores, space_scores = [], []
    else:  # the stand-in can't read: where the matches are is all it knows
        app_scores = _share(hits, "application", read)
        space_scores = _share(hits, "space", read)
    chosen_apps = app_scores
    chosen_spaces = space_scores
    if not judged:
        chosen_apps = _keep(app_scores, threshold=FITS, at_most=3)
        chosen_spaces = _keep(space_scores, threshold=FITS)

    # N4: several applications whose names fit the question's words
    apps = read(
        "MATCH (a:Application) RETURN a.id AS id, a.name AS name, "
        "coalesce(a.summary_line, '') AS line ORDER BY a.name"
    )
    by_app = Counter(h.get("application") for h in hits)
    named_apps = [a for a in apps if words and any(w in a["name"].lower() for w in words)]
    disambiguation = None
    if len(named_apps) > 1:
        options = tuple(a["name"] for a in named_apps)
        [n4] = decisions.decide(
            "N4",
            {"text": f"Question: {question}\n"
                     + "\n".join(f"- {a['name']}: {a['line']}" for a in named_apps),
             "question": question,
             "signals": {"Which one is meant?": {a["name"]: float(by_app.get(a["name"], 0))
                                                 for a in named_apps}}},
            [Choice("Which one is meant?", options, _lines(named_apps))],
        )  # fmt: skip
        disambiguation = {
            "options": [{"name": a["name"], "line": a["line"],
                         "confidence": round(n4.distribution.get(a["name"], 0.0), 2)}
                        for a in named_apps],
            "why": "several applications' names fit the question",
        }  # fmt: skip

    # When the flows can't be told apart with confidence, the agent's own model picks: from
    # what Jev scored highest, or for the stand-in, which can't tell a right flow from a wrong
    # one, on plain signs: no flow found, words nothing matched, or a close spelling only
    shortlist = None
    if judged:
        if chosen_flows and chosen_flows[0]["confidence"] < SURE:
            shortlist = _judged_shortlist(flow_scores)
    else:
        flow_hits = [h for h in hits if h.get("kind") == "flow"]
        best = (
            next((h for h in flow_hits if h["id"] == chosen_flows[0]["id"]), {})
            if chosen_flows
            else {}
        )
        weak = not chosen_flows or bool(dropped) or "close spelling" in str(best.get("matched", ""))
        if weak and chosen_apps:
            shortlist = _shortlist(read, chosen_apps[0]["id"], words)

    banded = calibrated and not decisions.unavailable

    def show(items: list[dict[str, Any]], chosen: list[dict[str, Any]], keys: tuple[str, ...]):
        ids = {c["id"] for c in chosen}
        return (
            [{**{k: i[k] for k in keys if i.get(k)}, "confidence": round(i["confidence"], 2),
              **({"band": b} if (b := band(i["confidence"], banded)) else {})}
             for i in items if i["id"] in ids],
            [{k: i[k] for k in keys if i.get(k)} | {"confidence": round(i["confidence"], 2)}
             for i in items if i["id"] not in ids][:5],
        )  # fmt: skip

    spaces_out, spaces_alt = show(space_scores, chosen_spaces, ("name",))
    apps_out, apps_alt = show(app_scores, chosen_apps, ("id", "name", "space"))
    flows_out, flows_alt = show(flow_scores, chosen_flows, ("id", "name", "application"))
    return {
        "backend": decisions.decider.name,
        "confidence": "calibrated" if banded else "uncalibrated",
        **({"note": NOTES[decisions.decider.name]} if decisions.decider.name in NOTES
           and not banded else {}),
        **({"unavailable": decisions.unavailable} if decisions.unavailable else {}),
        "question_kind": {"kind": intent, "confidence": round(q0.confidence, 2),
                          "next": NEXT[intent]},
        "spaces": spaces_out,
        "applications": apps_out,
        "flows": flows_out,
        "alternatives": {k: v for k, v in {"spaces": spaces_alt, "applications": apps_alt,
                                           "flows": flows_alt}.items() if v},
        **({"nothing_fits": nothing_fits} if nothing_fits else {}),
        **({"disambiguation": disambiguation} if disambiguation else {}),
        **({"shortlist": shortlist} if shortlist else {}),
    }  # fmt: skip


def _asks(candidate: dict[str, Any]) -> str:
    return f"Does {candidate['name']} do what was asked?"


def _propose(
    driver: Driver,
    database: str,
    read: Callable[..., list[dict[str, Any]]],
    question: str,
    words: list[str],
    hits: list[dict[str, Any]],
    judged: bool,
) -> tuple[list[dict[str, Any]], str]:
    """The flows worth judging, and each one's reason. Every source only adds: code finds what's
    named exactly, keyword search names and words, semantic search meaning. For a graph small
    enough, Jev reads every flow. Returns the candidates and what semantic search did."""
    keyword = [h["id"] for h in hits if h.get("kind") == "flow"]
    rows = read(
        "MATCH (f:Flow)-[:BELONGS_TO]->(a:Application) "
        "OPTIONAL MATCH (a)-[:BELONGS_TO]->(s:Space) "
        "RETURN f.id AS id, f.name AS name, f.digest AS digest, a.name AS application, "
        "a.id AS application_id, a.aliases AS aliases, s.name AS space"
    )
    by_id = {r["id"]: r for r in rows}
    text = question.lower()
    reasons: dict[str, list[str]] = {}

    def add(flow: str, why: str) -> None:
        if flow in by_id:
            reasons.setdefault(flow, []).append(why)

    for r in rows:
        if len(r["name"]) >= MIN_NAME and r["name"].lower() in text:
            add(r["id"], "named in the question")
    for rank, flow in enumerate(keyword):
        add(flow, f"keyword search #{rank + 1}")
    semantic = "off"
    if judged:
        for r in rows:  # an application named in the question: all of its flows
            names = [r["application"] or "", *(r.get("aliases") or [])]
            if any(len(n) >= MIN_NAME and n.lower() in text for n in names):
                add(r["id"], "its application is named in the question")
        if get_settings().embeddings_enabled:
            try:
                for rank, flow in enumerate(similar_flows(driver, database, question, SEMANTIC)):
                    add(flow, f"semantic search #{rank + 1}")
                semantic = "on"
            except (EmbeddingsUnavailable, Neo4jError) as exc:
                log.warning("semantic search unavailable: %s", exc)
                semantic = f"unavailable: {exc}"
        if len(rows) <= ALL_FLOWS:
            for r in rows:
                add(r["id"], "the graph is small enough to read every flow")
    limit = JUDGED if judged else CANDIDATES
    keyword_ranks = {flow: rank for rank, flow in enumerate(keyword)}
    ordered = list(reasons)[:limit]
    return (
        [
            {**by_id[flow], "why": reasons[flow], "keyword_rank": keyword_ranks.get(flow)}
            for flow in ordered
        ],
        semantic,
    )


def _roll_up(flows: list[dict[str, Any]], name: str, key: str) -> list[dict[str, Any]]:
    """An application or space scores by its best flow. Its flows being why it's here, there's
    no separate choice to make, and a question touching five spaces returns five."""
    best: dict[str, dict[str, Any]] = {}
    for f in flows:
        if f.get(name) and (f[key] not in best or f["confidence"] > best[f[key]]["confidence"]):
            best[f[key]] = {"id": f[key], "name": f[name], "confidence": f["confidence"],
                            **({"space": f["space"]} if name == "application" else {})}  # fmt: skip
    return sorted(best.values(), key=lambda x: -x["confidence"])


def _share(
    hits: list[dict[str, Any]], kind: str, read: Callable[..., list[dict[str, Any]]]
) -> list[dict[str, Any]]:
    """For the stand-in: each application's or space's share of the keyword matches."""
    if kind == "application":
        rows = read(
            "MATCH (a:Application)-[:BELONGS_TO]->(s:Space) "
            "RETURN a.id AS id, a.name AS name, s.name AS space ORDER BY a.name"
        )
        counts = Counter(
            h.get("application") or (h["name"] if h.get("kind") == "application" else None)
            for h in hits
        )
        counts.pop(None, None)
        total = sum(counts.values()) or 1
        return sorted(
            ({"id": r["id"], "name": r["name"], "space": r["space"],
              "confidence": counts.get(r["name"], 0) / total} for r in rows),
            key=lambda x: -x["confidence"],
        )  # fmt: skip
    rows = read("MATCH (s:Space) RETURN s.id AS id, s.name AS name ORDER BY s.name")
    counts = Counter(h.get("space") for h in hits if h.get("space"))
    most = max(counts.values(), default=0)
    return sorted(
        ({"id": r["id"], "name": r["name"],
          "confidence": counts.get(r["name"], 0) / most if most else 0.0} for r in rows),
        key=lambda x: -x["confidence"],
    )  # fmt: skip


def _judged_shortlist(flows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "why": "several flows could be what was asked: pick by what each does, or ask the user",
        "flows": [
            {"id": f["id"], "name": f["name"], "application": f["application"],
             "confidence": round(f["confidence"], 2), **({"does": d} if (d := _does(f)) else {})}
            for f in flows[:SHORTLIST_JUDGED]
        ],
    }  # fmt: skip


def _does(flow: dict[str, Any]) -> str | None:
    doc = next(
        (x[3:].strip() for x in (flow.get("digest") or "").splitlines() if x.startswith("DOC ")),
        None,
    )
    return doc[: DOES_CHARS - 1] + "…" if doc and len(doc) > DOES_CHARS else doc


def _lines(apps: list[dict[str, Any]]) -> dict[str, str]:
    return {a["name"]: a["line"] for a in apps if a["line"]}


def _keep(
    scored: list[dict[str, Any]], threshold: float, at_most: int | None = None
) -> list[dict[str, Any]]:
    """The choices at or above the threshold, and any close behind the best (a close call
    keeps both branches, §3a); the best one when nothing reaches the threshold."""
    if not scored or scored[0]["confidence"] <= 0:
        return []
    top = scored[0]["confidence"]
    kept = [s for s in scored if s["confidence"] >= threshold or top - s["confidence"] <= CLOSE]
    kept = [s for s in kept if s["confidence"] > 0] or scored[:1]
    return kept[:at_most] if at_most else kept


def _reader(driver: Driver, database: str) -> Callable[..., list[dict[str, Any]]]:
    def read(cypher: str, **params: Any) -> list[dict[str, Any]]:
        with driver.session(database=database) as s:
            return s.run(cypher, **params).data()

    return read


def _digests(read: Callable[..., list[dict[str, Any]]], ids: list[str]) -> dict[str, str]:
    rows = read("MATCH (f:Flow) WHERE f.id IN $ids RETURN f.id AS id, f.digest AS d", ids=ids)
    return {r["id"]: r["d"] for r in rows if r["d"]}


def _shortlist(
    read: Callable[..., list[dict[str, Any]]], app_id: str, words: list[str]
) -> dict[str, Any]:
    """The application's flows, each with what its code says it does (the digest's DOC line),
    for the agent to pick from when routing can't. Those whose name or digest has more of the
    question's words (or their stems) first, so the cut drops the least likely."""
    rows = read(
        "MATCH (f:Flow)-[:BELONGS_TO]->(a:Application {id: $id}) "
        "RETURN a.name AS app, f.id AS id, f.name AS name, f.digest AS digest ORDER BY f.name",
        id=app_id,
    )
    terms = [forms(w) for w in words]

    def overlap(r: dict[str, Any]) -> int:
        text = f"{r['name']}\n{r['digest'] or ''}".lower()
        return sum(any(f in text for f in fs) for fs in terms)

    rows.sort(key=lambda r: -overlap(r))
    flows = []
    for r in rows[:SHORTLIST]:
        doc = next(
            (x[3:].strip() for x in (r["digest"] or "").splitlines() if x.startswith("DOC ")), None
        )
        if doc and len(doc) > DOES_CHARS:
            doc = doc[: DOES_CHARS - 1] + "…"
        flows.append({"id": r["id"], "name": r["name"], **({"does": doc} if doc else {})})
    return {
        "application": rows[0]["app"] if rows else None,
        "why": "the flows couldn't be told apart with confidence: pick from these by what each "
        "does, or ask the user",
        "flows": flows,
        **({"more": len(rows) - SHORTLIST} if len(rows) > SHORTLIST else {}),
    }
