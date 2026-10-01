"""The `Decision` interface (docs/jev.md §8): typed, calibrated decisions, backend-agnostic.

Jev is the intended backend. Until it's available, stand-in backends (heuristics,
search + rank fusion, an LLM with structured output) implement the same interface,
so callers never know which one answered.
"""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol


@dataclass(frozen=True)
class Noul:
    """Yes/no. The answer is a probability of yes."""

    question: str


@dataclass(frozen=True)
class Choice:
    """One of up to 255 options. The answer is a distribution."""

    question: str
    options: tuple[str, ...]


@dataclass(frozen=True)
class Score:
    """A position on a 2–10 level scale."""

    question: str
    levels: int = 5


Question = Noul | Choice | Score


@dataclass(frozen=True)
class Answer:
    value: bool | str | int | None
    confidence: float
    distribution: dict[str, float] = field(default_factory=dict)


class Band(StrEnum):
    """Confidence bands (docs/jev.md §5). Starting points, to be tuned on logged decisions."""

    ACT = "act"  # > 0.9
    ACT_LOW_CONFIDENCE = "act_low_confidence"  # 0.5 – 0.9
    AMBIGUOUS = "ambiguous"  # < 0.5


def band(confidence: float) -> Band:
    if confidence > 0.9:
        return Band.ACT
    if confidence >= 0.5:
        return Band.ACT_LOW_CONFIDENCE
    return Band.AMBIGUOUS


class DecisionBackend(Protocol):
    name: str

    def decide(
        self, decision: str, state: str | dict[str, Any], questions: list[Question]
    ) -> list[Answer]:
        """Answer every question about one state in one pass.

        `decision` names the decision (e.g. "I1", "Q4") for logging in `jev_decision`.
        """
        ...


class DisabledBackend:
    """Every decision can be switched off (docs/jev.md §3a). Answers are always ambiguous."""

    name = "disabled"

    def decide(
        self, decision: str, state: str | dict[str, Any], questions: list[Question]
    ) -> list[Answer]:
        return [Answer(value=None, confidence=0.0) for _ in questions]
