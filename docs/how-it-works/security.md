# Security and privacy

What Steno holds, what leaves the organization, and who may see what. Part of [How it works](./README.md).

> **In one minute**
> - Steno stores **no file contents**, but its facts, citations, and cards describe the code, and the MCP server holds git credentials to read it. Both need the same access controls as the repositories.
> - **Secret values are never ingested.** Credentials live in a secret manager; Steno stores references.
> - **Data leaving the organization** (to an LLM for cards, to a decision model later) must be confirmed against the organization's policy first, and kept to the minimum.
> - **MCP authentication and scoping by role** are `v1` requirements before any client deployment.

| Area | Position | Status |
|---|---|---|
| **Source code** | No file contents stored. Facts, citations, and cards describe the code; the MCP server reads code from the git host at the ingested commit. Treat Steno's data with the same access controls as the repositories. | Decided |
| **Secrets** | Never ingested. Config keys that look like secrets (`password`, `secret`, `token`, `credential`, `private-key`, `api-key`, …) are skipped by every config rule. A value from a vault is recorded as an unresolved reference. Connector credentials are references into a secret manager. | Decided (built) |
| **Deployment values** | When admins supply runtime values (Proposed), URLs are stripped of `user:password@`, credential-like keys are refused, and values never reach an LLM. | Proposed |
| **Data leaving the organization** | The LLM receives flow context, including bounded code snippets, when writing cards. A decision model (later) receives digests: names, summaries, interactions, rarely code. **Confirm the organization's policy before any LLM spend,** and send only the minimum. A local model is possible through LiteLLM. | Decided (policy check), **Open** (our organization's answer) |
| **Clone tokens** | Today a token can appear on the git command line. Direction: git's credential helper. | Open |
| **Credentials for many systems** (`v1`) | Each API connector is read-only, uses the narrowest scope, references a secret manager, and gets a security review per connector type. | Decided (direction) |
| **MCP access control** | Authentication (OAuth over Streamable HTTP) and scoping results by space or role (a contractor sees less than an employee). | **Open**, `v1` |
| **Auditing** | Tool calls (`tool_call`), decisions (`jev_decision`), and LLM generations (`llm_call`, `llm_output`) are logged. | Decided (tool calls built) |
| **Organization repositories** | Steno never asks an organization to change its repositories; it's read-only against git. | Decided |
| **Deployment** | One Steno deployment per organization (D34). Where it runs (the client's cloud, on premises, air-gapped) is **Open** and a first question for any client. | Open |
