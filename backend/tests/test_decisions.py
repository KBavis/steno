import pytest

from steno.decisions.base import Band, DisabledBackend, Noul, band


@pytest.mark.parametrize(
    ("confidence", "expected"),
    [
        (0.95, Band.ACT),
        (0.9, Band.ACT_LOW_CONFIDENCE),
        (0.5, Band.ACT_LOW_CONFIDENCE),
        (0.49, Band.AMBIGUOUS),
    ],
)
def test_confidence_bands(confidence, expected):
    assert band(confidence) is expected


def test_disabled_backend_is_always_ambiguous():
    answers = DisabledBackend().decide("I3", "state", [Noul("is this an entry point?")])
    assert band(answers[0].confidence) is Band.AMBIGUOUS
