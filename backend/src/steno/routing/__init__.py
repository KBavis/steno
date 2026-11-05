"""Routing a question (D142): which spaces, applications, and flows it's about, each with a
confidence, so the consuming agent knows when to trust the answer.

Top down, so the cost grows with the tree's depth, not the number of flows:

    Q0  what kind of question it is            (find a flow, what depends on X, what is X, …)
    Q1  which spaces: one yes/no per space      reads each space's summary line
    Q2  which applications in those spaces      reads each application's summary line
        keyword search proposes candidate flows inside the chosen applications
    Q4  does each candidate do what was asked   reads each candidate's digest
    N4  which of several similar names is meant

Jev never decides alone (docs/plan/later/jev.md §3a): keyword search proposes, the decision
backend verifies and ranks, close calls keep at least two branches, and every decision can be
switched off (`STENO_DECISION_BACKEND=disabled`). When the flows can't be told apart with
confidence, the answer carries a `shortlist`: the chosen application's flows, each with what
it does, for the agent's own model to pick from.

The backend (`steno.decisions`): the stand-in by default, its confidences rankings from search,
marked `uncalibrated`, with no band; or Jev, directly or through a gateway. Measured on 15
questions, Jev behind this top-down path did worse than the stand-in (7 right first against 9),
because a summary line picks the wrong application before Jev reads the right flow. So the
stand-in stays the default until routing goes flows first (docs/plan/routing-design.md). If Jev
can't answer, the search still routes from the stand-in and says why.
"""

from steno.routing.route import route

__all__ = ["route"]
