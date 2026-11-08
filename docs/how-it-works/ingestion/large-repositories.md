# Large repositories

How a very large repository is ingested within memory and time. Built unless marked otherwise (D63, D127, D128, D134, D135).

> **In one minute**
> - **The goal:** cost grows with chunks, not with the repository. Every fix kept the graph identical.
> - **Memory:** two-pass Java resolution, output indexed on disk, one syntax tree at a time, compact traces, small write transactions.
> - **Time:** repeated work in flows is done once. On ThingsBoard, flows went from 2,459 s to 125 s.
> - **Open:** an organization's run was stopped after more than 8 hours in the flows stage. The cause isn't known (§3c).

```mermaid
flowchart LR
    C["clone"] --> P1["Pass 1: declarations"]
    P1 --> P2["Pass 2: bodies in batches"]
    P2 --> D[("Disk index")]
    D --> X["extract, assemble"]
    X --> F["flows: traces compacted"]
    F --> W["write in chunks"]
    M["free memory"] -. "claim only what fits" .-> C
```

## 1. Benchmarks

| Repository | Size | Latest result |
|---|---|---|
| ThingsBoard | About 5,500 Java files | parse 120 s, extract 74 s, flows 125 s, Python peak 839 MB; 116,395 nodes, 246,687 edges |
| Keycloak | 5,772 Java files | Resolves within a 600 MB heap; whole job about 3 minutes; 0 flows, because no pack covers Quarkus and JAX-RS |
| Elasticsearch | 21,229 Java files | Drove the second round of memory work; no complete run is recorded |

## 2. Two-pass Java resolution (D127)

Resolving a call needs other files' **declarations**, never their **bodies**.

1. **Declaration index.** Parse every file and empty each method body. Constructor bodies stay, because they can assign fields.
2. **Bodies in batches** (200 files by default). Put bodies back, resolve, drop them, and clear caches.

**Proven equal:** `steno resolver-check <folder>` compares both ways, call site by call site.

**Tokens are released after parsing,** which cut Keycloak's live heap from 1,791 MB to 473 MB. Anonymous classes get stable names.

**On:** above `STENO_RESOLVER_JVM_BATCH_ABOVE_FILES` (3,000) Java files, or after a run ran out of memory.

## 3. The resolver's output, on disk (D127)

The helper writes JSON Lines, and Python indexes them into SQLite (`resolvers/disk_index.py`) instead of loading them whole. Elasticsearch's 2 GB of output would otherwise have taken about 9 GB.

The index lives under `STENO_WORKSPACE_DIR/indexes/`. `steno job recover` cleans up after a killed worker.

## 3a. Extraction, a file at a time

Each file's syntax tree is built, used by every rule, and dropped. Facts come out in the same order as before.

## 3b. A module that can't be resolved (D127)

If the whole repository still fails, parse tries one build module at a time. A failed module is a gap (`modules_failed`, counted as `unparsed`). Calls between modules count as unresolved. The job fails only if no module resolves.

## 3c. Flows: work done once

**Round 1 (built, 2026-10-31).** On ThingsBoard, flows took 41 of a job's 46 minutes. Paths per file, a function's gaps, and "does this reach an effect?" are now worked out once.

| ThingsBoard, same graph | Before | After |
|---|---|---|
| parse | 193 s | 120 s |
| extract | 119 s | 74 s |
| flows | 2,459 s | 125 s |
| Python's peak | 1,644 MB | 839 MB |

**The open problem.** A run at an organization's repository was stopped after more than 8 hours in the flows stage. A stopped stage records nothing, and ThingsBoard doesn't reproduce it, so **the cause isn't known.**

**Round 2 (`fix/flows-scale`, not merged).** Profiling ThingsBoard found work growing faster than the repository:

- Which functions a framework calls was rebuilt per inferred flow: flows × hooks × the harmless list, 68 million checks. It grows with the square of the repository.
- Digests held every decoded trace in memory at once.
- Fixed fields were recomputed per trace entry.

That branch took the whole build from 74.7 s to 54.6 s and Python's peak from 1,380 MB to 844 MB, and adds per-minute progress logs, `timings`, and `slowest_flows`.

**What's left grows with trace entries:** about 5 µs each, and each flow walks everything it reaches again. So a repository where flows reach most of its code costs **flows × reach**. Whether traces stay is the ablation's question (D140).

**The helper's time limit** grows with the repository: `STENO_RESOLVER_JVM_TIMEOUT` (1,800 s) plus `STENO_RESOLVER_JVM_TIMEOUT_PER_FILE` (0.25 s) per file.

## 4. Compact traces (D63)

A trace lists each function once per call site, so large flows repeat functions thousands of times. Each trace is now a table of functions plus one short row per call site, compressed to bytes on the `Flow` node (`graph/trace.py`). ThingsBoard's traces went from 2.5 GB to 61 MB.

## 5. Failures and fitting the machine (D128, D134)

- **A resolver that fails as a whole fails the job.** The last good graph stays.
- **A job starts only if its reservation fits free memory,** less `STENO_MEMORY_HEADROOM_MB` (768), even when alone.
- **A reservation** is what the repository needed last time plus 25%, or 3 GB on a first run.
- **If the plan doesn't fit,** the heap is cut and batches are used, never below what the repository is known to need.
- **If nothing fits,** the job waits and says why. A job bigger than the machine's budget fails at once.
- **After out of memory,** parse retries once in the same job with twice the heap, bounded by free memory.

| Setting | Default | Means |
|---|---|---|
| `STENO_WORKER_MEMORY_FRACTION` | 0.8 | Share of memory jobs may reserve |
| `STENO_JOB_MEMORY_DEFAULT_MB` | 3072 | A never-measured repository's reservation |
| `STENO_WORKER_PROCESSES` | One per CPU, up to 8 | Worker processes |
| `STENO_MAX_PARALLEL_JOBS` | 16 | Ceiling across machines |
| `STENO_RESOLVER_JVM_MAX_HEAP` | 2g | Heap with no history |
| `STENO_RESOLVER_JVM_BATCH_FILES` | 0 | Files per batch |
| `STENO_MEMORY_WAIT_MINUTES` | 30 | Java step's wait for memory |

## 6. What's measured on every run

| Where | Metric |
|---|---|
| Every stage | `rss_peak_mb` |
| Parse | `heap_live_peak_mb`, `heap_limit_mb`, `near_limit`, `batches`, `files`, `functions` |
| Flows | `trace_entries` |
| Job | `stats.resources`, `host`, `memory_reserved_mb` |

## 7. Not built yet

- **Flows per application** in a monorepo, so the call graph is never held for the whole repository (D127).
- **Eclipse JDT,** only if the helper fails a larger benchmark (Open).

## 8a. Writing the graph to Neo4j in chunks (D135)

**Why:** Neo4j must hold a whole transaction in memory. ThingsBoard needed about 3.5 GiB, ten times its properties. With no limit set, an oversized write crashed a 3.1 GB heap.

**How:** see [Stages §7](./stages.md#7-write). A flow is the unit (median 207 KB), because one ThingsBoard application holds 99.7% of its flow bytes, so splitting by module wouldn't work.

**Measured:** a ThingsBoard-size graph wrote in 42 s and 590 transactions into a **1 GiB heap**. A failed batch left the old graph intact, and the next run wrote exactly what was missing.

**Sizing:** `make up` gives local Neo4j 25% of memory (1–4 GB). Keep the default transaction limit: it turns an oversized batch into a refused one, which Steno halves.

**Still proportional to the repository (Open):** the comparison reads every node's properties into Python (61 s, even when nothing changed), and traces are 59% of the properties.
