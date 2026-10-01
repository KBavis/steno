"""Extractors: "when you see X, emit fact Y" (docs/ingestion.md §4).

- Code-pattern rules (ast-grep YAML) from the rule packs in /rule-packs
- Config path rules (YAML / properties)
- Code plugins for anything rules can't express

Extractors match file types and patterns, never connectors. Language specifics live
here and in the resolvers, never in the graph schema.
"""
