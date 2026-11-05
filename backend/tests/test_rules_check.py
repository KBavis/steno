"""Checks a drafted rule passes before a person sees it: recall and identity."""

from steno.rule_packs.check import RuleCheck, run_draft

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


def test_a_rule_with_no_surface_fact_or_no_recall_is_blocked():
    """D147: a rule that states nothing on the surface should be a harmless mark instead, and
    one that explains none of its pattern isn't the rule for it."""
    no_surface = RuleCheck(files=2, explained=["a", "b"], surface=False)
    assert no_surface.blocking()
    assert any(p.startswith("surface:") for p in no_surface.problems())
    nothing = RuleCheck(files=3, missed=["a", "b", "c"])
    assert nothing.blocking()
    assert nothing.problems() == ["recall: explains none of the 3 files where the pattern occurs"]


def test_a_clue_that_completes_a_surface_fact_counts_as_surface():
    from steno.rule_packs.packs import Rule
    from steno.rule_packs.surface import on_surface

    table = Rule("t", "p", "java", "code", {}, [{"clue": "table_of", "entity": "@class"}])
    label = Rule("l", "p", "java", "code", {}, [{"clue": "label", "name": "$X"}])
    assert on_surface(table, clues=True) and not on_surface(table)
    assert not on_surface(label, clues=True)


def test_nothing_to_check_is_refused_not_passed():
    """A pattern a rule already explains has no occurrences: "0 of 0" isn't a proof."""
    nothing = RuleCheck()
    assert nothing.blocking()
    assert nothing.problems()[0].startswith("nothing checked:")
