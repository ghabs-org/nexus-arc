# Authoring skills for Codex and Claude Code

Nexus generates two offline skills for external coding assistants:

- `nexus-studio`: workspace files, storage locations, providers, projects and run inspection.
- `nexus-studio-config`: native configuration releases, a validated agent example,
  complete preflight, reviewed plans and supported external conversion.

Command references come from the installed CLI parser. Configuration guides list
current resource collections, providers and administrator workflow defaults. No
running Studio, network access, model account or optional dependency is required.
These are authoring skills, distinct from deploying a catalog skill to an agent.

## Install

Run from the project where the assistant should discover the skills:

```sh
nexus ai-assistance skill install
nexus ai-assistance skill install --target claude
```

The default `agents` target writes `.agents/skills/`; `codex` is an alias.
`claude` writes `.claude/skills/`. Add `--scope user` to install under your home
directory instead of the current project. Installation is explicit; starting
Studio or browsing its catalog never changes assistant configuration.

For an explicit directory, use `--to` without `--target` or `--scope`:

```sh
nexus ai-assistance skill install --to ./nexus-authoring-skills
```

Existing copies are preserved unless `--force` is provided. Both generated skills
are checked and staged before replacement. Custom extra files and symbolic links
are rejected even with `--force`. A failed replacement restores prior copies.
If rollback itself fails, the command reports the staging directory containing
recoverable originals instead of deleting it. An installation interrupted by
process termination can leave `.nexus-authoring.lock` and a staging directory;
inspect and recover these before removing the lock and retrying. Filesystem
permissions are private on POSIX; inherited Windows ACLs still apply.

## Export and check

Export produces the same portable directories without installing them into a
project or user assistant folder:

```sh
nexus ai-assistance skill export --to ./nexus-skill-bundle
nexus ai-assistance skill check --path ./nexus-skill-bundle
```

Without `--path`, check examines existing Nexus copies in the current project's
and user's `.agents/skills/` and `.claude/skills/`. It exits nonzero when no copies
exist or any package is missing, incomplete, altered, unsafe or stale. Markers
record the Nexus version and file hashes; checking compares every generated
file with this framework installation. After upgrading Nexus, use a reviewed
`install --force` to regenerate the guides.

All operations print JSON. Check exits 1 on mismatch; invalid arguments or failed
installation exit 2. A custom destination and target/scope cannot be combined.

Exports contain framework guidance only. They do not include workspace content,
catalog skill text, conversation history, credentials or resolved environment
values. Installing them does not authorize resource activation or external actions.
The guides use Nexus command names and configuration formats. Agent and workflow
conversion remains bounded by the existing interoperability validators.

## Acceptance and compatibility

Milestone 11 is locally accepted through deterministic bundle generation, native
configuration preflight, real CLI export/install/check, both assistant discovery
paths, scope selection, stale/missing/tampered detection, overwrite refusal,
symlink rejection and rollback fault tests. The example consumer is
`examples/nexus-bot/src/studio_authoring_skills.py`.

No persisted Studio records or legacy commands change. Assistant applications
can discover the generated `SKILL.md` folders through their normal skill loading;
loading them in a particular installed assistant version remains a manual live
check. Physical Windows/Linux checks remain separate from platform-neutral path
and file-format tests.

Authoring guidance is generated from the installed Nexus framework.
