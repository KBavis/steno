"""Checks a drafted rule passes before a person sees it: recall and identity."""

from steno.curation.checks import RuleCheck, run_draft

HANDLER = """
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

COMPOSE = """
id: compose-service
description: A Docker Compose service
kind: config
files: ["**/compose.yaml"]
match:
  key: "services.{SERVICE}.image"
emit:
  - claim: deployable
    application: $SERVICE
    kind: service
"""


def test_a_draft_runs_over_real_files_and_shows_what_it_misses():
    files = {
        "svc/com/x/A.java": (
            'package com.x;\nclass A {\n    @AcmeHandler("orders")\n    void on() {}\n}\n'
        ),
        "svc/com/x/B.java": "package com.x;\nclass B {\n    void plain() {}\n}\n",
    }
    x = run_draft(HANDLER, {"language": "java"}, files, packs=[])
    matched = {e.origin.file for e in x.entry_points}
    assert matched == {"svc/com/x/A.java"}


def test_a_draft_that_names_applications_generically_is_found():
    files = {"app/compose.yaml": "services:\n  backend:\n    image: acme/orders-api\n"}
    x = run_draft(COMPOSE, {"language": "any", "witness_type": "PLATFORM"}, files, packs=[])
    assert [c.fields["application"] for c in x.claims] == ["backend"]


def test_problems_say_what_a_reviewer_must_know():
    c = RuleCheck(files=5, explained=["r/a.py"] * 3, missed=["r/b.py", "r/c.py"],
                  names=["backend"], generic=["backend"])  # fmt: skip
    problems = c.problems()
    assert any(p.startswith("identity: names too generic") for p in problems)
    assert any("explains 3 of 5 files" in p and "r/b.py" in p for p in problems)
    assert c.blocking()
    assert not RuleCheck(files=2, explained=["a", "b"]).problems()
    # Missing some files is shown, but only identity problems keep a rule from "ready"
    assert not RuleCheck(files=2, explained=["a"], missed=["b"]).blocking()
