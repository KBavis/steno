"""The organization's LLM is what's used (D149), through LiteLLM, here against a stand-in gateway
that speaks the OpenAI API. A provider that can't be reached never fails an ingestion."""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from steno.llm import LlmUnavailable, get_provider
from steno.llm.choice import check
from steno.llm.providers import ClaudeAgentSdk, Fake, LiteLlm


class Gateway(BaseHTTPRequestHandler):
    seen: list[dict] = []
    status = 200

    def do_POST(self):  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["content-length"])))
        Gateway.seen.append({"path": self.path, "auth": self.headers.get("authorization"), **body})
        answer = {
            "id": "x", "object": "chat.completion", "created": 1, "model": body["model"],
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": " written "}}],
            "usage": {"prompt_tokens": 7, "completion_tokens": 2, "total_tokens": 9},
        }  # fmt: skip
        data = json.dumps(
            answer if Gateway.status == 200 else {"error": {"message": "no"}}
        ).encode()
        self.send_response(Gateway.status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


@pytest.fixture
def gateway():
    Gateway.seen, Gateway.status = [], 200
    server = HTTPServer(("127.0.0.1", 0), Gateway)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}/v1"
    server.shutdown()


def test_the_gateway_is_called_with_its_model_and_the_key_it_refers_to(gateway, monkeypatch):
    monkeypatch.setenv("TEST_GATEWAY_KEY", "s3cret")
    done = get_provider("litellm", "openai/house-model", gateway, "env:TEST_GATEWAY_KEY").complete(
        "be brief", "summarize"
    )
    assert done.text == "written" and done.model == "openai/house-model"
    assert (done.input_tokens, done.output_tokens) == (7, 2)
    call = Gateway.seen[0]
    assert call["path"].endswith("/chat/completions") and call["model"] == "house-model"
    assert call["auth"] == "Bearer s3cret"
    assert [m["role"] for m in call["messages"]] == ["system", "user"]


def test_a_gateway_that_refuses_is_unavailable_and_the_key_never_shows(gateway, monkeypatch):
    monkeypatch.setenv("TEST_GATEWAY_KEY", "s3cret")
    Gateway.status = 401
    with pytest.raises(LlmUnavailable) as err:
        LiteLlm("openai/m", gateway, "env:TEST_GATEWAY_KEY").complete("s", "p")
    assert "s3cret" not in str(err.value)


def test_a_gateway_that_is_down_is_unavailable():
    with pytest.raises(LlmUnavailable) as err:
        LiteLlm("openai/m", "http://127.0.0.1:9/v1", None).complete("s", "p")
    # It says what's wrong, not something about LiteLLM's own packages
    assert "Connection" in str(err.value) and "tenacity" not in str(err.value)


def test_a_key_that_isnt_set_is_unavailable(monkeypatch):
    monkeypatch.delenv("TEST_GATEWAY_KEY", raising=False)
    with pytest.raises(LlmUnavailable, match="TEST_GATEWAY_KEY"):
        LiteLlm("openai/m", "http://127.0.0.1:9/v1", "env:TEST_GATEWAY_KEY").complete("s", "p")


def test_claude_that_cant_start_is_unavailable_not_a_failed_job(monkeypatch):
    class CLINotFoundError(Exception):
        pass

    def boom(coro):
        coro.close()
        raise CLINotFoundError("no claude here")

    monkeypatch.setattr("steno.llm.providers.asyncio.run", boom)
    with pytest.raises(LlmUnavailable, match="no claude here"):
        ClaudeAgentSdk("m").complete("s", "p")


def test_the_settings_refuse_a_raw_key_and_accept_any_named_model():
    check("litellm", "openai/house-model", "https://llm.acme.com/v1", "env:LLM_KEY")
    check("litellm", "azure/my-deployment")
    with pytest.raises(ValueError, match="reference"):
        check("litellm", "openai/m", None, "sk-live-abc123")
    with pytest.raises(ValueError, match="address"):
        check("litellm", "openai/m", "llm.acme.com")
    with pytest.raises(ValueError, match="name a model"):
        check("litellm", "  ")
    with pytest.raises(ValueError, match="doesn't take"):
        check("claude_agent_sdk", "claude-opus-5-5", "https://x.example.com")
    with pytest.raises(ValueError, match="no model"):
        check("claude_agent_sdk", "gpt-x")


def test_the_organizations_choice_is_what_runs(monkeypatch):
    from steno.llm import choice

    class Session:
        def get(self, _model, _id):
            return type("Org", (), {"settings": {"llm": {
                "provider": "litellm", "model": "openai/house", "base_url": "https://g.example.com/v1",
                "api_key_ref": "env:K"}}})()  # fmt: skip

    p = choice.provider_for(Session())
    assert isinstance(p, LiteLlm)
    assert (p.model, p.base_url, p.api_key_ref) == (
        "openai/house",
        "https://g.example.com/v1",
        "env:K",
    )
    # A one-off override to another provider doesn't get this one's address or key
    assert isinstance(choice.provider_for(Session(), "fake", "m"), Fake)


def test_a_gateway_with_no_key_still_answers(gateway):
    assert LiteLlm("openai/m", gateway).complete("s", "p").text == "written"


def test_an_llm_that_cant_be_reached_skips_summaries_and_says_why(monkeypatch):
    """The ingestion's facts don't depend on the LLM: the cards stage is skipped, with why."""
    from types import SimpleNamespace

    from steno.ingestion import pipeline

    def unavailable(*a, **k):
        raise LlmUnavailable("APIConnectionError: the gateway is down")

    monkeypatch.setattr("steno.summaries.summarize", unavailable)
    monkeypatch.setattr("steno.llm.choice.provider_for", lambda session: Fake())
    monkeypatch.setattr(pipeline, "get_driver", lambda: None)
    ctx = SimpleNamespace(
        state={"identities": {"svc": SimpleNamespace(steno_id="abc")}},
        workspace=None, session=None, job=SimpleNamespace(id=1), dry_run=False,
    )  # fmt: skip
    with pytest.raises(pipeline.Skip, match="no summaries written: .*gateway is down"):
        pipeline.cards(ctx)
