"""The LLM only writes text: flow purposes and narratives, app/space/org cards.

Every call is logged in `llm_call` and its text cached in `llm_output` by input hash.
In a dry run, nothing is generated: inputs are token-counted and cost is projected.
The model is still Open (DESIGN_DOC §21), decided from the dry run's numbers.
"""
