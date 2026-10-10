# Connectors and sources

What Steno reads, how it reaches it, and how repositories are chosen and placed into spaces.

> **In one minute**
> - A **connector** says where a source is and how to reach it. It fetches; it doesn't interpret.
> - A git connector is a host plus a scope (a Bitbucket project, a GitHub organization). **Which repositories** to ingest and **which space** each belongs to are separate choices.
> - Anything stored in a repository (code, config, build files, manifests, the XLD repo, Helm values) comes through the **git connector**. New connectors are needed only for systems reached through an API (`v1`).
> - Ingestion **clones** into a temporary workspace. Queries never clone: code tools read the git host at the ingested commit.

---

## 1. Connectors (Decided)

**A connector is a source system plus a scope, not a single repository:** a Bitbucket workspace or project, a GitHub organization, a GitLab group, with a reference to its credentials. Credentials live in a secret manager, never in Steno's database.

Three separate questions, kept apart so one doesn't have to answer the others (D39):

| Concern | Question | Where it lives |
|---|---|---|
| **Access** | What can Steno see on this host? | The connector: base URL, credentials, scope |
| **Selection** | Which repositories get ingested? | The repository list: explicit in the POC, discovery later |
| **Placement** | Which space does a repository belong to? | The repository's space |

A connector's scope never has to be narrowed to one repository: testing on one repository means selecting one repository under a broad connector.

| Phase | Repository selection |
|---|---|
| `POC` | An **explicit include list**, one repository at a time |
| `v1` | **Discovery**: list every repository in the connector's scope, filtered by include/exclude patterns, archived status, and recent activity |

### Placement rules (Decided; built with discovery)

Placement rules map the host's own structure onto spaces, so an organization with thousands of repositories doesn't place each by hand:

- A rule is a connector, a host grouping (Bitbucket project, GitLab group, GitHub organization), and an optional repository-name pattern, pointing at a space: "connector `bitbucket`, project `PAY` → Payroll", "`pay-*` → Payroll". Rules are evaluated in order.
- **An explicit assignment on a repository always wins.**
- Discovered repositories no rule matches go to an **unassigned** queue for an admin.
- Placement is the Structure facet: ADMIN first, then declared sources such as a catalog's domain field. Spaces are always admin-defined (D27).

## 2. Witnesses and connectors

A connector answers *where is it stored*. That's separate from *how it's read* (a rule) and *what kind of claim it is* (the witness type). See [Witnesses and facets](../../concepts/witnesses-and-facets.md#1-three-separate-questions-about-any-piece-of-information).

| Source | Connector | Witness |
|---|---|---|
| Application source, its config, its build files | Git | CODE |
| Config repositories: Helm values, Kubernetes manifests, gateway routes, CronJobs | Git | PLATFORM |
| The XLD repository (topics, ACLs, deployables) | Git | PLATFORM |
| `component_manifest.yaml`, `catalog-info.yaml`, CODEOWNERS | Git | DECLARED |
| XLD server, Kafka admin API, artifact repository, scheduler, catalog API | **A new connector** (`v1`) | PLATFORM or DECLARED |

**Connector kinds over time:**

| Kind | Examples | Scope |
|---|---|---|
| Git host | Bitbucket, GitHub, GitLab | `POC` |
| Deploy and infrastructure systems (API) | XLD server, Kafka admin API, Kubernetes | `v1` |
| Artifact repository | JFrog Artifactory, Nexus | `v1` |
| Scheduler | Control-M, Airflow | `v1` |
| Catalog | Backstage, Cortex | `v1` |
| Data store catalog (read-only) | Postgres, Oracle, Mongo | `v2` (Data V2) |
| Logs and traces | Splunk, OpenTelemetry | `v2` (OBSERVED) |

**How an API connector works (`v1`):** the connector holds an address and a credentials reference; an adapter pulls a **snapshot** on its own schedule, skips it if unchanged, turns it into claims, and stores them in Postgres with an as-of time. When a system also keeps its configuration in git, prefer the git copy: it's versioned and citeable to a line.

## 3. Cloning (Decided)

Ingestion workers clone into a temporary workspace and delete it after the run. Why clone instead of fetching files through the API:

- Symbol resolution needs the full source tree (and, later, dependency JARs).
- Fetching thousands of files hits git host rate limits. A clone is one request.
- `git diff` between commits is local, exact, and detects renames.
- **Optional:** a cache of bare clones turns the next run into a `git fetch`; very large repositories can use a partial clone (`--filter=blob:none`). An optimization only.

Today: a shallow clone of the default branch; the commit SHA is stamped onto every fact. Tokens come through `env:` references (direction: git's credential helper).

**Queries never clone.** Steno stores no file contents; code tools read the git host API at the ingested commit ([Retrieval §4](../retrieval-and-mcp.md#4-code-access-decided-no-stored-file-contents)).

## 4. What a config file produces

Config files become facts in the graph, plus a property map for the symbol resolvers.

| Config value | Becomes |
|---|---|
| Kafka topic names, consumer groups | `KafkaTopic` nodes, `CONSUMES` / `PRODUCES` properties |
| Kafka bootstrap servers | `KafkaCluster` identity (`HOSTED_ON`) |
| Downstream base URLs (`services.billing.url`) | A host for `CALLS` targets, then an application or `ExternalSystem` |
| Datasource URLs | `DataStore` identity: host, database, vendor |
| Cron expressions, scheduling config | `Schedule` properties |
| Every property, per profile | `property` clues for `${placeholder}` resolution, re-read every job (nothing stored) |
| Secrets | **Never read.** Keys that look like secrets are skipped by every config rule. |

**Deployment values (Proposed, later).** Admins can supply the values code reads at runtime (`SYNC_REL_DB_URL`), per repository or application, so stores resolve to their real host and applications sharing a database share one `DataStore`. Order: admin values → committed config (`.env.sample`, compose, `application.yml`) → code defaults. Credentials are never kept: URLs are stripped of `user:password@`, credential-like keys are refused, values never reach an LLM. Read-only connectors to deployment config (Helm values, ConfigMaps, Terraform outputs) can supply them later; this is the Routing facet's PLATFORM provider.

## 5. What admins declare

| Declared | Where | Projected into Neo4j |
|---|---|---|
| The organization (name, description) | Onboarding, `organization` (one row) | Yes |
| Spaces and their nesting | Onboarding, `space` | Yes |
| Connectors, repositories, placement | Onboarding | Repository → Space at ingestion |
| Aliases, renames, overrides (planned) | Admin UI → identity registry | Through identify |
| Glossary terms | `glossary.yaml` per space, or the UI later | Yes |

### Glossary (Decided: human-declared)

Organization vocabulary often appears nowhere in the code ("Swing" is a nickname for a specific application), so people declare it:

```yaml
# glossary.yaml, one per space (or entered in the UI later)
Swing: app/pay-adjustment-svc
PEO: "Professional Employer Organization"; entity/PeoClient
```

- A declared term that points at a node is used by search to expand questions into graph nodes.
- Code-derived suggestions (repository, module, and package names without a declared term) can be shown to admins as prompts, never added automatically.
- Deriving terms from documentation comes with the Contextualized integration.
- **Open:** the declaration format (`glossary.yaml` per space, or the UI).
