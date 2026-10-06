---
name: rule-pack-author
description: Write or change Steno rules and rule packs from a plain-language request, such as "topics under app.kafka.topics in application.yml are Kafka topics" or "SQLAlchemy models with __tablename__ map to tables". Finds real examples in the code, maps them to Steno's node, edge, clue, and entry-point vocabulary, proposes new types instead of inventing them, writes the rule and its test cases, and measures how often the pattern matches. Use whenever someone asks to recognize a framework pattern, add or fix a rule, create a rule pack, or act on a coverage-report item.
---

# Rule pack author

You help a person write **rules**: "when you see this code, it means this fact." The person owns the result; you do the legwork and explain your choices.

A rule is an ast-grep `match` (where the pattern is) plus a Steno `emit` (what it means). Rules are deterministic YAML; nothing you write runs an LLM at ingestion time.

## Read first, every time

1. `docs/rule-packs.md`: the rule format, emits, anchors, identity properties, clue types, config rules, pack layout, tests. **This is the source of truth for syntax.**
2. `docs/knowledge-graph.md` §3 (node types) and §4 (relationship types, with what each connects): the vocabulary you may emit.
3. The existing packs under `rule-packs/`, so you extend instead of duplicating.

## Workflow

### 0. Confirm where the code is

Rules are learned from real code, so first establish **which local folder holds the code to learn from** (for example a clone of Contextualized). If the request doesn't name one, **ask**. Don't search the disk for it, and don't assume the current repository: that's Steno itself, not the code the rule is for. If the person doesn't have a local copy, ask them to clone it.

### 1. Restate the request as a fact

Turn the person's words into one sentence:

> When **[evidence in code or config]**, Steno should record **[node / edge / clue / entry point]**.

A request often implies **more than one fact**. Work out the full set and say which you'll cover. For example, "I want Steno to know this is a Kafka topic via `application.yml`" usually means:
- **the topic exists** (a `KafkaTopic` node from the config key: a config rule), and
- **who uses it**: the function that consumes it (`@KafkaListener(topics = "${app.kafka.topics.orders}")`) or produces to it (`kafkaTemplate.send(...)`). Those need **code rules**. The `${...}` placeholder is resolved automatically from config (`property` clues), so the code rule doesn't read the YAML itself.

If the request is ambiguous in a way that changes the rule, such as which config keys or which annotation, ask. Otherwise decide, and say what you assumed.

### 2. Find real examples

Never write a rule from imagination. In the folder from step 0:
- Find **2–5 real occurrences** with `grep`/ripgrep, or a quick ast-grep run (step 6).
- Find at least one **near miss**: code that looks similar but must *not* match (`dict.get(...)` next to `client.get(...)`, a commented-out property, a test file).
- Note **variations**: keyword vs. positional arguments, annotated vs. plain assignments, YAML nesting vs. dotted keys, multiple decorators.

### 3. Map it to Steno's vocabulary

Decide what to emit, using only types that exist in the docs:

| Question | Emit |
|---|---|
| Is it a **thing** others can call, read, or depend on (endpoint, topic, table, entity, external system, schedule)? | `node`, with all **identity properties** from rule-packs.md §3 |
| Is it a **connection** (exposes, calls, produces, consumes, reads, writes, maps to)? | `edge`. Check the from → to pair is allowed in knowledge-graph.md §4. |
| Is part of the fact **somewhere else** (a router's prefix, a mount point, a config value)? | `clue` of an existing clue type, labeled by the symbol it's about |
| Does it **start work** (handles a request, consumes a message, runs on a schedule)? | `entry_point` with the trigger node and `@function` |
| Does matching depend on a **type** the pattern can't see? | a `where:` condition |

Rules never create `Flow` or `Step` nodes, and never reference another rule.

**If nothing fits, stop and propose.** If the fact needs a node type, edge type, property, or clue type that doesn't exist, or a join no clue type covers (that would be a plugin), don't invent it and don't write the rule yet. Tell the person:
- what's missing, and why the existing types don't fit,
- what you'd add (name, identity properties, what it connects to),
- that it's a schema or design change, which belongs in `docs/` once they agree.

### 4. Choose the pack

- Find the existing pack for the framework in `rule-packs/<ecosystem>/`.
- Otherwise create one: `rule-packs/<java|python|…>/steno-pack-<framework>/` for public frameworks (`source: core`), or `rule-packs/org/<org>-<name>/` for internal ones (`source: org`). Fill in `pack.yaml` (see rule-packs.md §6), including `enabled_when.dependencies` as they appear in the build file.

### 5. Write the rule

- One rule per file in `rules/`; the file name is the `id`.
- `description:` says what it recognizes, with a one-line example.
- Prefer **structural** matching (`kind`, `has`, `inside`, `field`) over long literal patterns; it survives formatting differences. Constrain captures with `constraints:` regexes.
- One concept per rule. A clue and the fact that uses it are usually separate rules.

### 6. Measure it against real code

```sh
uv run .claude/skills/rule-pack-author/scripts/check_match.py <rule.yaml> <repo-or-dir> [--limit N]
```

It prints every match with its captures (code rules through ast-grep, config rules through key paths). It doesn't check `where:` or the emitted facts; `steno rules test` (step 7) does.

- Run it on the examples **and on the whole repository**. Compare the count with an independent estimate (for example, `grep -c "@router\."`). **A pattern that looks right can silently miss cases.** The first FastAPI prefix pattern, `$ROUTER = APIRouter($$$, prefix=$PREFIX, $$$)`, matched only 1 of Contextualized's 8 routers, because it needed another argument next to `prefix=`. A structural rule found all 8.
- Investigate every miss and every unexpected match, then adjust. Repeat until the numbers make sense.

### 7. Write the test cases

Under `tests/<rule-id>/<case-name>/`:
- `input/`: the **smallest** real snippet that shows the case, trimmed. Include every file the case needs; a rule that relies on clues needs the files that emit them (the router's `__init__.py` for an endpoint's prefix).
- `expected.yaml`: the facts it must produce (format in rule-packs.md §7).
- At least one **negative case** (`expected.yaml` with nothing in it) when the pattern could over-match.
- **Never copy secrets**, tokens, internal hostnames, or personal data into test inputs. Replace them with placeholders.

Then run the pack's tests until they pass:

```sh
cd backend && uv run steno rules test <pack-name>
```

It runs every case through the full engine (symbol resolver, assemblers, `where:`) and shows what's missing or unexpected. To see everything a pack produces on a whole repository, use `uv run steno extract <folder> --json facts.json`.

### 8. Report back

Give the person a short summary:
- **What it recognizes**, in one sentence, and which files you added or changed.
- **What it emits** (node / edge / clue / entry point).
- **Measured:** matches across N files, versus the independent estimate. Misses or false positives you found and how you handled them.
- **Proposed, not done:** any new type or plugin the request needs.
- **Tests:** the `steno rules test` result for the pack.

## Guardrails

- Never invent node, edge, property, or clue types. Propose them.
- Never edit another pack's rules without saying so.
- Rules describe what code **says**, not runtime behavior. Values that are only known at runtime stay unresolved; don't guess them.
- Keep test inputs minimal and free of secrets.
