"""A rule curation run (D120, D130) with a stand-in drafter: no LLM is called."""

import json

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from steno.curation import run as curation
from steno.curation.drafters import (
    DraftResult,
    NotImplementedDrafter,
    _run_cases,
    get_drafter,
)
from steno.db.models import (
    CoveragePattern,
    CurationRun,
    PatternState,
    ProposalKind,
    ProposalStatus,
    RuleProposal,
)
from steno.db.session import _session_factory

RULE = """
id: acme-handler
description: A handler method, e.g. @AcmeHandler("orders")
match:
  rule:
    kind: method_declaration
    has: { kind: modifiers, has: { pattern: "@AcmeHandler($CHANNEL)" } }
emit:
  - node: [Interface, AcmeChannel]
    as: channel
    name: $CHANNEL
  - entry_point: { trigger: channel, function: "@function" }
"""
CODE = 'package com.x;\nclass H {\n    @AcmeHandler("orders")\n    void on() {}\n}\n'
CASES = {"one": {"files": {"com/x/H.java": CODE}, "expected": {"entry_points": [
    {"trigger": {"AcmeChannel": {"name": "orders"}}, "function": "com.x.H#on()"}]}}}  # fmt: skip


def test_the_proof_runs_a_drafted_rule_against_its_cases():
    results = _run_cases({"rule": RULE, "cases": json.dumps(CASES)})
    assert [r["passed"] for r in results] == [True]


def test_providers_are_chosen_by_name():
    assert get_drafter("claude_agent_sdk").name == "claude_agent_sdk"
    assert isinstance(get_drafter("gemini"), NotImplementedDrafter)
    with pytest.raises(ValueError):
        get_drafter("nonsense")


@pytest.fixture
def session():
    s = _session_factory()()
    try:
        s.execute(text("select 1"))
    except OperationalError:
        pytest.skip("Postgres isn't running (make up)")
    try:
        yield s
    finally:
        s.rollback()
        s.close()


class Fake:
    name = "fake"

    def __init__(self, result: DraftResult):
        self.result = result
        self.seen: list = []

    def draft(self, request, read_file):
        self.seen.append(request)
        return self.result


def _pattern(session, fp="unknown_hook:com.acme.test.AcmeHandler"):
    p = CoveragePattern(
        fingerprint=fp, kind="unmet_join", signal="unknown_hook", label="x",
        owner="com.acme.test", occurrences=3, repositories=1, applications=1,
    )  # fmt: skip
    session.add(p)
    session.flush()
    return p


@pytest.fixture
def no_git(monkeypatch):
    monkeypatch.setattr(curation, "gather", lambda session, p, read: ([], {}))
    monkeypatch.setattr(session_commit := curation.Session, "commit", session_commit.flush)


def test_a_proven_rule_becomes_a_ready_proposal(session, no_git):
    p = _pattern(session)
    result = DraftResult(kind="rule", summary="AcmeHandler methods consume channels",
                         facet="Messaging", rule=RULE, cases=CASES,
                         pack={"name": "acme-messaging"}, cost_usd=0.4, turns=6)  # fmt: skip
    fake = Fake(result)
    run = curation.curate(session, fingerprints=[p.fingerprint], drafter=fake, budget_usd=5)
    proposal = session.query(RuleProposal).filter(RuleProposal.run_id == run.id).one()
    assert (proposal.kind, proposal.status) == (ProposalKind.RULE, ProposalStatus.READY)
    assert proposal.target_pack == "acme-messaging" and proposal.rule == RULE
    assert p.state == PatternState.PROPOSED
    assert run.spent_usd == 0.4 and run.status == "succeeded"
    assert fake.seen[0].owner == "com.acme.test"


def test_a_pattern_the_drafter_cant_settle_needs_a_person(session, no_git):
    p = _pattern(session)
    fake = Fake(DraftResult(kind="needs_person", summary="can't tell from the code"))
    curation.curate(session, fingerprints=[p.fingerprint], drafter=fake)
    assert p.state == PatternState.NEEDS_PERSON


def test_a_provider_that_isnt_built_fails_the_run(session, no_git):
    p = _pattern(session)
    with pytest.raises(NotImplementedError):
        curation.curate(
            session, fingerprints=[p.fingerprint], drafter=NotImplementedDrafter("openai")
        )
    run = session.query(CurationRun).order_by(CurationRun.id.desc()).first()
    assert run.status == "failed" and "openai" in run.error


# ------------------------------------------------------------------ approval


@pytest.fixture
def packs_dir(tmp_path, monkeypatch):
    from steno.config import get_settings

    monkeypatch.setattr(get_settings(), "rule_pack_dirs", [tmp_path])
    return tmp_path


def _ready(session, p, **kw):
    proposal = RuleProposal(kind=ProposalKind.RULE, status=ProposalStatus.READY,
                            fingerprints=[p.fingerprint], owner=p.owner, title="acme handler",
                            summary="AcmeHandler methods consume channels", rule=RULE,
                            tests=CASES, pack={"name": "acme-messaging", "language": "java",
                                               "source": "org"}, **kw)  # fmt: skip
    session.add(proposal)
    session.flush()
    return proposal


def test_approving_a_rule_writes_it_with_its_tests_into_a_new_org_pack(session, packs_dir):
    from steno.curation.approve import approve

    p = _pattern(session)
    proposal = _ready(session, p)
    out = approve(session, proposal, by="reviewer")
    pack = packs_dir / "acme-messaging"
    assert out["created_pack"] and (pack / "pack.yaml").exists()
    assert (pack / "rules" / "acme-handler.yaml").read_text().startswith("id: acme-handler")
    assert (pack / "tests" / "acme-handler" / "one" / "input" / "com/x/H.java").exists()
    assert proposal.status == ProposalStatus.APPROVED and p.state == PatternState.APPROVED


def test_a_rule_whose_cases_fail_is_refused_and_leaves_nothing(session, packs_dir):
    from steno.curation.approve import ApprovalError, approve

    p = _pattern(session)
    wrong = {"one": {**CASES["one"], "expected": {"entry_points": []}}}
    proposal = _ready(session, p)
    proposal.tests = wrong
    with pytest.raises(ApprovalError):
        approve(session, proposal, by="reviewer")
    assert not (packs_dir / "acme-messaging").exists()
    assert proposal.status == ProposalStatus.READY


def test_approving_ignore_ignores_the_pattern_and_rejecting_records_why(session, packs_dir):
    from steno.curation.approve import approve, reject

    p = _pattern(session)
    ignore = RuleProposal(
        kind=ProposalKind.IGNORE, status=ProposalStatus.READY,
        fingerprints=[p.fingerprint], title="x", summary="a logging helper",
    )  # fmt: skip
    session.add(ignore)
    session.flush()
    approve(session, ignore, by="reviewer")
    assert p.state == PatternState.IGNORED
    q = _pattern(session, "unknown_hook:com.acme.test.Other")
    proposal = _ready(session, q)
    reject(session, proposal, by="reviewer", why="not how Acme works")
    assert (proposal.status, q.state) == (ProposalStatus.REJECTED, PatternState.REJECTED)
    assert proposal.comment == "not how Acme works"
