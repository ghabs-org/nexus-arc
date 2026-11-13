# Studio release history

The source of truth is [the bundled release catalog](../nexus/studio_static/release-notes.json).
Open **Release notes** in Studio's menu, or ask Studio assistant “Which features are new?”
or “What changed in Studio 1.0.5?”. **Find local answer** uses the same catalog without
a provider call. The assistant's system instructions include a bounded summary and its
existing `studio_help` tool returns exact version notes and source links.

Notes ship as package data in Python installations, VM images and desktop backends.
They do not depend on a Git checkout, ignored release folders, network access or a
knowledge-project upload. Rebuilding/deploying Studio is required to update an existing
installation's bundled history; this source change does not modify old signed artifacts.

The AI path uses the selected provider and can take time even when notes are
available locally. A routing fallback notice describes model selection, not the
availability of release notes. Use **Find local answer** for an immediate lookup
without model usage; see [router deadlines and statuses](STUDIO-ROUTER.md).

## Historical reconstruction

Entries 1.0.0–1.0.6 were reconstructed from Git history, checked-in verification
checkpoints and retained local artifact records on 9 November 2026. Evidence paths
are recorded with each entry. Local artifact references identify historical evidence;
the artifact itself is not required to read the bundled notes.

- 1.0.0 records a beta checkpoint, not a verified public release date.
- 1.0.1 is explicitly unverified: it appears as synthetic update-test metadata.
- 1.0.2, 1.0.3 and 1.0.5 have recorded signed local DMG releases.
- 1.0.4 was a local application install; no DMG was created for that version.
- 1.0.6 has a local notarization record and versioned source history, but its release
  date and complete frozen build contents are not established by that record.

Unreleased work is separate. `source_version` describes source, **not** the running
server, installed desktop version, public distribution or update-feed availability.
The assistant must retain these qualifications. Unknown versions return an explicit
absence of notes rather than borrowing another version's features.

## Every future version

1. Add user-facing changes to **Unreleased** as they land. Separate actual shipped
   behavior from plans, deployment repairs and account-dependent validation.
2. Before building a version, add its entry with a title, changes, limitations,
   evidence and distribution status. Move only changes contained in that build.
   Set the date only when supported by the release record; otherwise use `null`.
3. Update `source_version` with the desktop version. Run the release-notes tests and
   the applicable release gates. The desktop build consumer and macOS signing
   pipeline reject versions without substantive notes. The default updater text
   comes from that version's entry, including its limitations.
4. After verification, update the evidence/status. Do not silently alter an old
   signed app or imply that source changes upgraded a hosted instance.

The framework helpers live in `nexus.core.studio_release_notes`. The existing
`examples/nexus-bot/src/studio_workspace_access.py` consumer exercises version lookup.
