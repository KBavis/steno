# Steno

Steno is a company-agnostic system for capturing an organization's software architecture as a layered, evolving knowledge graph, rather than a static, one-time diagram that goes stale the day it's drawn.

## The goal

At a high level:

- **Initial ingestion**: build a snapshot of what currently exists, at a point in time (T0), automatically derived where possible (repos, services, integration points, data stores) and organized into layers of abstraction (org → space → repo → data store).
- **Continuous refinement**: after T0, the graph is updated by discrete, attributable deltas, changes to the architecture tied to the body of work that caused them and the reasoning behind it, rather than by re-ingesting everything from scratch.
- **Navigable, not just stored**: the graph is meant to be queried layer by layer (by a person or by an agent) rather than dumped in full, since no single context window can hold an entire organization's architecture at once.
- **Company-agnostic**: the ingestion and schema are designed to work for any organization's codebase and tooling, not tied to one company's internal systems.

---

## Why "Steno"

Steno is named after [Nicolas Steno](https://en.wikipedia.org/wiki/Nicolas_Steno), the 17th-century founder of stratigraphy. Steno formulated the law of superposition: rock forms in layers over time, the oldest at the bottom and the newest on top, and that history can be read back out of the layers themselves just by observing how they're stacked.

That's close to what this project does. An organization's software architecture is a set of layers (org, space, repository, data store) that accumulate over time. Each unit of work adds a new layer on top of what existed before, and the *reason* for that layer is worth keeping alongside the structure it produced. Steno's job is to hold that stack: an initial snapshot of "what's there," plus every layer added after it, each with its own provenance and its own why.

