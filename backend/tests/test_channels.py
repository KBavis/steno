"""D61: an org's own messaging framework is two rules, a sender and a receiver, and
applications connect wherever the channel names match."""

from pathlib import Path
from textwrap import dedent

from steno.graph.build import Context, build
from steno.graph.structure import detect
from steno.ingestion.local import run_folder
from steno.rule_packs.packs import is_enabled, load_packs

PACK = {
    "pack.yaml": """
        name: org-px
        version: 0.1.0
        language: python
        source: org
        description: The org's own messaging
        enabled_when:
          imports: [px]
    """,
    "rules/px-send.yaml": """
        id: px-send
        description: Sending on a channel, e.g. messenger.post("orders", order)
        match:
          rule:
            pattern: $M.post($CHANNEL, $$$)
        where:
          $M: { type: px.Messenger }
        emit:
          - edge: PRODUCES
            from: "@function"
            to: { PxChannel: { name: $CHANNEL } }
    """,
    "rules/px-receive.yaml": """
        id: px-receive
        description: A handler for a channel, e.g. @on_message("orders")
        match:
          rule:
            kind: decorated_definition
            has:
              kind: decorator
              has:
                pattern: on_message($CHANNEL)
        emit:
          - node: [Interface, PxChannel]
            as: channel
            name: $CHANNEL
          - entry_point:
              trigger: channel
              function: "@function"
    """,
}
SENDER = """
    from px import Messenger, on_message

    messenger = Messenger()


    @on_message("payments")
    def on_payment(payment):
        publish(payment)


    def publish(payment):
        messenger.post("orders", payment)
"""
RECEIVER = """
    from px import on_message


    @on_message("orders")
    def on_order(order):
        return order
"""


def _write(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(dedent(text).lstrip())
    return root


def _plan(tmp_path: Path, name: str, code: str):
    packs_dir = _write(tmp_path / "packs", {f"org/org-px/{k}": v for k, v in PACK.items()})
    repo = _write(tmp_path / name, {"app.py": code, "requirements.txt": "px\n"})
    packs = [p for p in load_packs(packs_dir) if is_enabled(p, repo)]
    engine = run_folder(repo, packs)
    structure = detect(repo, {ep.origin.file for ep in engine.out.entry_points})
    return build(engine.out, engine.resolver, structure, Context(name, "space:1", None, 1, "x"))


def test_sender_and_receiver_in_different_applications_meet_on_one_channel(tmp_path):
    sender = _plan(tmp_path, "billing", SENDER)
    receiver = _plan(tmp_path, "shipping", RECEIVER)
    sent = {(e.type, e.dst) for e in sender.edges.values()}
    received = {(e.type, e.src, e.dst) for e in receiver.edges.values()}

    # The sender's step that posts produces to the channel, and its flow rolls it up
    assert ("PRODUCES", "pxchannel:orders") in sent
    steps = [
        e.src for e in sender.edges.values() if e.type == "PRODUCES" and e.src.startswith("step:")
    ]
    assert len(steps) == 1 and steps[0].endswith(":app.publish")
    # The receiver's flow is started by the same channel node
    assert ("STARTS", "pxchannel:orders", "flow:shipping:orders") in received
    assert "Interface" in receiver.nodes["pxchannel:orders"].labels
