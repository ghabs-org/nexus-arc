# Original file attachments

Use **Attach files → Upload files** in Conversations, or **Add context → Upload
files** in Quick Build. Existing Library files can also be selected. Uploading
does not send anything to a model; files are included only when you submit.

Select up to five files, at most 16 MiB each and 32 MiB combined. Originals stay in
the private artifact library. A queued run pins each selected revision, so later
edits cannot silently replace its source.

Local CLI agents receive the original bytes in a private temporary folder, with
file links and paths in their prompt. Text is no longer pasted into CLI prompts.
The temporary copies remain available throughout streaming and tool rounds and
are removed when the invocation finishes, fails or is cancelled. The saved
originals remain available. A later run creates fresh temporary copies.

Shared workers receive only the selected files through a read-only mount. No
additional project/state directory is exposed. Claude's worker Read permission is
scoped to that attachment directory. Normal model capability and tool limits
still apply; making a file accessible does not guarantee every model understands
every file format.

API models cannot read local filesystem links. UTF-8 text (128 KiB combined) and
PDF extraction retain their existing API path; OpenAI can receive original image
and PDF parts. Unsupported binary API inputs produce a clear error. No fallback
provider is selected automatically. Your chosen provider's billing still applies.

On phones, the header shows labelled Help and Install icons with 44-pixel touch
targets. The page title remains intact; the redundant Workspace breadcrumb is
hidden. Desktop retains text labels. The menu still includes Interactive guide.

No data migration is required. Existing queued requests remain readable. Restart
Studio to load backend changes, then refresh the page to receive the updated
interface. The public application-shell cache version is updated; private API
responses and attachment downloads remain uncached.

The disposable consumer
`examples/nexus-bot/src/studio_attachment_files.py` demonstrates pinned originals,
temporary file links and cleanup without invoking a model.
