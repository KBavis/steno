"""The coverage report stays quiet: a guard against the noise seen on Steno's own
repository coming back. Each case is a shape that once produced many items for one thing."""


def test_a_file_one_application_writes_and_another_reads_stays_visible():
    from steno.coverage.collect import Item, _quiet

    own = Item("external_call", "file_call", "joblib", "x")
    own.add({"file": "svc/a.py", "function": "a.save", "application": "svc"})
    own.add({"file": "svc/b.py", "function": "b.load", "application": "svc"})
    handoff = Item("external_call", "file_call", "joblib", "x")
    handoff.add({"file": "training/train.py", "function": "train.save", "application": ""})
    handoff.add({"file": "inference/predict.py", "function": "p.load", "application": "inference"})
    _quiet({1: own, 2: handoff})
    assert own.quiet and not handoff.quiet


def test_an_unread_file_shows_only_when_it_may_be_something_missed():
    """a service in a language no pack reads, and a config file with evidence, show."""
    from steno.coverage.collect import Item, _quiet

    def unread(target: str, *evidence: str) -> Item:
        it = Item("config", "unread_file", target, target)
        it.evidence = set(evidence)
        return it

    items = {
        "go": unread(".go@*", "names_known_things"),
        "compose": unread(".yaml@/", "referenced_by_code"),
        "nginx": unread(".conf@nginx/", "names_known_things"),
        "plain-yaml": unread(".yml@config/"),
        "script": unread(".sh@scripts/", "names_known_things"),
    }
    _quiet(items)
    assert {k for k, i in items.items() if not i.quiet} == {"go", "compose", "nginx"}
