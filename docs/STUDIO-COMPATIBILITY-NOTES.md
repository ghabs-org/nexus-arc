# Studio compatibility notes — 23 October 2026

Studio code, configuration guides, generated authoring skills and catalog metadata
now use native Nexus terminology. This cleanup changes names and provenance;
it adds no external runtime dependency or new execution permission.

## Interfaces

Existing HTTP paths, payloads, release formats, workflow mapping languages,
provider bindings and resource identifiers remain supported. The interoperability
OpenAPI operation names are `convertPortableConfiguration` and
`exportPortableConfiguration`; regenerate clients that derive method names from
operation IDs. Their URLs and request/response schemas remain unchanged.

Python consumers use:

- `StudioPortableConfiguration` and `PortableConfigurationLoader` in
  `nexus.core.studio_interop_configuration`.
- `MissingPortableValueError` in `nexus.core.studio_interop_values`.
- `convert_template_settings` in `nexus.core.studio_nuclia_interop`.

The Nuclia example consumer uses `portable_template=True` and the CLI flag
`--portable-template`. Callers of renamed helpers or example flags must update
those names. No branded aliases are retained. The converter's policy checks,
fail-closed diagnostics, typed mappings and execution behavior remain unchanged.

Catalog `setup_metadata.source` identifies the Nexus implementation, with its
SHA-256 content digest in `source_revision`. That value is a local source digest,
not a Git commit. The legacy `catalog_commit` field stays present but empty when
there is no known native Git commit. Catalog inspection remains passive.

## State and evidence

No conversations, resources, queued runs, credential records or user files are
rewritten. Existing user-authored content is not scanned or edited. Third-party
package code and required license notices are not modified.

Local-only planning documents now have neutral filenames, including
`STUDIO-IMPLEMENTATION-PLAN.md`, `STUDIO-CONFIGURATION.md` and `STUDIO-AUDIT.json`.
Original comparison records remain in the sibling audit archive outside this
checkout. Neutral source aliases preserve recorded hashes and scope without
claiming that renamed aliases are original executable paths or remote addresses.
The archive index retains the original source addresses and file backups.

Previously exported authoring skills may now report stale content. Review and
regenerate them with `nexus ai-assistance skill install --force` in the intended
project or user scope.
