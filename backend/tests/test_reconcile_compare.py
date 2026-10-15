"""Reconcile's comparison (claims-and-reconcile.md §2, §7): the worked example. Code and a
platform witness agree, disagree each way, and a channel nobody else mentions isn't checked."""

from steno.reconcile.compare import CODE_ONLY, CONFIRMED, DECLARED_ONLY, Source, Use, compare

CODE = Source("CODE", "spring-kafka-send", "PaymentPublisher.java", 41)
XLD = Source("PLATFORM", "acme-xld", "topics/payment.yaml", 6)


def test_the_worked_example():
    code = [
        Use("app:payments", "produces", [CODE]),
        Use("app:recon", "consumes", [Source("CODE", "kafka-listener", "ReconListener.java", 12)]),
    ]
    claimed = [
        Use("app:payments", "produces", [XLD]),
        Use("app:recon", "consumes", [Source("PLATFORM", "acme-xld", "topics/payment.yaml", 9)]),
        Use("app:ledger", "consumes", [Source("PLATFORM", "acme-xld", "topics/payment.yaml", 10)]),
    ]
    results = {
        (r.app, r.role): r for r in compare("kafkatopic:payment.settled", code, claimed, True)
    }
    assert results[("app:payments", "produces")].agreement == CONFIRMED
    assert results[("app:payments", "produces")].sources == [CODE, XLD]  # one per witness
    assert results[("app:recon", "consumes")].agreement == CONFIRMED
    assert results[("app:ledger", "consumes")].agreement == DECLARED_ONLY


def test_a_use_nothing_grants_is_code_only():
    code = [Use("app:recon", "consumes", [CODE])]
    claimed = [Use("app:other", "consumes", [XLD])]  # the channel is checked: XLD lists it
    results = {r.app: r.agreement for r in compare("kafkatopic:refund.issued", code, claimed, True)}
    assert results == {"app:recon": CODE_ONLY, "app:other": DECLARED_ONLY}


def test_producing_and_consuming_are_compared_separately():
    code = [Use("app:a", "produces", [CODE])]
    claimed = [Use("app:a", "consumes", [XLD])]  # granted consume, uses produce
    results = {(r.app, r.role): r.agreement for r in compare("kafkatopic:t", code, claimed, True)}
    assert results == {("app:a", "produces"): CODE_ONLY, ("app:a", "consumes"): DECLARED_ONLY}


def test_a_channel_no_other_witness_mentions_is_not_checked():
    assert compare("kafkatopic:internal", [Use("app:a", "produces", [CODE])], [], False) == []
