"""Jev behind the Decision interface: directly from TypeSafe, or through a gateway."""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from steno.config import Settings, get_settings
from steno.decisions.base import Choice, DecisionUnavailable, Noul, Score
from steno.decisions.jev import BATCH_TOKENS, JevBackend, _batches, _payload


class _Gateway:
    """A stand-in for TypeSafe or a gateway: records requests and answers them."""

    def __init__(self, status: int = 200, fail_first: int = 0):
        outer = self
        self.requests: list[dict] = []
        self.fail_first = fail_first

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.requests.append({"path": self.path, "headers": dict(self.headers), **body})
                if outer.fail_first > 0:
                    outer.fail_first -= 1
                    self.send_response(529)
                    self.end_headers()
                    return
                if status != 200:
                    self.send_response(status)
                    self.end_headers()
                    return
                answers = {}
                for qid, q in body["questions"].items():
                    if q["type"] == "noul":
                        answers[qid] = {"type": "noul", "noul": 0.9}
                    elif q["type"] == "choice":
                        first = next(iter(q["criteria"]))
                        probabilities = {o: 0.0 for o in q["criteria"]} | {first: 1.0}
                        answers[qid] = {
                            "type": "choice",
                            "choice": first,
                            "probabilities": probabilities,
                            "confidence": 0.5,
                        }
                    else:
                        answers[qid] = {"type": "score", "score": 1.0, "confidence": 0.7}
                out = json.dumps({"answers": answers, "usage": {"input_tokens": 12}}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(out)

            def log_message(self, *args):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()


@pytest.fixture
def configure(monkeypatch):
    def set_up(**values):
        monkeypatch.setenv("TEST_JEV_KEY", "secret-key")
        monkeypatch.setenv("STENO_JEV_API_KEY_REF", "env:TEST_JEV_KEY")
        monkeypatch.setenv("STENO_JEV_TIMEOUT_SECONDS", "2")
        for key, value in values.items():
            monkeypatch.setenv(f"STENO_JEV_{key.upper()}", value)
        get_settings.cache_clear()

    yield set_up
    get_settings.cache_clear()


def test_typesafe_is_the_default_with_a_bearer_key():
    fresh = Settings.model_fields
    assert fresh["jev_base_url"].default == "https://api.typesafe.ai/v1"
    assert fresh["jev_auth_header"].default == "Authorization"
    assert fresh["jev_auth_scheme"].default == "Bearer"


def test_a_gateway_changes_only_configuration(configure):
    gateway = _Gateway()
    configure(base_url=f"{gateway.url}/typesafe/v1/", auth_header="api-key", auth_scheme="")
    [answer] = JevBackend().decide("Q4", {"question": "q"}, [Noul("fits?", "a digest")])
    sent = gateway.requests[0]
    assert sent["path"] == "/typesafe/v1/systemone"
    headers = {k.lower(): v for k, v in sent["headers"].items()}
    assert headers["api-key"] == "secret-key"
    assert "authorization" not in headers
    assert answer.value is True and answer.confidence == 0.9


def test_direct_sends_a_bearer_key_the_pinned_model_and_each_candidate_alone(configure):
    server = _Gateway()
    configure(base_url=f"{server.url}/v1")
    jev = JevBackend()
    jev.decide("Q4", {"question": "q", "text": "every digest"}, [Noul("fits?", "digest a")])
    sent = server.requests[0]
    assert sent["headers"]["Authorization"] == "Bearer secret-key"
    assert sent["model"] == "jev-1.14.0"
    assert sent["state"] == {"user_question": "q"}
    assert sent["questions"]["q0"]["instructions"] == {"question": "fits?", "candidate": "digest a"}
    assert jev.input_tokens == 12


def test_a_choice_answers_with_the_probability_of_its_choice(configure):
    server = _Gateway()
    configure(base_url=server.url)
    [a] = JevBackend().decide(
        "Q2", {"question": "q"}, [Choice("which?", ("x", "y"), {"x": "does x"})]
    )
    assert server.requests[0]["questions"]["q0"]["criteria"] == {"x": "does x", "y": None}
    assert a.value == "x" and a.confidence == 1.0 and a.distribution == {"x": 1.0, "y": 0.0}


def test_a_rejected_key_is_unavailable_and_never_shown(configure):
    configure(base_url=_Gateway(status=401).url)
    with pytest.raises(DecisionUnavailable) as caught:
        JevBackend().decide("Q1", {"question": "q"}, [Noul("x?")])
    assert "401" in str(caught.value) and "secret-key" not in str(caught.value)


def test_an_unset_key_or_a_dead_address_is_unavailable(configure, monkeypatch):
    configure(base_url="http://127.0.0.1:9", api_key_ref="env:NOT_SET_ANYWHERE")
    with pytest.raises(DecisionUnavailable, match="isn't set"):
        JevBackend().decide("Q1", {"question": "q"}, [Noul("x?")])
    configure(base_url="http://127.0.0.1:9")
    monkeypatch.setattr("steno.decisions.jev.BACKOFF_S", 0)
    with pytest.raises(DecisionUnavailable, match="can't reach"):
        JevBackend().decide("Q1", {"question": "q"}, [Noul("x?")])


def test_overload_is_retried(configure, monkeypatch):
    monkeypatch.setattr("steno.decisions.jev.BACKOFF_S", 0)
    server = _Gateway(fail_first=1)
    configure(base_url=server.url)
    [a] = JevBackend().decide("Q1", {"question": "q"}, [Noul("x?")])
    assert a.confidence == 0.9 and len(server.requests) == 2


def test_questions_split_into_requests_that_fit_and_keep_their_order(configure):
    server = _Gateway()
    configure(base_url=server.url)
    long = "word " * 6000  # about 7,500 tokens
    questions = [Noul(f"q{i}?", long) for i in range(12)]
    answers = JevBackend().decide("Q4", {"question": "q"}, questions)
    assert len(answers) == 12 and len(server.requests) >= 2
    seen = sorted(int(k[1:]) for r in server.requests for k in r["questions"])
    assert seen == list(range(12))


def test_batches_stay_under_the_budget():
    payloads = [{"instructions": "x" * 40_000} for _ in range(10)]
    for batch in _batches(payloads, 10):
        assert sum(len(json.dumps(p)) // 4 + 45 for _, p in batch) + 10 <= BATCH_TOKENS


def test_limits_are_refused_before_sending():
    with pytest.raises(DecisionUnavailable, match="1–255"):
        _payload(Choice("which?", tuple(str(i) for i in range(256))))
    with pytest.raises(DecisionUnavailable, match="levels"):
        _payload(Score("how much?"))
