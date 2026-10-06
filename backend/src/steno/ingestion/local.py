"""Ingest a local folder without a job or a database: parse, extract, assemble. Nothing more.

The developer path, never the worker's. Used by:
- `steno extract <folder>`: the facts JSON for any folder
- `steno rules test` and `make test`: every rule pack test case, and the graph-building test

Real ingestion jobs don't come through here: the worker runs `pipeline.py`, where each step is
its own recorded stage, followed by flows, write and cards.
"""

from pathlib import Path

from steno.assemblers.assemble import assemble
from steno.extraction.engine import Engine
from steno.rule_packs.packs import Pack


def run_folder(repo: Path, packs: list[Pack]) -> Engine:
    """The engine after parse, extract, and assemble; its `out` holds the completed facts."""
    engine = Engine(repo, packs)
    engine.parse()
    engine.extract()
    assemble(engine.out, engine.resolver, engine.repo)
    return engine
