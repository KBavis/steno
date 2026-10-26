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


def test_a_run_never_spends_more_than_its_budget(session, no_git):
    # Each draft may cost up to the per-pattern cap ($2), so a $5 run drafts while $2 is left:
    # three $1.50 drafts, then $0.50 is left and the fourth waits for the next run
    fps = [_pattern(session, f"unknown_hook:com.acme.test.H{i}").fingerprint for i in range(4)]
    fake = Fake(DraftResult(kind="ignore", summary="in-process", cost_usd=1.5))
    run = curation.curate(session, fingerprints=fps, drafter=fake, budget_usd=5)
    assert len(fake.seen) == 3 and run.spent_usd == 4.5
    assert run.stats["total"] == 4 and run.stats["left_for_next_run"] == 1


def test_a_run_takes_every_open_pattern_unless_limited(session, no_git):
    for i in range(3):
        _pattern(session, f"unknown_hook:com.acme.test.H{i}")
    assert len(curation.pick(session, None)) >= 3
    assert len(curation.pick(session, 2)) == 2


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


def test_a_usage_limit_stops_the_run_and_leaves_patterns_open(session, no_git):
    from steno.curation.drafters import DrafterUnavailable

    class OutOfUsage(Fake):
        def draft(self, request, read_file):
            if self.seen:
                raise DrafterUnavailable("Claude's five hour limit is reached")
            return super().draft(request, read_file)

    ps = [_pattern(session, f"unknown_hook:com.acme.test.L{i}") for i in range(3)]
    fake = OutOfUsage(DraftResult(kind="ignore", summary="in-process", cost_usd=0.2))
    with pytest.raises(DrafterUnavailable):
        curation.curate(session, fingerprints=[p.fingerprint for p in ps], drafter=fake)
    run = session.query(CurationRun).order_by(CurationRun.id.desc()).first()
    assert run.status == "failed" and run.error == "Claude's five hour limit is reached"
    assert run.stats["left_for_next_run"] == 2
    assert sorted(p.state for p in ps).count(PatternState.OPEN) == 2


def test_a_search_drafts_with_the_model_it_was_started_with(session, no_git):
    run = curation.queue_run(session, model="claude-haiku-5-5")
    assert run.model == "claude-haiku-5-5"
    assert get_drafter(run.drafter, run.model).model == "claude-haiku-5-5"
    default = curation.queue_run(session)
    assert get_drafter(default.drafter, default.model).model == "claude-opus-5-5"


def test_only_allowed_models_can_be_chosen():
    from fastapi.testclient import TestClient

    from steno.api.app import create_app

    out = TestClient(create_app()).post("/api/curation/runs", json={"model": "gpt-9"})
    assert out.status_code == 422


def test_a_new_core_pack_goes_in_the_folder_the_drafter_chose(monkeypatch, tmp_path):
    from steno.curation import approve

    monkeypatch.setattr(
        approve, "get_settings", lambda: type("S", (), {"rule_packs_dir": tmp_path})()
    )
    meta = {"name": "steno-pack-compose", "language": "any", "source": "core"}
    assert approve._new_pack_dir(meta, "platform") == tmp_path / "platform" / "steno-pack-compose"
    # No folder, or one a drafted core pack can't use: its language's, or platform
    assert approve._new_pack_dir(meta) == tmp_path / "platform" / "steno-pack-compose"
    assert approve._new_pack_dir(meta, "core") == tmp_path / "platform" / "steno-pack-compose"
    assert approve._new_pack_dir({**meta, "language": "python"}, "../x") == (
        tmp_path / "python" / "steno-pack-compose"
    )


def test_publishing_finds_the_repository_for_a_folder_that_doesnt_exist_yet(tmp_path):
    from steno.curation.publish import destination

    remote, work = tmp_path / "remote.git", tmp_path / "work"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(remote))
    _git(tmp_path, "clone", "-q", str(remote), str(work))
    _git(work, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty",
         "-m", "first")  # fmt: skip
    _git(work, "push", "-q", "origin", "main")
    dest = destination("core", work / "rule-packs" / "platform" / "steno-pack-compose")
    assert dest.repo.resolve() == work.resolve() and dest.base == "main"


def test_one_function_signals_are_left_to_the_coverage_report(session, no_git):
    dead = CoveragePattern(
        fingerprint="unreachable_effect:com.acme.test.Svc#unused()", kind="unreachable",
        signal="unreachable_effect", label="x", occurrences=1, repositories=1, applications=1,
    )  # fmt: skip
    session.add(dead)
    session.flush()
    assert dead.fingerprint not in {p.pattern.fingerprint for p in curation.pick(session, None)}


def _queued_run(session, n, budget=10.0, prefix="P"):
    fps = [
        _pattern(session, f"unknown_hook:com.acme.test.{prefix}{i}").fingerprint for i in range(n)
    ]
    return curation.queue_run(session, fingerprints=fps, budget_usd=budget), fps


def test_workers_draft_a_runs_patterns_in_parallel(session, no_git, monkeypatch):
    monkeypatch.setattr(curation.get_settings(), "curation_parallel", 2)
    run, fps = _queued_run(session, 3)
    a, b = curation.claim(session, run.id), curation.claim(session, run.id)
    assert {a.fingerprint, b.fingerprint} == set(fps[:2])
    assert curation.claim(session, run.id) is None  # two at once, the cap
    assert run.stats["reserved_usd"] == 4.0 and len(run.stats["inflight"]) == 2
    fake = Fake(DraftResult(kind="ignore", summary="in-process", cost_usd=0.3))
    curation.draft_one(session, b, fake)
    c = curation.claim(session, run.id)
    assert c.fingerprint == fps[2]
    curation.draft_one(session, a, fake)
    curation.draft_one(session, c, fake)
    assert run.status == "succeeded" and run.spent_usd == 0.9
    assert run.stats["ignore:ready"] == 3 and "inflight" not in run.stats


def test_a_claim_waits_for_drafts_in_flight_before_stopping_at_the_budget(session, no_git):
    run, fps = _queued_run(session, 3, budget=3.0, prefix="B")
    a = curation.claim(session, run.id)
    # $2 reserved for the draft in flight; another $2 could pass $3, so wait, don't stop
    assert curation.claim(session, run.id) is None and "stopping" not in run.stats
    curation.draft_one(session, a, Fake(DraftResult(kind="ignore", summary="x", cost_usd=0.5)))
    assert curation.claim(session, run.id) is not None  # $0.50 spent: $2 more fits


def test_a_claim_a_worker_never_finished_is_drafted_again(session, no_git, monkeypatch):
    run, fps = _queued_run(session, 1, prefix="S")
    a = curation.claim(session, run.id)
    stats = dict(run.stats)
    stats["inflight"] = {
        a.fingerprint: {**stats["inflight"][a.fingerprint], "since": "2000-01-01T00:00:00+00:00"}
    }
    run.stats = stats
    again = curation.claim(session, run.id)
    assert again is not None and again.fingerprint == a.fingerprint


def test_a_sample_without_a_line_points_at_the_first_mention():
    text = "import os\nfrom psycopg2 import connect\n\nconn = connect(host=h)\n"
    assert curation.find_line(text, "psycopg2.connect") == 2  # `connect`, on the import
    assert curation.find_line(text, "psycopg2") == 2
    assert curation.find_line("x = 1\n", "joblib.dump") is None
    assert curation.find_line("anything", ".yml@/") is None  # a file-level pattern


def test_ignoring_unread_files_covers_only_the_names_the_drafter_examined():
    from steno.curation.approve import ignore_scope

    p = RuleProposal(
        kind=ProposalKind.IGNORE, fingerprints=["unread_file:.yml@/"],
        samples=[{"repository": "r", "file": "docker-compose.local.yml"}],
    )  # fmt: skip
    assert ignore_scope(p) == {"unread_file:.yml@/": ["docker-compose.local.yml"]}
    # A whole language, or any other pattern, is ignored whole
    p.fingerprints = ["unread_file:.js@*", "external_call:torch.save"]
    assert ignore_scope(p) == {}


def test_a_rule_the_checks_flag_goes_back_to_the_drafter_once(session):
    from steno.curation.checks import RuleCheck

    p = _pattern(session, "unknown_hook:com.acme.test.Revise")
    fake = Fake(DraftResult(kind="rule", summary="s", rule=RULE, cases=CASES, cost_usd=0.3))
    generic = RuleCheck(files=1, explained=["r/a"], names=["backend"], generic=["backend"])
    proposal = curation._draft(fake, p, [], [], lambda r, f: "", lambda rule, pack: generic)
    assert len(fake.seen) == 2  # the draft, then one revision
    assert fake.seen[1].feedback[0].startswith("identity: names too generic")
    # Still generic after the revision: a person decides, with the problem in front of them
    assert proposal.status == ProposalStatus.NEEDS_PERSON
    assert proposal.flags[0].startswith("identity:") and proposal.cost_usd == 0.6
    assert proposal.proof["checks"]["generic"] == ["backend"]
