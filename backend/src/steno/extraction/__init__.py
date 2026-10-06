"""Extraction: the rule engine runs rule packs over a repository (docs/ingestion.md §4).

- Code-pattern rules (ast-grep YAML) from the rule packs in /rule-packs
- Config path rules (YAML / properties)
- Code plugins for anything rules can't express (not built yet)

Rules match file types and patterns, never connectors. Language specifics live
here and in the symbol resolvers, never in the graph schema.
"""
