# Rule packs

Versioned bundles of extractor rules, one per framework or org (D25, [Ingestion §4](../docs/ingestion.md#4-extractors-rules-for-being-agnostic)).

```
steno-pack-<name>/
├─ pack.yaml      name, version, source (core | org), language, enabled_when
└─ rules/         one YAML file per rule: "when you see X, emit fact Y"
```

- A pack is auto-enabled when the repository's build file has a dependency listed in `enabled_when`.
- Phase 1 rules are written by hand. The coverage report after each ingestion says which rules to write next.
- Org-specific packs (e.g. `yourorg-internal`) live alongside these and follow the same layout.
