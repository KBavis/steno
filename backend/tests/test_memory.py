"""Peak memory per stage (D127): measured where the platform allows, flagged near a limit."""

from steno.ingestion import memory


def test_a_peak_near_its_limit_is_flagged():
    assert memory.near_limit(1700, 2048)
    assert not memory.near_limit(900, 2048)
    assert not memory.near_limit(None, 2048)
    assert not memory.near_limit(900, None)


def test_peak_is_read_after_a_reset_or_not_at_all():
    if memory.reset_peak():
        peak = memory.peak_mb()
        assert peak is None or peak > 0
