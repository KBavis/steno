"""Build the `routing` block of a `search` answer (D142). See `steno.routing`."""

import logging
import time
from collections import Counter
from collections.abc import Callable
from typing import Any

from neo4j import Driver
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
SHORTLIST = 25  # flows in a shortlist, at most
DOES_CHARS = 110  # each shortlisted flow's "does", at most
CANDIDATES = 8  # flows Q4 checks, at most
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
) -> dict[str, Any]:
    decider = decider or backend()
    calibrated = bool(getattr(decider, "calibrated", decider.name == "jev"))
    decisions = _Log(session, request_id, decider)
    read = _reader(driver, database)
    question = query.strip()
    judged = decider.name == "jev"  # read the text, so its score means something, even unchecked

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

    # Q1: which spaces, one yes/no each, from their summary lines
    spaces = read(
        "MATCH (s:Space) RETURN s.id AS id, s.name AS name, "
        "coalesce(s.summary_line, s.description, '') AS line ORDER BY s.name"
    )
    by_space = Counter(h.get("space") for h in hits if h.get("space"))
    most = max(by_space.values(), default=0)
    q1 = decisions.decide(
        "Q1",
        {
            "text": f"Question: {question}\n"
            + "\n".join(f"- {s['name']}: {s['line']}" for s in spaces),
            "question": question,
            "signals": {
                f"Is it about {s['name']}?": by_space.get(s["name"], 0) / most if most else 0.0
                for s in spaces
            },
        },  # fmt: skip
        [Noul(f"Is it about {s['name']}?", f"{s['name']}: {s['line']}") for s in spaces],
    )
    space_scores = sorted(
        (
            {"id": s["id"], "name": s["name"], "confidence": a.confidence}
            for s, a in zip(spaces, q1, strict=True)
        ),
        key=lambda s: -s["confidence"],
    )
    chosen_spaces = _keep(space_scores, threshold=0.5)

    # Q2: which applications in the chosen spaces, from their summary lines
    apps = read(
        "MATCH (a:Application)-[:BELONGS_TO]->(s:Space) WHERE s.id IN $spaces "
        "RETURN a.id AS id, a.name AS name, coalesce(a.summary_line, '') AS line, "
        "s.name AS space ORDER BY a.name",
        spaces=[s["id"] for s in chosen_spaces],
    )
    by_app = Counter(h.get("application") or (h["name"] if h.get("kind") == "application" else None)
                     for h in hits)  # fmt: skip
    by_app.pop(None, None)
    app_scores: list[dict[str, Any]] = []
    if apps:
        options = tuple(a["name"] for a in apps)
        [q2] = decisions.decide(
            "Q2",
            {
                "text": f"Question: {question}\n"
                + "\n".join(f"- {a['name']}: {a['line']}" for a in apps),
                "question": question,
                "signals": {
                    "Which application?": {a["name"]: float(by_app.get(a["name"], 0)) for a in apps}
                },
            },  # fmt: skip
            [
                Choice(
                    "Which application?", options, {a["name"]: a["line"] for a in apps if a["line"]}
                )
            ],
        )
        app_scores = sorted(
            ({"id": a["id"], "name": a["name"], "space": a["space"],
              "confidence": q2.distribution.get(a["name"], 0.0)} for a in apps),
            key=lambda a: -a["confidence"],
        )  # fmt: skip
    chosen_apps = _keep(app_scores, threshold=0.5, at_most=3)

    # N4: several applications whose names fit the question's words
    named = [a for a in apps if words and any(w in a["name"].lower() for w in words)]
    disambiguation = None
    if len(named) > 1:
        options = tuple(a["name"] for a in named)
        [n4] = decisions.decide(
            "N4",
            {"text": f"Question: {question}\n"
                     + "\n".join(f"- {a['name']}: {a['line']}" for a in named),
             "question": question,
             "signals": {"Which one is meant?": {a["name"]: float(by_app.get(a["name"], 0))
                                                 for a in named}}},
            [Choice("Which one is meant?", options, _lines(named))],
        )  # fmt: skip
        disambiguation = {
            "options": [{"name": a["name"], "line": a["line"],
                         "confidence": round(n4.distribution.get(a["name"], 0.0), 2)}
                        for a in named],
            "why": "several applications' names fit the question",
        }  # fmt: skip

    # Q4: does each candidate flow (proposed by keyword search, in the chosen apps) fit
    app_names = {a["name"] for a in chosen_apps}
    flow_hits = [h for h in hits if h.get("kind") == "flow" and h.get("application") in app_names]
    candidates = flow_hits[:CANDIDATES]
    digests = _digests(read, [c["id"] for c in candidates])
    flow_scores = []
    if candidates:
        q4 = decisions.decide(
            "Q4",
            {
                "text": f"Question: {question}\n\n"
                + "\n\n".join(digests.get(c["id"], c["name"]) for c in candidates),
                "question": question,
                "signals": {
                    f"Does {c['name']} do what was asked?": 1.0 / (1 + i)
                    for i, c in enumerate(candidates)
                },
            },  # fmt: skip
            [
                Noul(f"Does {c['name']} do what was asked?", digests.get(c["id"], c["name"]))
                for c in candidates
            ],
        )
        flow_scores = sorted(
            ({"id": c["id"], "name": c["name"], "application": c.get("application"),
              "confidence": a.confidence} for c, a in zip(candidates, q4, strict=True)),
            key=lambda f: -f["confidence"],
        )  # fmt: skip
    chosen_flows = _keep(flow_scores, threshold=0.5, at_most=3)

    # When the flows can't be told apart with confidence: the chosen application's flows,
    # each with what it does, for the agent's own model to pick from
    # The stand-in can't tell a right flow from a wrong one, so only plain signs count: no
    # flow found, words nothing matched, or a best hit matched by a close spelling only
    best = (
        next((h for h in flow_hits if h["id"] == chosen_flows[0]["id"]), {}) if chosen_flows else {}
    )
    weak = (
        not chosen_flows
        or bool(dropped)
        or "close spelling" in str(best.get("matched", ""))
        or (judged and chosen_flows[0]["confidence"] < 0.9)
    )
    shortlist = _shortlist(read, chosen_apps[0]["id"], words) if weak and chosen_apps else None

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
        **({"disambiguation": disambiguation} if disambiguation else {}),
        **({"shortlist": shortlist} if shortlist else {}),
    }  # fmt: skip


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
