"""The LLM only writes text: summaries for applications, spaces, and the organization (D143).
Flows get a deterministic digest instead (`steno.graph.digest`).

Every call is logged in `llm_call` and its text cached in `llm_output` by input hash.
In a dry run, nothing is generated: inputs are token-counted and cost is projected.
The model is still Open (docs/plan/decisions.md, open questions), decided from the dry
run's numbers.
"""
