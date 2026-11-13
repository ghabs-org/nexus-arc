# Studio unsent draft recovery

Date: 2 November 2026. This is an approved addition to the original implementation
plan, not a replacement for its remaining device/account/recovery/desktop gates.

## Behavior

Studio saves one unsent conversation draft and one unsent Quick Build prompt on
this browser. Editing text, model/settings or selected files schedules a save
after 350ms; pagehide and hidden-page events flush pending edits. Draft recovery
is offered after reload rather than filling or submitting a composer automatically.
The compact saved indicator and Restore/Discard controls use existing theme,
corner and touch-target roles.

Choose **Restore draft** to recover text, available selections and the original
conversation/build context. Recovery makes only existing metadata/context GETs;
it never queues a run or generates a build. The composer must be empty first, so
restoring cannot overwrite newly typed text. Missing original context reports an
error and keeps the saved record. Missing provider/agent/project access blocks
restoration instead of silently choosing another resource. Unavailable attachment
IDs and optional settings are skipped with a persistent review notice.

Choose **Discard draft** to remove the browser copy. Discarding an active draft
also clears its text/file selection. Discarding an offered old draft preserves
any newly typed text and saves that new draft. Discarding does not remove uploaded
server files, saved builds, conversations or runs.

## Scope and stored fields

Draft keys include deployment mode, authenticated Nexus user (or the local desktop
owner), server workspace path and embedded-agent identity. Browser origin supplies
the address boundary. Recovery does not read another user's/workspace's key. A
known sign-in loss clears visible draft inputs and hides recovery until bootstrap
verifies the current identity again. Server-side resource authorization remains
unchanged and still validates reads and submitted attachment references.

Stored records contain only version/ID/time, literal prompt text, at most five
artifact IDs, context ID and allowlisted agent/project/provider/model/effort/tool
values. They collect no file bytes, credential fields or prompt-variable answers.
Text limits are 100,000 characters for chat and 16,000 for Quick Build; oversized
or unavailable storage reports an error without truncating or disabling the editor.

This is browser-profile `localStorage`, not encrypted credential storage or a
server backup. Prompt text can itself contain sensitive information supplied by
the user. Clearing site data removes drafts; another browser/address or a changed
desktop origin has separate storage. Drafts expire seven days after their last
save and are removed when their scope is next read. Malformed records are rejected.
The final debounce interval can be lost in a hard crash before a save/flush.

There is one latest draft per surface and scope, not a conversation draft history
or cross-device sync. Observed cross-tab version changes are checked before a
write, restore or deletion. A detected conflict preserves the other saved draft
and current text for explicit review. This is best-effort conflict detection;
localStorage is not a transactional multi-tab collaboration database.

## Submission lifecycle

Before a request is sent, its snapshot is marked unconfirmed. Confirmed queue
acknowledgement clears only that snapshot, before run polling. A later monitoring
failure never turns submitted work into an unsent draft. Quick Build ID assignment
also cannot recreate the already confirmed prompt on pagehide.

Without acknowledgement, the saved record remains and warns **Check Activity
before sending again**. Recovery never retries automatically. A newer draft from
another tab is not deleted by an older acknowledgement. Blocked/quota-limited
storage is best effort and cannot change the server's dispatch guarantees.

## Implementation and proof

- `nexus/studio_static/drafts.js` owns bounded storage and recovery controls.
  Existing chat/Quick Build handlers provide submit/confirm/fail/settle hooks;
  authentication loss locks recovery. Upload completion (including partial
  success), saved-prompt insertion and transcription notify draft listeners.
- Existing public static delivery serves the local script. Public shell v72
  includes it without caching drafts, APIs or account data. Existing Python wheel
  and future desktop bundling already include all static JavaScript. No native
  installer was built.
- `examples/nexus-bot/src/studio.py` consumes the shared UI and documents a manual
  reload/restore/discard check without sending work.
- All 309 frontend checks pass, including 20 focused draft checks. The 35 nearest
  backend/bundling checks pass. Both suites have zero failures/skips. Syntax,
  Ruff and whitespace checks pass; Ruff retains its existing settings warning.
- A real disposable browser verified immediate refresh recovery for chat and
  Quick Build, restored effort, explicit keyboard restore and persistent discard.
  Layout checks at 1280x720, 390x844 and 360x780 fit horizontally in Light/Dark;
  visible mobile draft actions are 44px high. The console had no errors. The
  final model-input handling also has Node contract coverage.
- The owned preview stopped cleanly with zero runs. It used no personal workspace,
  provider or microphone. Temporary screenshots/reports are path/hash references
  in the [checkpoint](STUDIO-DRAFT-RECOVERY-CHECKPOINT.json).

No VM, new dependency, authentication service, scheduling service, release upload
or DMG operation occurred. Physical-device/crash hardware testing remains separate
from reload/event and browser-width evidence. Workflow undo/redo and notifications
were suggestions, not part of this approved draft-recovery slice.
