"""Compare the code with other witnesses, for one subject (claims-and-reconcile.md §7).

Pure: what the code shows and what claims say go in, agreement states come out. The I/O
(reading the graph and the claim store, writing results) is in `reconcile.run`.

Evidence is compared at the grain the claim speaks at. An ACL speaks per application, so code
facts are lifted to the application: "some flow of `recon-svc` is started by `payment.settled`".
"""

from dataclasses import dataclass, field
from typing import Any

CONFIRMED, CODE_ONLY, DECLARED_ONLY = "confirmed", "code_only", "declared_only"
ROLES = {"may_produce": "produces", "may_consume": "consumes"}


@dataclass(frozen=True)
class Source:
    """One witness's citation for a fact."""

    witness_type: str  # CODE | PLATFORM | DECLARED | ADMIN
    witness: str  # the rule (code) or pack (claims) that read it
    file: str | None = None
    line: int | None = None
    repo: str | None = None
    commit: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v is not None}


@dataclass
class Use:
    """One application producing to or consuming from a channel, by one side's account."""

    app: str  # the application's node ID
    role: str  # produces | consumes
    sources: list[Source] = field(default_factory=list)


@dataclass
class Result:
    app: str
    role: str
    channel: str
    agreement: str
    sources: list[Source]


def compare(channel: str, code: list[Use], claimed: list[Use], checked: bool) -> list[Result]:
    """Each application's use of the channel, with its agreement state. `checked`: some
    witness other than the code mentions the channel. Unchecked, nothing is compared: a topic
    no platform source lists isn't `code only`, nobody looked (§7)."""
    if not checked:
        return []
    by_key: dict[tuple[str, str], tuple[list[Source], list[Source]]] = {}
    for use in code:
        by_key.setdefault((use.app, use.role), ([], []))[0].extend(use.sources)
    for use in claimed:
        by_key.setdefault((use.app, use.role), ([], []))[1].extend(use.sources)
    out = []
    for (app, role), (from_code, from_claims) in sorted(by_key.items()):
        if from_code and from_claims:
            agreement = CONFIRMED
        elif from_code:
            agreement = CODE_ONLY
        else:
            agreement = DECLARED_ONLY
        out.append(Result(app, role, channel, agreement, _unique(from_code + from_claims)))
    return out


def _unique(sources: list[Source]) -> list[Source]:
    seen: set[Source] = set()
    out = []
    for s in sources:
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out
