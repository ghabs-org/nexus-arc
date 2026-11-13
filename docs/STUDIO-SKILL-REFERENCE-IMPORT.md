# Explicit skill references in configuration imports

Declarative agent imports now accept `spec.skillRefs` alongside routing `skills`
and older `additionalConfigurations.skills` references. Routing descriptions stay
in the agent card; knowledge instructions come only from explicitly bound native
skill resources.

```yaml
kind: agent
name: reviewer
spec:
  systemPrompt: Review the supplied evidence.
  modelProvider: [subscription]
  skillRefs: [review-guidance]
```

Supply bindings through the existing owner-only conversion interface:

```json
{
  "models": {"subscription": {"provider": "codex"}},
  "skills": {"review-guidance": "existing-nexus-skill-id"}
}
```

The native skill must exist and be unarchived. Conversion resolves aliases only:
it does not fetch a URL, open a skill path, install software or invoke a provider.
Unbound names, invalid shapes, archived resources and unsupported fields reject
the entire executable document. Conversion does not create a plan or save agents.

Up to twenty aliases are accepted. Existing knowledge references come first;
`skillRefs` adds an ordered, deduplicated union, limited to twenty distinct skill
resources. Routing descriptions never become execution permissions. Import/export
preserves original source fields, including an empty declared `skillRefs` list.

Review the converted document and use the existing configuration validate/plan/apply
lifecycle. External deployment flags remain rejected; conversion does not silently
activate resources. Runtime skill resolution uses existing native agent snapshots,
so later skill edits cannot change already queued instructions.

This is a bounded import extension. It does not implement current connector-node
syntax, workflow-level toolsets, dataset sibling-file resolution or a whole-bundle
conversion fixture. Those remain separate comparison work. It also does not
reproduce an external runtime's dynamic skill-loading or knowledge-search tools;
the explicitly selected native skill supplies instructions through Nexus context.
No state migration or optional dependency is introduced.

Example: `examples/nexus-bot/src/studio_interop_skill_refs.py`.
API contract: `postman/contracts/studio-interop-configuration.yaml`.
