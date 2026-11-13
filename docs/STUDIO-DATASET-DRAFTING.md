# Inline dataset drafting

In Library → Datasets, create or edit a dataset and expand **Draft cases with AI**.
The same controls appear in an evaluation suite's case editor.

1. Describe behavior and expected answers. Select provider, optional model and 1–100 cases.
2. Select **Generate draft cases**. This uses the selected account; existing cases stay intact.
3. Review the generated JSON, especially expected answers. Model suggestions can be wrong.
4. Choose **Append reviewed cases** or **Replace all cases**, then save normally.

Only the supplied request is sent automatically. Existing cases, server files and
workspace agent names are not included. Drafts are private Activity runs; saved
datasets follow existing workspace sharing. Generation never saves resources,
configures judges, activates anything or executes evaluation cases.

Appending preserves existing form controls and rejects duplicate inputs or more
than 100 total cases. Invalid JSON, unsupported checks and provider failures leave
existing cases intact. Closing the editor ignores late generation results; the
run remains inspectable in Activity. Generated drafts use the existing isolated
designer and provider routing, with shared worker tools disabled.

Framework example: `examples/nexus-bot/src/studio_dataset_drafting.py`.
HTTP contract: `postman/contracts/studio-datasets.md`.
No state migration or optional dependency is introduced.
