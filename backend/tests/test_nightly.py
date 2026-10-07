"""When the nightly run is due (D59)."""

from datetime import UTC, datetime

from steno.ingestion.nightly import is_due, last_slot


def _t(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 10, day, hour, minute, tzinfo=UTC)


def test_last_slot_is_tonight_once_it_has_passed_else_last_night():
    assert last_slot(_t(6, 3), "02:00") == _t(6, 2)
    assert last_slot(_t(6, 1), "02:00") == _t(5, 2)


def test_due_once_per_night():
    assert is_due(_t(6, 2, 1), last_run=_t(5, 2, 1), at="02:00")
    assert not is_due(_t(6, 9), last_run=_t(6, 2, 1), at="02:00")  # already ran tonight
    assert not is_due(_t(6, 1), last_run=_t(5, 2, 1), at="02:00")  # tonight's isn't here yet


def test_a_new_deployment_waits_for_the_next_night():
    assert not is_due(_t(6, 9), last_run=None, at="02:00")
