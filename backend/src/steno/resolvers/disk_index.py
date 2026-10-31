"""The JVM helper's two largest outputs, indexed on disk instead of loaded whole (D127).

Every expression a rule may capture and every function's calls grow with the repository: on
Elasticsearch they're about 2 GB of JSON, several times that once loaded into Python. The helper
leaves them as JSON Lines (`--parts`); this reads each line once into a SQLite file beside them,
and answers the resolver's lookups from it: an expression by its position, a function's calls by
its symbol, and every function's calls in order. Rules match a small share of the expressions,
and a flow walk reads each function's calls many times, so recent calls are cached.
"""

import json
import sqlite3
from collections import OrderedDict
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

_DECODER = json.JSONDecoder()
CACHED_FUNCTIONS = 20_000  # a function's calls, decoded, kept for repeat lookups
_ROWS_PER_INSERT = 5_000


class DiskCalls(Mapping[str, list[dict[str, Any]]]):
    """Each function's calls, read from the index on demand; iterates in the helper's order."""

    def __init__(self, db: sqlite3.Connection):
        self._db = db
        self._cache: OrderedDict[str, list[dict[str, Any]] | None] = OrderedDict()
        self._len: int | None = None

    def __getitem__(self, symbol: str) -> list[dict[str, Any]]:
        if symbol in self._cache:
            self._cache.move_to_end(symbol)
            found = self._cache[symbol]
        else:
            row = self._db.execute("SELECT body FROM calls WHERE symbol = ?", (symbol,)).fetchone()
            found = json.loads(row[0]) if row else None
            self._cache[symbol] = found
            if len(self._cache) > CACHED_FUNCTIONS:
                self._cache.popitem(last=False)
        if found is None:
            raise KeyError(symbol)
        return found

    def __iter__(self) -> Iterator[str]:
        for (symbol,) in self._db.execute("SELECT symbol FROM calls ORDER BY rowid"):
            yield symbol

    def __len__(self) -> int:
        if self._len is None:
            self._len = self._db.execute("SELECT count(*) FROM calls").fetchone()[0]
        return self._len

    def items(self) -> Iterator[tuple[str, list[dict[str, Any]]]]:  # type: ignore[override]
        # A cursor of its own, so lookups made while iterating don't disturb it
        for symbol, body in self._db.execute("SELECT symbol, body FROM calls ORDER BY rowid"):
            yield symbol, json.loads(body)


class HelperIndex:
    """`exprs.jsonl` and `calls.jsonl` in `parts`, indexed into `parts/index.sqlite`."""

    def __init__(self, parts: Path):
        self._db = sqlite3.connect(parts / "index.sqlite", check_same_thread=False)
        self._db.executescript(
            """
            PRAGMA journal_mode = OFF;
            PRAGMA synchronous = OFF;
            CREATE TABLE exprs (file TEXT, sl INT, sc INT, el INT, ec INT, body TEXT);
            CREATE TABLE calls (symbol TEXT PRIMARY KEY, body TEXT);
            """
        )
        self.expressions = self._load_exprs(parts / "exprs.jsonl")
        self._load_calls(parts / "calls.jsonl")
        self._db.executescript(
            """
            CREATE INDEX exprs_at ON exprs (file, sl, sc, el, ec);
            CREATE INDEX exprs_lines ON exprs (file, sl, el);
            """
        )
        self.calls = DiskCalls(self._db)

    def _load_exprs(self, path: Path) -> int:
        count = 0
        rows: list[tuple[Any, ...]] = []
        for line in _lines(path):
            e = json.loads(line)
            rows.append((e["file"], e["sl"], e["sc"], e["el"], e["ec"], line))
            count += 1
            if len(rows) >= _ROWS_PER_INSERT:
                self._db.executemany("INSERT INTO exprs VALUES (?, ?, ?, ?, ?, ?)", rows)
                rows.clear()
        self._db.executemany("INSERT INTO exprs VALUES (?, ?, ?, ?, ?, ?)", rows)
        return count

    def _load_calls(self, path: Path) -> None:
        rows: list[tuple[str, str]] = []
        for line in _lines(path):
            # `["symbol",[...]]`: read the symbol, keep the calls as text until they're asked for
            symbol, end = _DECODER.raw_decode(line, 1)
            rows.append((symbol, line[end + 1 : -1]))
            if len(rows) >= _ROWS_PER_INSERT:
                self._db.executemany("INSERT OR REPLACE INTO calls VALUES (?, ?)", rows)
                rows.clear()
        self._db.executemany("INSERT OR REPLACE INTO calls VALUES (?, ?)", rows)

    def expr(self, file: str, sl: int, sc: int, el: int, ec: int) -> dict[str, Any] | None:
        """The expression at exactly this position; the last one written, as a dict would."""
        row = self._db.execute(
            "SELECT body FROM exprs WHERE file = ? AND sl = ? AND sc = ? AND el = ? AND ec = ?"
            " ORDER BY rowid DESC LIMIT 1",
            (file, sl, sc, el, ec),
        ).fetchone()
        return json.loads(row[0]) if row else None

    def exprs_on(self, file: str, sl: int, el: int) -> list[dict[str, Any]]:
        """Every expression spanning these lines, in the helper's order."""
        rows = self._db.execute(
            "SELECT body FROM exprs WHERE file = ? AND sl = ? AND el = ? ORDER BY rowid",
            (file, sl, el),
        )
        return [json.loads(body) for (body,) in rows]

    def all_exprs(self) -> Iterator[dict[str, Any]]:
        """Every expression, in the helper's order."""
        for (body,) in self._db.execute("SELECT body FROM exprs ORDER BY rowid"):
            yield json.loads(body)

    def close(self) -> None:
        self._db.close()


def _lines(path: Path) -> Iterator[str]:
    if not path.exists():
        return
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if line:
                yield line
