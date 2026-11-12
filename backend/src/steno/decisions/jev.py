"""Jev (TypeSafe's System One model) behind the `Decision` interface.

Reached either directly or through an organization's gateway. Which one is only configuration:

    STENO_JEV_BASE_URL     where (default: TypeSafe, https://api.typesafe.ai/v1)
    STENO_JEV_API_KEY_REF  the key, as `env:NAME`; never stored
    STENO_JEV_AUTH_HEADER, STENO_JEV_AUTH_SCHEME  how a gateway wants it sent
    STENO_JEV_MODEL        pinned, because a score means different things across versions

Each candidate sits in its own question, so one candidate's text is never read as context for
another's: accuracy falls with state unrelated to the decision, in TypeSafe's own guidance.
"""

import concurrent.futures as cf
import json
import time
import urllib.error
import urllib.request
from typing import Any

from steno.config import get_settings
from steno.connectors.git import CloneError, resolve_secret
from steno.decisions.base import Answer, Choice, DecisionUnavailable, Noul, Question, Score

PATH = "/systemone"
MAX_OPTIONS = 255  # in a Choice, an API limit
BATCH_TOKENS = 40_000  # of the API's 64k per request, leaving room for our estimate to be off
QUESTION_TOKENS = 45  # what a question costs besides its text, roughly
RETRIES = 3
BACKOFF_S = 0.5
WORKERS = 4


class JevBackend:
    name = "jev"

    def __init__(self) -> None:
        settings = get_settings()
        # Probabilities become bands only once they're checked on this organization's questions
        self.calibrated = settings.jev_calibrated
        self.model = settings.jev_model
        self.url = settings.jev_base_url.rstrip("/") + PATH
        self.header = settings.jev_auth_header
        self.scheme = settings.jev_auth_scheme
        self.timeout = settings.jev_timeout_seconds
        self.key_ref = settings.jev_api_key_ref
        self.input_tokens = 0

    def decide(
        self, decision: str, state: str | dict[str, Any], questions: list[Question]
    ) -> list[Answer]:
        if not questions:
            return []
        body_state = _state(state)
        payloads = [_payload(q) for q in questions]
        key = self._key()
        batches = _batches(payloads, len(json.dumps(body_state)) // 4)
        with cf.ThreadPoolExecutor(min(WORKERS, len(batches))) as pool:
            answered = list(pool.map(lambda b: self._ask(body_state, b, key), batches))
        answers: dict[int, Any] = {}
        for got in answered:
            answers |= got
        return [_answer(q, answers[i]) for i, q in enumerate(questions)]

    def _key(self) -> str | None:
        if not self.key_ref:
            return None  # a gateway that authenticates another way
        try:
            return resolve_secret(self.key_ref)
        except CloneError as exc:
            raise DecisionUnavailable(str(exc)) from exc

    def _ask(
        self, state: dict[str, Any], batch: list[tuple[int, dict[str, Any]]], key: str | None
    ) -> dict[int, Any]:
        data = json.dumps(
            {"model": self.model, "state": state, "questions": {f"q{i}": q for i, q in batch}}
        ).encode()
        headers = {"Content-Type": "application/json"}
        if key:
            headers[self.header] = f"{self.scheme} {key}".strip()
        for attempt in range(RETRIES):
            request = urllib.request.Request(self.url, data, headers, method="POST")
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    got = json.load(response)
                self.input_tokens += got.get("usage", {}).get("input_tokens", 0)
                return {i: got["answers"][f"q{i}"] for i, _ in batch}
            except urllib.error.HTTPError as exc:
                if exc.code not in (429, 500, 502, 503, 504, 529) or attempt == RETRIES - 1:
                    raise DecisionUnavailable(f"{self.url} answered HTTP {exc.code}") from None
            except (urllib.error.URLError, TimeoutError) as exc:
                if attempt == RETRIES - 1:
                    reason = getattr(exc, "reason", exc)
                    raise DecisionUnavailable(f"can't reach {self.url}: {reason}") from None
            except (KeyError, ValueError, AttributeError) as exc:
                raise DecisionUnavailable(f"{self.url} answered unexpectedly: {exc!r}") from None
            time.sleep(BACKOFF_S * (2**attempt))
        raise DecisionUnavailable(f"can't reach {self.url}")  # unreachable: the last try raises


def _state(state: str | dict[str, Any]) -> dict[str, Any]:
    """The state Jev reads: the question alone, when candidates are in their own questions."""
    if isinstance(state, str):
        return {"text": state}
    if state.get("question"):
        return {"user_question": state["question"]}
    return {"text": state.get("text", "")}


def _payload(q: Question) -> dict[str, Any]:
    if isinstance(q, Noul):
        return {
            "type": "noul",
            "instructions": (
                {"question": q.question, "candidate": q.candidate} if q.candidate else q.question
            ),
        }
    if isinstance(q, Choice):
        if not 1 <= len(q.options) <= MAX_OPTIONS:
            raise DecisionUnavailable(f"{len(q.options)} options, and a Choice takes 1–255")
        return {
            "type": "choice",
            "instructions": q.question,
            "criteria": {o: q.descriptions.get(o) for o in q.options},
        }
    if isinstance(q, Score):
        if len(q.criteria) < 2:
            raise DecisionUnavailable("a Score needs its levels described")
        return {"type": "score", "instructions": q.question, "criteria": list(q.criteria)}
    raise DecisionUnavailable(f"unknown question {q!r}")


def _batches(
    payloads: list[dict[str, Any]], state_tokens: int
) -> list[list[tuple[int, dict[str, Any]]]]:
    """Questions in requests that fit, without reordering them. The state counts in each."""
    batches: list[list[tuple[int, dict[str, Any]]]] = [[]]
    used = state_tokens
    for i, payload in enumerate(payloads):
        cost = len(json.dumps(payload)) // 4 + QUESTION_TOKENS
        if batches[-1] and used + cost > BATCH_TOKENS:
            batches.append([])
            used = state_tokens
        batches[-1].append((i, payload))
        used += cost
    return batches


def _answer(q: Question, got: dict[str, Any]) -> Answer:
    """A probability throughout: Jev's own `confidence` describes how spread out an answer is,
    not how likely it is to be right."""
    if isinstance(q, Noul):
        p = float(got["noul"])
        return Answer(value=p >= 0.5, confidence=p)
    if isinstance(q, Choice):
        probabilities = {k: float(v) for k, v in got.get("probabilities", {}).items()}
        choice = got.get("choice")
        return Answer(
            value=choice, confidence=probabilities.get(choice, 0.0), distribution=probabilities
        )
    return Answer(
        value=got.get("score"),
        confidence=float(got.get("confidence", 0.0)),
        distribution={k: float(v) for k, v in got.get("probabilities", {}).items()},
    )
