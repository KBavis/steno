# Rule packs

Rule packs teach Steno to recognize what code **does**. This folder holds every pack.

## What a rule pack is

Steno reads repositories without running them, so it has to recognize what code means from how it's written. In a FastAPI app, a function decorated with `@router.post("/projects")` is an HTTP endpoint. In a Spring app, a class with `@Entity @Table(name="users")` maps to the `users` table.

An **extractor rule** captures one such pattern: **"when you see this code, it means this fact."** A **rule pack** is a versioned bundle of rules for one framework or one organization: `steno-pack-fastapi`, `steno-pack-spring-kafka`, `yourorg-internal`.

- Rules are **deterministic**. No AI runs when Steno ingests code; a rule matches or it doesn't.
- Packs are **auto-enabled** from a repository's dependencies: `fastapi` in `requirements.txt` turns on `steno-pack-fastapi`.
- The **coverage report** after every ingestion lists code that looked important but that no rule explained, ranked by how often it appears. It tells you which rule to write next.

The full format is in **[docs/extractor-rules.md](../docs/extractor-rules.md)**. This page is the practical guide.

## Layout

Packs are grouped by ecosystem, meaning a language plus its config files:

```
rule-packs/
├─ java/                         Spring, JPA, Kafka, …
├─ python/                       FastAPI, SQLAlchemy, …
└─ org/                          your organization's own packs, any language
```

Each pack:

```
steno-pack-fastapi/
├─ pack.yaml                     name, version, language, description, enabled_when
├─ rules/
│  └─ fastapi-endpoint.yaml      one rule per file; the file name is the rule id
└─ tests/
   └─ fastapi-endpoint/          one folder per rule
      └─ nested-prefix/          one folder per test case
         ├─ input/               small copies of real code
         └─ expected.yaml        the facts this case must produce
```

## What a rule looks like

A rule has two halves:

- **`match`**: where the pattern is. Plain [ast-grep](https://ast-grep.github.io/) syntax, so its docs and [playground](https://ast-grep.github.io/playground.html) work as they are.
- **`emit`**: what it means, in Steno's vocabulary.

```yaml
id: fastapi-endpoint
description: A route handler, e.g. @router.post("/projects/{project_id}")
match:
  rule:
    kind: decorated_definition
    has:
      kind: decorator
      has:
        pattern: $ROUTER.$METHOD($PATH $$$)
  constraints:
    METHOD: { regex: "^(get|post|put|patch|delete)$" }
emit:
  - node: [Interface, HttpEndpoint]     # a thing in the graph
    as: endpoint
    method: $METHOD
    path: $PATH
    prefixed_by: $ROUTER                # the router's prefix chain is added later
  - edge: EXPOSES                       # a connection: this app exposes the endpoint
    from: "@app"
    to: endpoint
  - entry_point:                        # this function starts a flow
      trigger: endpoint
      function: "@function"
```

A rule can emit four things:

| Emit | Meaning |
|---|---|
| `node` | A thing: endpoint, topic, table, entity, … |
| `edge` | A connection: `EXPOSES`, `CALLS`, `PRODUCES`, `MAPS_TO`, … |
| `clue` | A partial fact for Steno to combine later, e.g. "this router's prefix is `/jobs`" |
| `entry_point` | "This function is started by this trigger." Steno derives flows from these. |

**Clues** exist because some facts are spread across files. A FastAPI endpoint's full path comes from the decorator, the router's `prefix=`, and wherever the router is mounted. Each rule reports only what it sees, and Steno joins the pieces by the symbol they're about. Rules never refer to each other. See [Clues](../docs/extractor-rules.md#4-clues-decided).

## Writing a rule

### With the skill (recommended)

In Claude Code, describe what you want recognized:

> "Topics declared under `app.kafka.topics` in `application.yml` are Kafka topics."
>
> "In Contextualized, every SQLAlchemy model with a `__tablename__` maps to a table."

The [`rule-pack-author` skill](../.claude/skills/rule-pack-author/SKILL.md) then:
1. finds real examples in the code,
2. maps them to existing node, edge, and clue types, and **proposes** a new type if none fits instead of inventing one,
3. writes the rule and its tests,
4. counts how often the pattern matches across the repository, to catch both misses and false positives.

You review and own the result. GitHub Copilot and other agents can follow the same guide; [AGENTS.md](./AGENTS.md) points them to it.

### By hand

1. Find two or three real examples of the pattern, and one similar-looking case that must **not** match.
2. Write the `match` half and try it in the ast-grep playground.
3. Write the `emit` half using the vocabulary in [docs/extractor-rules.md §3–4](../docs/extractor-rules.md#3-what-a-rule-emits-decided).
4. Add a test case under `tests/<rule-id>/`.
5. Check it against real code:
   ```sh
   uv run .claude/skills/rule-pack-author/scripts/check_match.py \
     rule-packs/python/steno-pack-fastapi/rules/fastapi-endpoint.yaml path/to/repo
   ```
   It shows every match and what was captured.
6. Run the pack's tests, which check the emitted facts against `expected.yaml`:
   ```sh
   cd backend && uv run steno rules test steno-pack-fastapi
   ```
   `uv run steno extract <folder>` shows everything the enabled packs produce on a whole repository.

## Core packs and org packs

| | Core packs (`java/`, `python/`, …) | Org packs (`org/`) |
|---|---|---|
| **Covers** | Public frameworks: Spring, FastAPI, JPA, Kafka clients | Your internal frameworks and conventions |
| **`source`** | `core` | `org` |
| **Typical size** | 5–20 rules per framework | A handful of rules |

If a pattern needs something YAML can't express, such as joining facts in a way no clue type covers, that's a **plugin** (code). Plugins are rare and need a design discussion first.
