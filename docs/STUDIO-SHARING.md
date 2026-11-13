# Read-only conversation sharing

Open a saved conversation and choose **Share snapshot**. Select up to ten saved
files, enter existing Nexus recipient IDs and choose an expiry. No files are
selected automatically. Public sharing is disabled by default; only a workspace
owner can enable it, and the sender must select the public-link checkbox for
each new public snapshot.

Creation freezes current messages and each selected file's latest revision.
Future messages and file replacements do not change the snapshot. The receipt
lists included filenames and their frozen revisions. Recipients can download only
included files. Revoke snapshots from **Conversation sharing**. Disabling public
sharing revokes existing public links permanently; enabling it again does not
restore old links.

The viewer renders sanitized Markdown using Studio's conversation styles. It has
no message composer or resource-editing controls. Private requests require the
current Nexus session and matching recipient/owner permission. Public requests,
including file downloads, explicitly omit account cookies. Tokens and account
details are not exposed in the read-only snapshot response.

Changing the link clears previous content immediately and invalidates pending
responses and downloads. Known expiry clears the loaded view. Returning from
the background or browser cache rechecks current access. Revocation and expiry
deny subsequent retrieval; they cannot remove a copy already saved by a recipient.

## Workflow runs

Finished runs offer the same **Share snapshot** action in **Task details**. The
snapshot freezes the task and one section per completed workflow step
("1. plan", "2. cfo"…); runs without recorded step names are shared as one
output section. **Download Markdown** saves the same content locally, and
**Print / Save as PDF** prints only the run details through the browser's print
dialog. Neither needs a reachable Studio, so they work for recipients outside a
private network.

## Repeatable local acceptance

Run the focused regression checks:

```sh
node --test tests/studio_shared_viewer.test.cjs tests/studio_sharing.test.cjs
python -m pytest -o addopts='' tests/test_studio_sharing_viewer.py tests/test_studio_team.py tests/test_studio_parity_features.py
```

Start a full-app fixture:

```sh
PYTHONPATH=. python tests/studio_sharing_team_acceptance.py
```

The printed `/acceptance` page offers synthetic identities and seeded private,
expired and revoked snapshot links. Alice owns the conversation and two library
files. Bob is the allowed recipient and owns a separate file. Viewer has no
snapshot grant. Owner controls public sharing. All state lives in a temporary
directory; no real account, provider or user workspace is contacted.

The loopback-only front door substitutes the HTTPS Host/Origin expected by the
existing team policy, like the HTTP team fixtures. Production authorization, CSRF
validation, scripts, styles and sharing routes still run. This checks application
behavior, not real TLS termination or secure-cookie deployment.

Browser acceptance sequence:

1. Sign in as Alice, skip the optional guide and open **Sharing acceptance**.
   Create a private snapshot for Bob with `selected.txt` only. Copy its link.
2. Sign in as Bob and open that copied link. Check Markdown tables/code, selected
   download and the absence of a composer. Viewer and anonymous must see no content.
3. Sign in as Owner and enable public links in **Conversation sharing**. As Alice,
   create an explicitly public snapshot. Open the copied link as anonymous.
4. Advance the synthetic snapshot past expiry using the fixture-only HTTP endpoint
   listed on `/acceptance`. Substitute the printed origin and snapshot ID:

   ```sh
   postman request POST http://127.0.0.1:PORT/acceptance/expire/SNAPSHOT_ID \
     --header 'Origin: http://127.0.0.1:PORT' --no-report-events
   ```

   Reload the copied link; the view must be empty. This fixture updates only its
   temporary expiry record so acceptance does not require waiting one hour.
5. Create another public snapshot. Revoke public/private snapshots using Studio's
   real menu actions, then revisit their copied links and confirm denial.
6. Switch between valid, expired and revoked hashes in one viewer tab. The old
   content must disappear. Repeat at desktop and 390px phone widths; check keyboard
   access and horizontal overflow.

The saved Postman script `postman/contracts/studio-sharing-viewer.assertions.js`
checks both private recipient and public snapshot responses in this fixture:
selected revision, shared messages and absence of owner/grant/token-hash metadata.
Private requests use the fixture's synthetic Bob cookie from the existing team
test identities. Never use fixture identities against a personal workspace.

## Local evidence and remaining checks

Local acceptance passed 43 Python and 10 JavaScript tests without skips, plus
two Postman assertion groups against real local Studio routes. Full browser
checks covered private/public creation, copied links, recipient/outsider/anonymous
access, selected download, expiry, menu revocation, sanitized rendering, read-only
controls and phone layout. Stale-response/download races, timer overflow, background
revalidation and known-expiry clearing have deterministic regression tests.

The browser download contained the synthetic original evidence. The fixture's
expiry was advanced through its HTTP test control; no production clock or expiry
setting was changed. Real VM HTTPS, secure cookies, genuine account sessions and
physical-device acceptance remain part of the deployment milestone. Other
milestone 8 work remains open.
