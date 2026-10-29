# Evaluation input and grading-reference files

Dataset and evaluation case editors now have two optional, collapsible file roles:

- **Input files:** extracted text joins task input sent to the agent or workflow.
- **Grading-reference files:** extracted text goes only to the explicitly configured AI judge.

Upload files through Library → Artifacts, then choose a file revision in the case
editor. Configure a judge manually or apply a saved evaluator when using reference
files. Save the dataset or suite separately. Saving and choosing files do not run
models. Running uses the selected target and judge accounts.

Choose up to five files per role. The same artifact cannot fill both roles in a
single case. Each file must contain nonempty text of at most 16000 characters.
UTF-8 text, PDF and Office extraction use the existing document service; missing
PDF dependencies, images/audio, unreadable content and oversized files reject
queuing. This feature uses extracted text, not original multimodal file input.
A whole run's copied file snapshot is limited to 900 KiB; large suites should use
smaller files or fewer cases. Contents are never silently truncated.

Before queuing, Studio checks current artifact access and historical revision
access, then copies selected text and source metadata into the private job payload.
Editing or archiving files afterward does not alter queued copies. New runs cannot
use archived files. Shared datasets/suites do not grant access to private artifacts.
Raw server paths and external URLs are not accepted. Caller-supplied file snapshots
are discarded. File-role metadata survives JSON/YAML/CSV dataset import, suite
copying and catalog history. CSV role columns contain JSON reference lists.

Reports list pinned filenames, IDs, revisions and SHA-256 digests by role. Source
reference text is not copied into report metadata; model answers/reasons may quote
supplied material, so reports and exports remain private.

No data migration is needed: cases without files retain their behavior. Use the
updated runtime for file-bearing suites; older runtimes do not implement these
optional fields. Existing evaluation checkpoints retain copied file text for
continuation. This slice verified serialized payload reuse after source archival,
not a new deployed-worker recovery drill.

Local example:
`PYTHONPATH=. python examples/nexus-bot/src/studio_evaluation_files.py`

The example injects synthetic target/judge responses. No API account is contacted.
See `postman/contracts/studio-datasets.md` for the additive contract and
`STUDIO-EVALUATION-FILES-CHECKPOINT.json` for scoped acceptance evidence.
