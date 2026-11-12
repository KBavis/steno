"""Generated summaries: applications, then the spaces above them, then the organization.

Bottom up: an application's summary is written from what Steno knows of it (`inputs.py`); a
space's from its applications' summaries; the organization's from its spaces'. Each is written
only when its inputs change (`steno.llm.generate` caches by input hash), and set on its node
as `summary` (a few sentences), `summary_line` (one sentence, for routing), `summary_hash`, and
`summary_model`. Nobody writes them by hand; an admin's correction (ADMIN, cited) comes later.

Run by the job's cards stage for the repository's applications, and by `steno summaries` for
the whole graph.
"""

from steno.summaries.run import Report, summarize

__all__ = ["Report", "summarize"]
