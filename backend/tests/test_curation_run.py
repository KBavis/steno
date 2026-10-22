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


def _git(cwd, *args):
    import subprocess

    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True,
                          check=True).stdout.strip()  # fmt: skip


@pytest.fixture
def packs_dir(tmp_path, monkeypatch):
    """An organization's rule-pack repository: a bare remote and the clone Steno loads."""
    from steno.config import get_settings

    remote, clone = tmp_path / "remote.git", tmp_path / "org-packs"
    _git(tmp_path, "init", "--quiet", "--bare", "-b", "main", str(remote))
    _git(tmp_path, "clone", "--quiet", str(remote), str(clone))
    (clone / "README.md").write_text("org packs\n")
    _git(clone, "add", ".")
    _git(clone, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "--quiet", "-m", "init")
    _git(clone, "push", "--quiet", "origin", "main")
    _git(clone, "remote", "set-head", "origin", "main")
    settings = get_settings()
    monkeypatch.setattr(settings, "rule_pack_dirs", [clone])
    monkeypatch.setattr(settings, "cache_dir", tmp_path / "cache")
    monkeypatch.setattr(settings, "rules_publish_org", "pr")
    return clone


def _ready(session, p, rule=RULE, **kw):
    proposal = RuleProposal(kind=ProposalKind.RULE, status=ProposalStatus.READY,
                            fingerprints=[p.fingerprint], owner=p.owner, title="acme handler",
                            summary="AcmeHandler methods consume channels", rule=rule,
                            tests=CASES, pack={"name": "acme-messaging", "language": "java",
                                               "source": "org"}, **kw)  # fmt: skip
    session.add(proposal)
    session.flush()
    return proposal


def test_approved_rules_collect_on_one_branch_for_one_pull_request(session, packs_dir):
    """D131: each approval is a commit by Steno on steno/rules; the default branch and the
    checkout people use are untouched until the pull request merges."""
    from steno.curation.approve import approve

    p = _pattern(session)
    out = approve(session, _ready(session, p), by="reviewer")
    assert (out["mode"], out["branch"], out["created_pack"]) == ("pr", "steno/rules", True)
    remote = packs_dir.parent / "remote.git"
    files = _git(remote, "ls-tree", "-r", "--name-only", "steno/rules")
    assert "acme-messaging/rules/acme-handler.yaml" in files
    assert "acme-messaging/tests/acme-handler/one/input/com/x/H.java" in files
    assert "acme-messaging" not in _git(remote, "ls-tree", "-r", "--name-only", "main")
    assert not (packs_dir / "acme-messaging").exists()  # the working checkout is untouched
    assert _git(remote, "log", "-1", "--format=%an", "steno/rules") == "Steno"
    assert p.state == PatternState.APPROVED

    # A second approval adds to the same branch
    q = _pattern(session, "unknown_hook:com.acme.test.Other")
    approve(
        session, _ready(session, q, rule=RULE.replace("acme-handler", "acme-handler-2")), by="r"
    )
    assert _git(remote, "rev-list", "--count", "main..steno/rules") == "2"


def test_a_destination_set_to_push_commits_to_its_default_branch(session, packs_dir, monkeypatch):
    from steno.config import get_settings
    from steno.curation.approve import approve

    monkeypatch.setattr(get_settings(), "rules_publish_org", "push")
    out = approve(session, _ready(session, _pattern(session)), by="reviewer")
    assert out["mode"] == "push"
    remote = packs_dir.parent / "remote.git"
    assert "acme-messaging/rules/acme-handler.yaml" in _git(
        remote, "ls-tree", "-r", "--name-only", "main"
    )


def test_a_rule_whose_cases_fail_publishes_nothing(session, packs_dir):
    from steno.curation.approve import ApprovalError, approve

    proposal = _ready(session, _pattern(session))
    proposal.tests = {"one": {**CASES["one"], "expected": {"entry_points": []}}}
    with pytest.raises(ApprovalError):
        approve(session, proposal, by="reviewer")
    remote = packs_dir.parent / "remote.git"
    assert _git(remote, "ls-remote", "--heads", ".", "steno/rules") == ""
    assert proposal.status == ProposalStatus.READY


def test_approving_ignore_ignores_the_pattern_and_rejecting_records_why(session):
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
