# Response feedback and evaluation review

Rate a completed conversation response using **Helpful** or **Needs improvement**.
The dialog offers a rating and optional multiline comment. Reopening a rating
loads the current saved comment. Each account has one feedback record per run;
changing its rating updates that record instead of adding another entry.

Open **Response feedback** to review your history:

- **View response** opens existing task details and its recorded execution history.
- **Edit feedback** changes the rating/comment using the current revision.
- **Export feedback** retrieves current account records and downloads
  `response-feedback.json` in `nexus-response-feedback/v1` format.
- **Create evaluation case** copies the original task and your expected phrase
  into a saved evaluation. Optional source context is copied only when explicitly
  selected, using its pinned artifact revision. Private comments are not copied.
  The receipt opens the existing evaluation editor for review; nothing runs yet.

Comments stay private to their feedback owner. Workspace administrators do not
receive another account's feedback history. Shared-mode actions retain existing
Nexus permissions: operators can rate/edit accessible completed runs; creating an
evaluation requires builder/owner permission and ownership of that feedback.
History disables unavailable editing/promotion controls. Saved evaluations follow
the workspace's existing resource permissions.

The server rejects stale revisions instead of overwriting a newer edit. If another
tab changed the feedback, reopen the latest record before saving again. Network
failures never cause an automatic write retry. API routes and persistent record
formats are unchanged; this update uses revision checks that already existed.

## Local acceptance

Run the focused regression suite:

```sh
node --test tests/studio_feedback.test.cjs tests/studio_sharing.test.cjs tests/studio_evaluation_context.test.cjs
python -m pytest -o addopts='' tests/test_studio_feedback.py tests/test_studio_retrieval_evidence.py tests/test_studio_parity_features.py
PYTHONPATH=. python examples/nexus-bot/src/studio_conversations.py
```

For the full Studio browser flow:

```sh
PYTHONPATH=. python tests/studio_feedback_acceptance.py
```

Open the printed `/acceptance` link and sign in as Alice. Skip the optional guide,
then open **Feedback acceptance**. The fixture contains a previously rated cited
response, an unrated response, private Bob feedback and a saved target agent.
All data is synthetic and lives in a temporary workspace. No provider is contacted.

Acceptance sequence:

1. Rate the unrated response as **Needs improvement** with
   `Please include sources.` and `Keep the concise answer.` on separate lines.
2. Reopen it from **Helpful** and confirm that its comment is retained; cancel.
3. In **Response feedback**, edit it to **Helpful** with
   `Clear and useful.` and `Keep sources visible.` on separate lines.
4. Export history. Confirm two Alice records, the updated comment/revision and no
   Bob feedback. Sign in as Bob and confirm that only his own record appears;
   promotion is disabled for his operator role. **View response** must show his
   existing output without starting a task.
5. As Alice, promote the cited response to **Returns feedback regression**, target
   **Feedback reviewer**, expected phrase `thirty days`, and **Saved retrieval 1**.
   Review the created evaluation: original task `Return policy?`, copied passage
   `Thirty days.`, pinned revision 1, and no private feedback comment.
6. Repeat history/editing at phone and tablet widths. Check readable multiline
   comments, touch controls and horizontal overflow.

The [API contract](../postman/contracts/studio-feedback.md) and saved Postman
assertions repeat owner isolation, current edits and source-preserving promotion.
Only use their synthetic cookies against this disposable fixture.

Local evidence: 35 Python tests and 13 JavaScript tests passed without skips.
Three Postman assertion groups passed against the real local routes. The browser
export was inspected for current Alice-only contents. Full-app creation, editing,
cancel/reopen, history, export, promotion/review, role restrictions and response
inspection passed. Desktop, 390px phone and 820px tablet layouts were checked;
phone controls measured at least 44px high with no horizontal overflow.

The fixture reuses the sharing fixture's loopback Host/Origin front door and
existing Nexus team authorization. It does not prove real HTTPS termination,
secure cookies, real account sessions or physical-device behavior. Those remain
part of personal-VM operational acceptance. Unified operational summaries,
release comparison, resource health and other milestone 8 work remain open.
