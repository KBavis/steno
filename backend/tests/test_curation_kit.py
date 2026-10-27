"""The authoring kit (D124): deterministic tools a rule drafter uses. No LLM."""

from textwrap import dedent

from steno.curation.kit import find_rules, match_existing, vocabulary
from steno.curation.kit import test_rule as try_draft  # not a test itself


def test_the_vocabulary_comes_from_the_code():
    v = vocabulary()
    assert "HttpEndpoint" in v["node_identity"] and v["node_identity"]["HttpEndpoint"] == [
        "method",
        "path",
    ]
    assert "prefix" in v["clues"] and "may_consume" in v["claims"]
    assert "Flow" in v["labels"]["architecture"]


def test_existing_rules_are_found_by_what_they_look_for():
    found = {r.rule for r in find_rules("@KafkaListener")}
    assert "kafka-listener" in found
    assert all("TOPIC" not in m for r in find_rules("KafkaListener") for m in r.mentions)


ORDERS = dedent(
    """
    package com.x;
    import org.springframework.kafka.annotation.KafkaListener;
    class Orders {
        @KafkaListener(topics = "orders.created")
        void on(String m) {}
    }
    """
)


def test_an_existing_rule_already_matching_a_sample_is_found():
    """If a pack already explains the sample, the fix is enabling it, not a new rule."""
    matches = match_existing({"src/main/java/com/x/Orders.java": ORDERS})
    rules = {(m["pack"], m["rule"]) for m in matches}
    assert ("steno-pack-spring-kafka", "kafka-listener") in rules


def test_a_drafted_rule_runs_against_its_cases():
    rule = dedent(
        """
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
    )
    code = 'package com.x;\nclass H {\n    @AcmeHandler("orders")\n    void on() {}\n}\n'

    def case(channel: str) -> dict:
        trigger = {"AcmeChannel": {"name": channel}}
        return {
            "files": {"com/x/H.java": code},
            "expected": {"entry_points": [{"trigger": trigger, "function": "com.x.H#on()"}]},
        }

    results = {r.case: r for r in try_draft(rule, {"good": case("orders"), "wrong": case("x")})}
    assert results["good"].passed, (
        results["good"].missing,
        results["good"].unexpected,
        results["good"].errors,
    )
    assert not results["wrong"].passed


def test_a_disabled_pack_is_hidden_everywhere(monkeypatch):
    from steno.config import get_settings
    from steno.rule_packs.packs import load_all_packs

    monkeypatch.setattr(get_settings(), "disabled_packs", ["steno-pack-spring-web"])
    names = {p.name for p in load_all_packs()}
    assert "steno-pack-spring-web" not in names and "steno-pack-jpa" in names
