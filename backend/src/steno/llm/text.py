"""Generate text once per input: cached in `llm_output` by a hash of the inputs, every call
logged in `llm_call`. A dry run generates nothing and estimates the tokens instead."""

import hashlib
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from steno.db.models import LlmCall, LlmOutput
from steno.llm.providers import Provider

# A rough count without a tokenizer: English and code average about 4 characters a token
CHARS_PER_TOKEN = 4


@dataclass
class Generated:
    text: str | None  # None in a dry run
    input_hash: str
    cached: bool = False
    # What was spent (zero when cached); estimated input tokens in a dry run
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    model: str | None = None


def input_hash(kind: str, system: str, prompt: str) -> str:
    """What decides whether text must be written again: the kind, the instructions, and the
    inputs. Not the model, so changing the model doesn't rewrite everything by itself."""
    return hashlib.sha256(f"{kind}\0{system}\0{prompt}".encode()).hexdigest()


def generate(
    session: Session,
    provider: Provider | None,
    *,
    node_id: str,
    kind: str,
    system: str,
    prompt: str,
    job_id: int | None = None,
    dry_run: bool = False,
) -> Generated:
    """The text for (node, kind) from these inputs: the cached text when the inputs are
    unchanged, else written now (or, in a dry run, only counted)."""
    digest = input_hash(kind, system, prompt)
    cached = session.scalar(
        select(LlmOutput)
        .where(LlmOutput.node_id == node_id, LlmOutput.kind == kind, LlmOutput.input_hash == digest)
        .order_by(LlmOutput.id.desc())
        .limit(1)
    )
    if cached is not None:
        if not cached.is_current:
            _make_current(session, node_id, kind, cached.id)
        return Generated(cached.text, digest, cached=True)
    if dry_run or provider is None:
        estimate = (len(system) + len(prompt)) // CHARS_PER_TOKEN
        return Generated(None, digest, input_tokens=estimate)

    done = provider.complete(system, prompt)
    call = LlmCall(
        job_id=job_id,
        node_id=node_id,
        purpose=kind,
        model=done.model,
        input_tokens=done.input_tokens,
        output_tokens=done.output_tokens,
        cached_tokens=done.cached_tokens,
        cost=Decimal(str(round(done.cost_usd, 6))),
        latency_ms=done.latency_ms,
    )
    session.add(call)
    session.flush()
    output = LlmOutput(
        llm_call_id=call.id, node_id=node_id, kind=kind, input_hash=digest, text=done.text
    )
    session.add(output)
    session.flush()
    _make_current(session, node_id, kind, output.id)
    session.commit()
    return Generated(
        done.text,
        digest,
        input_tokens=done.input_tokens,
        output_tokens=done.output_tokens,
        cost_usd=done.cost_usd,
        model=done.model,
    )


def _make_current(session: Session, node_id: str, kind: str, keep: int) -> None:
    session.execute(
        update(LlmOutput)
        .where(LlmOutput.node_id == node_id, LlmOutput.kind == kind)
        .values(is_current=LlmOutput.id == keep)
    )
