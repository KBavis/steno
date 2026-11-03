"""Graph maintenance's pure parts: comparing two snapshots, and the reset's confirmation."""

from steno.graph.maintenance import confirmed, diff


def test_a_reset_needs_the_database_name_typed_exactly():
    assert confirmed("neo4j", "neo4j")
    assert confirmed("  neo4j \n", "neo4j")
    for typed in ("", None, "y", "yes", "Neo4j", "neo"):
        assert not confirmed(typed, "neo4j")


def test_diff_lists_only_what_changed():
    before = {"nodes": 10, "edges": 4, "by_label": {"Flow": 6, "Step": 4},
              "by_type": {"CALLS": 4}, "agreement": {"confirmed": 2}}  # fmt: skip
    after = {"nodes": 6, "edges": 5, "by_label": {"Flow": 6},
             "by_type": {"CALLS": 4, "READS_FROM": 1}, "agreement": {"confirmed": 2}}  # fmt: skip
    assert diff(before, after) == [
        "nodes: 10 → 6 (-4)",
        "edges: 4 → 5 (+1)",
        "by_label.Step: 4 → 0 (-4)",
        "by_type.READS_FROM: 0 → 1 (+1)",
    ]
    assert diff(after, after) == []
