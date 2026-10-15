"""Record rules (claims-and-reconcile.md §4): structured files read as trees that keep their
line numbers; `each` walks to every record; `when` filters; claims fan out over list fields and
cite the line of the value they came from."""

from pathlib import Path
from textwrap import dedent

from steno.ingestion.local import run_folder
from steno.rule_packs.packs import Pack, Rule
from steno.source import config_files, records

TOPICS = """
    topics:
      payment.settled:
        partitions: 6
        consumers:
          - recon-svc
          - ledger-svc
      refund.issued:
        consumers: [recon-svc]
"""

XML = """
    <udm.DeploymentPackage version="1.0" application="ledger">
      <deployables>
        <jee.War name="ledger-service" file="ledger.war"/>
        <jee.War name="ledger-admin" file="admin.war"/>
      </deployables>
    </udm.DeploymentPackage>
"""


def _write(tmp: Path, name: str, text: str) -> Path:
    path = tmp / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dedent(text).lstrip())
    return path


def test_yaml_records_keep_their_lines_and_keys(tmp_path: Path) -> None:
    (doc,) = records.load(_write(tmp_path, "topics.yaml", TOPICS))
    topics = records.walk(doc, "topics[*]")
    assert [(t.key, t.line) for t in topics] == [("payment.settled", 3), ("refund.issued", 8)]
    consumers = records.walk(topics[0], "consumers[*]")
    assert [(records.plain(c), c.line) for c in consumers] == [("recon-svc", 5), ("ledger-svc", 6)]
    assert records.plain(records.walk(topics[0], "@key")[0]) == "payment.settled"


def test_xml_reads_like_yaml(tmp_path: Path) -> None:
    path = _write(tmp_path, "deployit-manifest.xml", XML)
    (doc,) = records.load(path)
    # `jee.War` has a dot: walk every child element, and read its tag
    wars = records.walk(doc, "deployables.*[*]")
    assert {records.plain(w)["@tag"] for w in wars} == {"jee.War"}
    assert [(records.plain(w)["name"], w.line) for w in wars] == [
        ("ledger-service", 3),
        ("ledger-admin", 4),
    ]
    flat = config_files.read(path)
    assert flat["application"] == "ledger"
    assert flat["deployables.jee.War[0].name"] == "ledger-service"


def test_flat_keys_have_lines(tmp_path: Path) -> None:
    path = _write(tmp_path, "application.yml", "spring:\n  application:\n    name: users\n")
    assert config_files.lines(path)["spring.application.name"] == 3


def _pack(*rules: Rule) -> Pack:
    return Pack(
        "test-xld", "0", "any", "org", "", {"always": True}, Path("."), list(rules),
        witness_type="PLATFORM",
    )  # fmt: skip


def _rule(rid: str, match: dict, emit: list) -> Rule:
    return Rule(id=rid, pack="test-xld", language="any", kind="config", match=match, emit=emit)


def test_each_rule_fans_out_and_cites_each_value(tmp_path: Path) -> None:
    _write(tmp_path, "topics/payments.yaml", TOPICS)
    rule = _rule(
        "topic-acls",
        {"each": "topics[*]"},
        [
            {"claim": "channel", "channel": {"KafkaTopic": "$.@key"}},
            {
                "claim": "may_consume",
                "application": "$.consumers[*]",
                "channel": {"KafkaTopic": "$.@key"},
            },
        ],
    )
    claims = run_folder(tmp_path, [_pack(rule)]).out.claims
    got = {(c.claim_type, c.fields.get("application"), c.origin.line) for c in claims}
    assert got == {
        ("channel", None, 3),
        ("channel", None, 8),
        ("may_consume", "recon-svc", 5),
        ("may_consume", "ledger-svc", 6),
        ("may_consume", "recon-svc", 8),
    }
    assert all(c.witness_type == "PLATFORM" and c.witness == "test-xld" for c in claims)


def test_when_filters_records(tmp_path: Path) -> None:
    _write(tmp_path, "topics/payments.yaml", TOPICS)
    rule = _rule(
        "big-topics",
        {"each": "topics[*]", "when": {"$.partitions": {"exists": True}}},
        [{"claim": "channel", "channel": {"KafkaTopic": "$.@key"}}],
    )
    claims = run_folder(tmp_path, [_pack(rule)]).out.claims
    assert [c.fields["channel"] for c in claims] == [{"KafkaTopic": "payment.settled"}]


def test_an_invalid_claim_is_dropped_with_a_reason(tmp_path: Path) -> None:
    _write(tmp_path, "topics/payments.yaml", TOPICS)
    rule = _rule(
        "bad",
        {"each": "topics[*]"},
        [{"claim": "may_consume", "channel": {"KafkaTopic": "$.@key"}}],
    )
    out = run_folder(tmp_path, [_pack(rule)]).out
    assert out.claims == []
    assert any("missing application" in reason for _, reason in out.dropped)
