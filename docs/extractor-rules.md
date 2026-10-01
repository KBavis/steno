# Extractor Rules

The format of Steno's extractor rules and rule packs: how a rule finds code, what it emits, how facts from different rules are combined, and how a rule proves it works.

Part of the [Design Doc](./DESIGN_DOC.md); it details [Ingestion §4](./ingestion.md#4-extractors-rules-for-being-agnostic). Status markers: **Decided** · **Proposed** · **Open**.

---

## 1. The idea (Decided)

**A rule is a pattern that finds code, plus what that code means as graph facts.**

```yaml
match:   # WHERE: standard ast-grep, unchanged
emit:    # WHAT IT MEANS: Steno's vocabulary
```

- `match` is plain [ast-grep](https://ast-grep.github.io/) rule syntax (tree-sitter based, many languages). Authors, and their AI assistants, can use ast-grep's docs and playground as they are.
- `emit` is written in **Steno's vocabulary**: the node and edge types of the [knowledge graph](./knowledge-graph.md), plus clues (below). Whoever writes it has to know that vocabulary; §4 lists it.
- Rules are **deterministic**. They're fixed YAML, executed by code. No LLM runs during ingestion. (An AI assistant may help a person *write* a rule; see §8.)

## 2. Anatomy of a rule (Decided)

```yaml
id: fastapi-endpoint                  # unique within the pack
description: A route handler, e.g. @router.post("/projects/{project_id}")
match:                                # ast-grep: rule + optional constraints / utils
  rule:
    kind: decorated_definition
    has:
      kind: decorator
      has:
        pattern: $ROUTER.$METHOD($PATH, $$$)
  constraints:
    METHOD: { regex: "^(get|post|put|patch|delete)$" }
where:                                # optional; checked after symbol resolution
  ...
emit:                                 # one or more outputs
  - ...
```

- **`language`** comes from the pack (`pack.yaml`). A rule may override it, e.g. a YAML config rule inside a Java pack.
- **Captures** (`$PATH`, `$ROUTER`, …) are ast-grep metavariables. In `emit` they stand for the captured code.
- **`where`** holds conditions a pattern can't check, because they depend on **types** or other resolved information:

  ```yaml
  where:
    $CLIENT: { type: [httpx.AsyncClient, httpx.Client] }   # client.get(...) on httpx, not dict.get(...)
  ```

  `where` is evaluated in the resolve pass; a match whose condition fails is dropped.

## 3. What a rule emits (Decided)

| Emit | What it is | Reaches the graph? |
|---|---|---|
| **`node`** | A thing: an endpoint, a topic, a table, an entity | Yes |
| **`edge`** | A connection between two things: `EXPOSES`, `CALLS`, `MAPS_TO`, … | Yes |
| **`clue`** | A partial fact that helps complete other facts: "this router has prefix `/jobs`" | No, it's consumed by a resolver |
| **`entry_point`** | "This function is started by this trigger" | Indirectly: flows are derived from entry points |

**Rules never create `Flow` or `Step` nodes.** Flows are derived in pass 2 by walking the call graph from every entry point ([Ingestion §2](./ingestion.md#passes-per-application-decided)). A rule only marks where a flow starts.

### Referring to things inside `emit`

| Form | Meaning | Example |
|---|---|---|
| `$NAME` | A captured value | `path: $PATH` |
| `"literal"` | A fixed value | `method: POST` |
| `@function`, `@class`, `@file`, `@app` | **Anchors**: the function or class the match is in, its file, or the application being ingested | `from: "@function"` |
| `as: name` | Names an emitted node so later lines in the same rule can refer to it | `as: endpoint` … `to: endpoint` |
| `{Label: {key: value}}` | An inline reference to a node by its identity; a stub is created if it doesn't exist yet | `to: { KafkaTopic: { name: $TOPIC } }` |
| `{http: {method, url}}` | An outbound HTTP target; the resolver decides whether it's another app's endpoint or an `ExternalSystem` | `to: { http: { method: $VERB, url: $URL } }` |

**Captured values are resolved, not taken literally.** When a capture is a variable, a constant, an f-string, a `${placeholder}`, or a settings lookup, the resolver follows it as far as it can (local assignments, constants, config properties). What it can't resolve, such as a URL built from a runtime value, is recorded as **unresolved** with lower confidence and shows up in the coverage report.

### Identity: what each node needs

Steno builds every node's stable ID from its identity properties ([Knowledge Graph §3](./knowledge-graph.md#properties-every-fact-carries)), so an emitted node must supply them:

| Node | Identity properties | Notes |
|---|---|---|
| `HttpEndpoint` | `method`, `path` | Plus the exposing app, implied |
| `GrpcMethod` | `service`, `method` | |
| `KafkaTopic` / `Queue` | `name` | `cluster` when known |
| `Schedule` | `kind`, `expression` | Plus the app, implied |
| `Entity` | `name` (the symbol) | |
| `Table` | `name` | `schema`, `datastore` when known |
| `DataStore` | `vendor`, `host`, `database` | |
| `ExternalSystem` | `host` | |

### Example: an endpoint (node + edge + entry point)

```yaml
emit:
  - node: [Interface, HttpEndpoint]
    as: endpoint
    method: $METHOD
    path: $PATH
    prefixed_by: $ROUTER            # the prefix-chain resolver adds /api/jobs (§5)
  - edge: EXPOSES
    from: "@app"
    to: endpoint
  - entry_point:
      trigger: endpoint
      function: "@function"
```

## 4. Clues (Decided)

Some facts are spread across files. Contextualized's endpoint `POST /api/jobs/projects/{project_id}` is assembled from three places:

```python
# routers/__init__.py
app_router = APIRouter(prefix="/api")
app_router.include_router(job_router)            # mounts it under /api

# routers/job.py
router = APIRouter(prefix="/jobs")
@router.post("/projects/{project_id}")
async def run_project_jobs(...): ...
```

No single pattern sees all of it. So each rule reports what it sees, and a **resolver** combines the pieces afterward.

### Rules never reference each other

A rule never calls another rule or names another rule's ID. Rules meet through a **shared label: the identity of the code a clue is about**. Picture a bulletin board:

1. **Pass 1 (per file):** every rule runs on every file, in any order, and pins notes. Each note is labeled with the *thing* it's about, by its fully qualified symbol, never with which rule wrote it:
   ```
   📌 prefix  about app.api.routers.job.router   → "/jobs"
   📌 prefix  about app.api.routers.app_router   → "/api"
   📌 mount   app.api.routers.job.router is inside app.api.routers.app_router
   📌 endpoint POST "/projects/{project_id}", prefixed by app.api.routers.job.router
   ```
2. **Pass 3 (resolve):** each clue type's resolver reads the board and joins notes with the same label, producing `/api` + `/jobs` + `/projects/{project_id}`.

Notes from different files line up because **symbol resolution** gives the same object the same label: `router` in `job.py` and `job_router` imported in `__init__.py` are both `app.api.routers.job.router`.

This is why a new pack never has to be wired to existing ones: a pack that pins a `prefix` clue on a class or router automatically feeds every endpoint prefixed by it.

### Clue types are a fixed set, owned by Steno

Each clue type has exactly one built-in resolver that knows how to combine it. A pack author can **emit** these in YAML but can't define new ones; a new clue type means new resolver code (a plugin or a core change).

| Clue | Fields | Resolver | Used for |
|---|---|---|---|
| `prefix` | `owner`, `value` | **Prefix chain** | URL prefixes on routers, controllers, blueprints: FastAPI `APIRouter(prefix=…)`, Spring class-level `@RequestMapping` |
| `mount` | `parent`, `child`, `prefix?` | **Prefix chain** | One router mounted inside another: `include_router`, Express `app.use("/x", router)` |
| `property` | `key`, `value`, `profile?` | **Config** | Configuration values, for resolving `${placeholders}` and settings lookups. Steno emits these automatically for recognized config files; rules emit them only for unusual sources. |

More clue types arrive with the resolvers that need them, e.g. DI bindings with the DI resolver ([Ingestion §5](./ingestion.md#5-symbol-and-di-resolution)).

**If no clue is found,** nothing is added: a router without a prefix simply contributes no prefix, which is correct. If the label itself can't be worked out (the router came from a call symbol resolution couldn't follow), the fact is still recorded, marked lower-confidence, and listed in the coverage report.

## 5. Config rules (Decided)

Config files (YAML, `.properties`, `.env`) are matched by **key path** instead of a code pattern:

```yaml
id: spring-kafka-topic-declared
kind: config                              # code (default) | config
files: ["**/application*.yml", "**/application*.yaml", "**/application*.properties"]
match:
  key: "app.kafka.topics.{NAME}"          # dotted path; {NAME} captures one segment, ** matches many
emit:
  - node: [Interface, KafkaTopic]
    name: $VALUE                          # $KEY and $VALUE are always available
```

Most configuration needs **no rule**. Every key and value becomes a `property` clue automatically, so a code rule like `@KafkaListener(topics = "${app.kafka.topics.orders}")` gets the real topic name from the config resolver. Config rules are for when **the config alone states a fact**: a declared topic, a datasource URL (a `DataStore`), a downstream base URL.

## 6. Packs and layout (Decided)

Rules are published as **versioned packs**, grouped by **ecosystem** (the language plus its config files):

```
rule-packs/
├─ java/
│  ├─ steno-pack-spring-web/
│  ├─ steno-pack-spring-kafka/
│  └─ steno-pack-jpa/
├─ python/
│  └─ steno-pack-fastapi/
└─ org/                         org-specific packs, any language
```

Each pack:

```
steno-pack-fastapi/
├─ pack.yaml
├─ rules/
│  └─ fastapi-endpoint.yaml     one rule per file; the file name is the rule id
└─ tests/
   └─ fastapi-endpoint/         one folder per rule
      └─ nested-prefix/         one folder per case
         ├─ input/              small copies of real code
         └─ expected.yaml       the facts the case must produce
```

```yaml
# pack.yaml
name: steno-pack-fastapi
version: 0.1.0
language: python
source: core                    # core | org
description: FastAPI routers, endpoints, and dependencies
enabled_when:
  dependencies: [fastapi]       # read from requirements.txt, pyproject.toml, pom.xml, build.gradle, package.json
```

A pack is **auto-enabled** when the repository's build file lists one of `enabled_when.dependencies` (D25).

## 7. Tests (Decided)

**Every rule ships with test cases.** A case is a tiny input (a trimmed copy of real code, with no secrets) and the facts it must produce:

```yaml
# tests/fastapi-endpoint/nested-prefix/expected.yaml
nodes:
  - HttpEndpoint: { method: POST, path: "/api/jobs/projects/{project_id}" }
edges:
  - EXPOSES: { from: "@app", to: "POST /api/jobs/projects/{project_id}" }
entry_points:
  - "POST /api/jobs/projects/{project_id}" -> routers.job.run_project_jobs
```

- `steno rules test <pack>` runs every rule against its cases, through clue resolution, and shows any difference.
- Include at least one **negative case** (similar code that must *not* match) when a pattern could over-match.
- A case for a rule that depends on clues includes the other files it needs (here, the router's `__init__.py`), so the test covers the whole join.

## 8. Writing rules: by hand or with the skill (Decided)

The person who adds a rule owns it, whether they write the YAML by hand or use an AI assistant.

- **By hand:** read this doc, write the rule and its tests, run `steno rules test`.
- **With the `rule-pack-author` skill** (`.claude/skills/rule-pack-author/`): describe what you want recognized ("topics declared under `app.kafka.topics` in `application.yml` are Kafka topics"). The skill finds real examples in the code, maps them to existing node and edge types, writes the rule and its tests, checks how often the pattern matches, and **proposes** any new node, edge, or clue type instead of inventing one silently. Schema changes are design decisions.

This is different from Steno *generating* rules on its own from the coverage report (templates, LLM drafting), which stays deferred ([Ingestion §4](./ingestion.md#turning-a-coverage-item-into-a-rule-deferred-future-enhancement)).

## 9. Rules, resolvers, and plugins

| Mechanism | Covers | Written as |
|---|---|---|
| **Rules** | "See this pattern, emit this fact": most of what a framework does | YAML in a pack |
| **Built-in resolvers** | Joins many frameworks share: prefix chains, config placeholders, constants and f-strings, symbol and DI resolution | Steno core code |
| **Plugins** | Anything beyond both, such as an internal messaging framework with unusual routing. Rare. | Code, shipped with an org pack |
