"""Routing a question: where to look, with a confidence, so the consuming agent knows
when to trust the answer. Flows first (docs/plan/routing-design.md):

    Q0  what kind of question it is           (find a flow, what depends on X, what is X, …)
    propose  flows named in the question, keyword search, semantic search; only ever adds
    Q4  does each candidate do what was asked  reads each candidate's digest, on its own
    roll up  an application scores by its best flow, a space by its best application
    N4  which of several similar names is meant

Summaries don't choose where to look: measured, a summary line picks the wrong application
before Jev reads the right flow. Nothing above `FITS`: the answer says nothing fits.

The backend (`steno.decisions`): the stand-in by default, which can't read, so it ranks by keyword
matches, marked `uncalibrated`; or Jev, directly or through a gateway. If Jev can't answer, the
search still routes from the stand-in and says why. `STENO_DECISION_BACKEND=disabled` switches
every decision off. `steno route "<question>"` shows every candidate, its score, and its reason.
"""

from steno.routing.route import route

__all__ = ["route"]
