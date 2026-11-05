"""The stand-in for Jev at query time (D142): it answers from search signals, not from reading.

Jev reads the state's text (the question, summary lines, digests) and returns calibrated
probabilities. Until there are credits, this backend answers the same questions from what
keyword search already measured, passed in `state["signals"]` (one score in 0–1 per question,
keyed by the question's text): the share of matches in a space or application, a flow's rank.
Its confidences are rankings, not probabilities, so every answer is marked `uncalibrated` and
gets no band; callers must not act on them as if they were Jev's.
"""

from typing import Any

from steno.decisions.base import Answer, Choice, Noul, Question, Score


class StandInBackend:
    name = "stand_in"
    calibrated = False

    def decide(
        self, decision: str, state: str | dict[str, Any], questions: list[Question]
    ) -> list[Answer]:
        signals: dict[str, Any] = state.get("signals", {}) if isinstance(state, dict) else {}
        out = []
        for q in questions:
            if isinstance(q, Noul):
                p = float(signals.get(q.question, 0.0))
                out.append(Answer(value=p >= 0.5, confidence=p))
            elif isinstance(q, Choice):
                scores = signals.get(q.question) or {}
                total = sum(scores.get(o, 0.0) for o in q.options) or 1.0
                dist = {o: scores.get(o, 0.0) / total for o in q.options}
                best = max(dist, key=lambda o: dist[o]) if dist else None
                conf = dist.get(best, 0.0) if best else 0.0
                out.append(Answer(value=best if conf else None, confidence=conf, distribution=dist))
            elif isinstance(q, Score):
                out.append(Answer(value=None, confidence=0.0))
        return out
