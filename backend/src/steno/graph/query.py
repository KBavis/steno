"""How a search's words become a full-text query: deterministic, no LLM.

Agents should send keywords (the server's instructions say so), but a question can arrive as a
sentence, with typos, or in different words than the code uses. So each word is:

- kept only if it says something: stop words ("how", "can", "the") and single letters are
  dropped, so "how can I delete a conversation" searches `delete conversation`;
- matched as itself (boosted), as a prefix (`conversation*`), and by its stem (`categorize`
  finds `category`, `categories`: `categor*`);
- matched with a typo or two when it's long enough (`subsciption~1`);
- matched by the abbreviations the organization's names use: a name part that keeps the word's
  first letter and some of the rest, in order (`clt` for client, `prd` for prod, `sub` for
  subscription), taken from the names in the graph, so `client prod sub` finds
  `ent-clt-prd-sub`.

Every word must match (AND). When nothing matches every word, the search drops one word at a
time (and says which) until something does: `rank_tiers`.
"""

import re
from collections.abc import Iterable, Iterator
from itertools import combinations

# Words that never name anything in the code
STOP_WORDS = frozenset(
    """a an the and or but of to in on at by for from with without into onto over under about
    is are was were be been being am do does did done doing have has had having can could will
    would shall should may might must i me my we us our you your they them their it its this
    that these those there here what which who whom whose when where why how all any some each
    every no not only just also very so than then too more most such own same other new
    please tell show find give get let know want need make way ways thing things through via
    using use used""".split()
)
# Suffixes stripped for the stem, longest first
SUFFIXES = (
    "izations", "ization", "isations", "isation", "ations", "ation", "izing", "ising", "izes",
    "ises", "ized", "ised", "ize", "ise", "ings", "ing", "ions", "ion", "ies", "ied", "ers", "er",
    "ed", "es", "s", "y",
)  # fmt: skip
STEM_MIN = 4  # a stem shorter than this matches too much
FUZZY_MIN = 5  # words shorter than this get no typo matching
ABBREVIATION_MIN = 3  # name parts shorter than this aren't taken as abbreviations
ABBREVIATIONS_PER_WORD = 8
MAX_WORDS = 6  # beyond this, the rest are ignored (and said so)
SPECIAL = '+-&|!(){}[]^"~*?:\\/'


def words_of(text: str) -> list[str]:
    """A query's words, split the way the full-text index splits names: `budgeting-app` →
    `budgeting`, `app`; `/auth/register` → `auth`, `register`."""
    return "".join(c if c.isalnum() or c == "_" else " " for c in text.lower()).split()


def meaningful(words: Iterable[str]) -> list[str]:
    """Without stop words and single letters, each once. When that leaves nothing (a query of
    only stop words), the words longer than one letter."""
    words = list(words)
    kept = list(dict.fromkeys(w for w in words if len(w) > 1 and w not in STOP_WORDS))
    return kept or list(dict.fromkeys(w for w in words if len(w) > 1))


def stem(word: str) -> str:
    for suffix in SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= STEM_MIN:
            return word[: -len(suffix)]
    return word


def is_abbreviation(part: str, word: str) -> bool:
    """`clt` abbreviates `client`: same first letter, shorter, and the rest of its letters in
    the word in order."""
    if len(part) < ABBREVIATION_MIN or len(part) >= len(word) or part[0] != word[0]:
        return False
    rest = iter(word[1:])
    return all(c in rest for c in part[1:])


def abbreviations(word: str, vocabulary: Iterable[str]) -> list[str]:
    """The name parts in the graph that abbreviate the word, closest first."""
    found = [p for p in vocabulary if is_abbreviation(p, word)]
    return sorted(found, key=lambda p: (-len(p), p))[:ABBREVIATIONS_PER_WORD]


def escape(word: str) -> str:
    return "".join(f"\\{c}" if c in SPECIAL else c for c in word)


def forms(word: str) -> list[str]:
    """What the word may appear as inside a function name: itself, and its stem."""
    s = stem(word)
    return [word, s] if s != word else [word]


def clause(word: str, vocabulary: Iterable[str] = ()) -> str:
    """The full-text clause for one word: itself (boosted), as a prefix, by its stem, with a
    typo, or by an abbreviation the graph's names use."""
    e = escape(word)
    parts = [f"{e}^4", f"{e}*"]
    s = stem(word)
    if s != word:
        parts.append(f"{escape(s)}*")
    if len(word) >= FUZZY_MIN:
        parts.append(f"{e}~{2 if len(word) >= 8 else 1}")
    parts += [f"{escape(a)}^2" for a in abbreviations(word, vocabulary)]
    return "(" + " OR ".join(parts) + ")"


def query(words: list[str], vocabulary: Iterable[str] = ()) -> str:
    vocabulary = list(vocabulary)
    return " AND ".join(clause(w, vocabulary) for w in words)


def rank_tiers(words: list[str]) -> Iterator[tuple[list[str], list[str]]]:
    """(the words to match, the words dropped): every word first, then one fewer at a time,
    down to half of them (never below one). The caller stops at the first tier with hits."""
    n = len(words)
    floor = max(1, (n + 1) // 2)
    for k in range(n, floor - 1, -1):
        for kept in combinations(words, k):
            yield list(kept), [w for w in words if w not in kept]


_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")


def split_identifiers(text: str) -> str:
    """The identifiers in a text as separate words, for the index: `git_commit` → `git commit`,
    `deleteConversation` → `delete conversation`, `DiffTaskRunner.execute` → `diff task
    runner execute`. The index keeps `git_commit` and camelCase names whole, so without these
    `git commits` or `delete conversation` wouldn't find them. Only identifiers that split are
    kept; each word once."""
    words: list[str] = []
    for token in re.findall(r"[A-Za-z0-9_]+", text):
        parts = [p for piece in token.split("_") for p in _CAMEL.split(piece) if p]
        if len(parts) > 1:
            words += [p.lower() for p in parts]
    return " ".join(dict.fromkeys(words))
