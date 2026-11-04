---
name: rule-pack-author
description: Write or change Steno rules and rule packs from a plain-language request, such as "topics under app.kafka.topics in application.yml are Kafka topics" or "SQLAlchemy models with __tablename__ map to tables", or work through the coverage report's open items ("work the top 10 coverage items"). Finds real examples in the code, learns what an unfamiliar library does (cloning its repository or reading its public source), maps it to Steno's node, edge, clue, and entry-point vocabulary, proposes new types instead of inventing them, writes the rule and its test cases, proves it with steno rules test and steno rules check, marks patterns harmless or expected, and opens the pull request. Use whenever someone asks to recognize a framework pattern, add or fix a rule, create a rule pack, act on a coverage-report item, or work the coverage report.
---

# Rule pack author

You help a person write **rules**: "when you see this code, it means this fact." The person owns the result; you do the legwork and explain your choices.

Two ways in: a **request** ("recognize X"; the workflow below), or **working the coverage report** (its open items, in order; see the last section, which reuses the workflow for each item). Steno runs no drafting job of its own: rules are written here, on the developer's machine, and approved by a person on a pull request (D147).

A rule is an ast-grep `match` (where the pattern is) plus a Steno `emit` (what it means). Rules are deterministic YAML; nothing you write runs an LLM at ingestion time.

## Read first, every time

1. `docs/how-it-works/ingestion/rules-and-packs.md`: the rule format, emits, anchors, identity properties, clue types, config rules, pack layout, tests. **This is the source of truth for syntax.**
2. `docs/how-it-works/graph-reference.md` §3 (node types) and §4 (relationship types, with what each connects): the vocabulary you may emit.
3. The existing packs under `rule-packs/`, so you extend instead of duplicating.
4. **What Steno stores** (D136, `docs/concepts/graph.md` §7): an application's placement, its **surface** (every way in: endpoints, consumed topics, schedules; every way out: calls, publishes, table reads and writes, files, external systems), and what each flow reaches. Agents read everything else from the code Steno points to.

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

**Then name what it's for (D139):** the **facet** it serves (Behavior, Structure, Components, Artifacts, Interfaces, Messaging, Routing, Data, or Schedules) and the **use-case question** it helps answer ("who consumes this channel?", "what writes this table?", "how do I trigger this flow?"; see `docs/purpose/use-cases.md`). If you can't name both, say so and don't write the rule.

**Stay on the surface.** A rule records an entry point, an effect, a name, or a pointer to a payload class. It doesn't record depth an agent can read from the code: validation rules and constraints, what an interceptor requires or adds, the order of internal calls, what a function computes. If a request asks for that, explain the line and offer the surface fact instead (for example, the endpoint and its request class, not the class's `@Size` limits).

### 2. Find real examples

Never write a rule from imagination. In the folder from step 0:
- Find **2–5 real occurrences** with `grep`/ripgrep, or a quick ast-grep run (step 6).
- Find at least one **near miss**: code that looks similar but must *not* match (`dict.get(...)` next to `client.get(...)`, a commented-out property, a test file).
- Note **variations**: keyword vs. positional arguments, annotated vs. plain assignments, YAML nesting vs. dotted keys, multiple decorators.

### 3. Map it to Steno's vocabulary

**First, which witness is this?** Ask what consumes the information (`docs/concepts/witnesses-and-facets.md` §2):

| What consumes it | Witness type | The pack emits |
|---|---|---|
| The running application: its source, its own `application.yml`, its build | `CODE` | **Facts** (nodes, edges, clues, entry points), written straight to the graph |
| Infrastructure applies it: XLD definitions, Strimzi topics and ACLs, Helm, CronJobs, gateway config in a deploy repository | `PLATFORM` | **Claims** (`claim:`), compared with the code by reconcile |
| Nothing; it only describes: a manifest, `catalog-info.yaml`, CODEOWNERS | `DECLARED` | **Claims** |

The witness type is set **once per pack** (`witness_type:` in `pack.yaml`), never per rule; one pack, one witness type. A repository holding both kinds of file gets two packs. Claims use only the fixed claim types (`channel`, `may_produce`, `may_consume`, `deployable`, `name`, `schedule`, `route`; fields in `docs/how-it-works/ingestion/claims-and-reconcile.md` §3). Platform files are usually lists of records, so claim rules are config rules with `each` and `when` (§4 there). Turn the pack on only for its files: `enabled_when.files`, with a `content` regex when the extension alone would match unrelated files. Names in claims (an application, a topic) must be the names that system uses; identity links them to Steno's applications at reconcile time, and one that matches nothing becomes a visible stub, never a guess. A `${placeholder}` a claim can't resolve drops the claim with a reason; say so if the platform's own placeholder syntax (XLD `{{…}}`, Helm templates) carries the real value.

Then decide what to emit, using only types that exist in the docs:

| Question | Emit |
|---|---|
| Is it a **thing** others can call, read, or depend on (endpoint, topic, table, entity, external system, schedule)? | `node`, with all **identity properties** from rules-and-packs.md §3 |
| Is it a **connection** (exposes, calls, produces, consumes, reads, writes, maps to)? | `edge`. Check the from → to pair is allowed in graph-reference.md §4. |
| Is part of the fact **somewhere else** (a router's prefix, a mount point, a config value)? | `clue` of an existing clue type, labeled by the symbol it's about |
| Does it **start work** (handles a request, consumes a message, runs on a schedule)? | `entry_point` with the trigger node and `@function` |
| Does matching depend on a **type** the pattern can't see? | a `where:` condition |

Rules never create `Flow` or `Step` nodes, and never reference another rule.

**An org's own messaging framework is not a new type (D61).** Communication is a **sender**, a **receiver**, and the **shared key** that joins them. For an internal framework (Kafka-based or not), use an org-defined `Interface` label (for example `PxChannel`), identified by `name`, and write two rules in the org pack: the sender (`edge: PRODUCES` from `@function` to `{ PxChannel: { name: $CHANNEL } }`) and the receiver (`entry_point` with that channel as the trigger). Applications connect wherever the names match. Only say the label you chose; no design proposal is needed.

**If nothing fits, stop and propose.** If the fact needs a node type, edge type, property, or clue type that doesn't exist, or a join no clue type covers (that would be a plugin), don't invent it and don't write the rule yet. Tell the person:
- what's missing, and why the existing types don't fit,
- what you'd add (name, identity properties, what it connects to),
- that it's a schema or design change, which belongs in `docs/` once they agree.

### 4. Choose the pack

- Find the existing pack for the framework in `rule-packs/<ecosystem>/`.
- Otherwise create one: `rule-packs/<java|python|…>/steno-pack-<framework>/` for public frameworks (`source: core`), or `rule-packs/org/<org>-<name>/` for internal ones (`source: org`). Fill in `pack.yaml` (see rules-and-packs.md §6), including `enabled_when.dependencies` as they appear in the build file.

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
- `expected.yaml`: the facts it must produce (format in rules-and-packs.md §7), or for a claim pack the `claims:` (with `line` and `witness_type` where it matters).
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
- **What it's for:** the facet and the use-case question it helps answer.
- **What it emits** (node / edge / clue / entry point, or the claims and the pack's witness type).
- **Measured:** matches across N files, versus the independent estimate. Misses or false positives you found and how you handled them.
- **Proposed, not done:** any new type or plugin the request needs.
- **Tests:** the `steno rules test` result for the pack.

## Working the coverage report (D147)

The coverage report lists what Steno might be missing, grouped and ranked: connections between applications first, internal libraries before external ones (D146; `docs/how-it-works/ingestion/coverage-report.md` §6). Work it top down, a framework at a time. Steno must be running with the organization ingested (`make up`, `make migrate`).

**1. Pull the worklist.**

```sh
cd backend && uv run steno coverage patterns --limit 10        # frameworks and patterns, ranked
uv run steno coverage show <fingerprint>                        # where it occurs, links, samples
```

Patterns sharing an owner (a library, a package root) are one framework: learn it once and write its rules together.

**2. Triage each pattern.** Decide one of these, and say why:

| Decision | When | Then |
|---|---|---|
| **A rule** | It states a surface fact (an entry point, a call, a publish, a table read or write, a file, an external system) and you can name the facet and use-case question (step 1 above) | Steps 3–6 below |
| **Harmless** | It never reaches the surface: a utility library, an in-process helper, a logging call | `steno coverage mark <fp> --state ignored --note "<why>"` |
| **Expected** | A real gap that's legitimately outside the organization: an endpoint only external clients call, a topic a vendor consumes | `steno coverage mark <fp> --state expected --note "<why>"` |
| **Needs source** | An internal library whose repository isn't ingested, and you can't get it | Tell the person which repository to connect; leave it open |
| **A blind spot** | A mechanism the catalog doesn't list, or one Steno's vocabulary can't express | Add a row to `docs/how-it-works/ingestion/what-steno-must-see.md` and propose the design (step 3 above: "If nothing fits") |

By signal: `internal_library` and `unknown_hook` on an organization type are usually an **internal framework** (a sender and a receiver, D61); `external_library` is usually harmless or a core-pack rule; `silent_repository` and `silent_witness` usually need a PLATFORM or DECLARED pack for files no pack reads; `one_sided` means the other end's rule is missing in the application that holds it, or it's expected.

**3. Learn what the code does.** Don't guess from names. In order:
- The library's own repository, when it's ingested: `steno coverage show` links its code at the ingested commit.
- A local clone. Sparse-clone only what you need into a scratch folder with the person's own credentials (`git clone --depth 1 --filter=blob:none --sparse <url>`, then `git sparse-checkout set <folders>`), and delete it afterwards.
- The public library's source or documentation, for an external library.

Then write the rule and its tests as in the workflow above (steps 2–7), using the samples from `steno coverage show` and the clone as the code to learn from.

**4. Prove it.**

```sh
uv run steno rules test <pack-name>
uv run steno rules check <path/to/rules/rule-id.yaml> -p <fingerprint> [-p <fingerprint> …]
```

`steno rules check` runs the rule over every file the pattern occurs in (read from the git host at the ingested commit). It exits 1 when the rule states no surface fact, explains none of the pattern, or would create a generic or already-taken application name. Fix it, or change the triage. Misses are fine when you can explain them; say so in the pull request.

**5. Open the pull request** as the developer, one per destination: rules for public libraries to the core packs in `rule-packs/`, rules for the organization's frameworks to its pack repository (`STENO_RULE_PACK_DIRS`; D129). Put each rule's `steno rules check` output, its facet, and its use-case question in the description. Follow the repository's own branch and PR conventions.

**6. Mark the patterns** the pull request explains: `steno coverage mark <fp>… --state proposed --note "<PR URL>"`. Once it merges and the next ingestion runs, they become `covered` on their own; any that still occur reopen.

Report back as in step 8, plus what you marked harmless or expected and why.

## Guardrails

- Never invent node, edge, property, or clue types. Propose them.
- Never write a rule that only adds depth (D136, D139): it must name its facet and use-case question, and emit only surface facts.
- Never edit another pack's rules without saying so.
- Rules describe what code **says**, not runtime behavior. Values that are only known at runtime stay unresolved; don't guess them.
- Keep test inputs minimal and free of secrets.
- Never mark a pattern harmless or expected without a note saying why; the note is what the next person reads.
- Delete scratch clones when you're done, unless the person asks to keep them.
