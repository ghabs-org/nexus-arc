# Signed macOS release and verified updates

The Apple Silicon release pipeline uses two independent trust mechanisms:

- Developer ID Application signing plus Apple notarization for Gatekeeper.
- A separate private update key whose public half is pinned in the native app.

An Apple Development or Apple Distribution certificate does not replace Developer
ID Application for a directly distributed DMG. An ad-hoc candidate remains a beta
until Developer ID signing, accepted notarization and installed checks pass.

## Apple setup

Create a certificate signing request on the Mac through Keychain Access:
Certificate Assistant → Request a Certificate From a Certificate Authority.
Use your own email and a descriptive common name, leave CA Email Address blank,
and choose Saved to disk. Apple is the CA; the email domain needs no CA service.

On the correct Apple Developer team, the Account Holder creates a Developer ID
Application certificate from that request. Download and open the `.cer`; My
Certificates must show the certificate and its private key. Do not export the key
to chat, the repository or build logs.

Create an app-specific password in your Apple Account and store it interactively:

```sh
xcrun notarytool store-credentials YOUR_NOTARY_PROFILE --apple-id YOUR_EMAIL --team-id YOUR_TEAM_ID
```

Enter the password at the private terminal prompt. The build uses only the
Keychain profile name. [Apple certificate guidance](https://developer.apple.com/help/account/certificates/create-developer-id-certificates)
and [notarytool guidance](https://developer.apple.com/documentation/technotes/tn3147-migrating-to-the-latest-notarization-tool).

## Build and release

Build the standalone full SDK backend with the pinned macOS arm64 requirements,
then the Tauri app. Use the generated bundle resource configuration; see
[full-profile instructions](STUDIO-DESKTOP-FULL-SDK.md). This does not package
Docker, subscription CLIs, model weights or account credentials.

The explicit release consumer accepts an application, a new output directory,
the installed Developer ID identity, a notarization profile, the private update
key file path and the installed official Tauri CLI `tauri.js` path:

```sh
python apps/studio-desktop/release_macos.py \
  --application '/PATH/TO/Nexus Studio.app' \
  --output /NEW/PRIVATE/RELEASE/FOLDER \
  --identity 'Developer ID Application: YOUR_TEAM (TEAM_ID)' \
  --keychain-profile YOUR_NOTARY_PROFILE \
  --updater-key /PRIVATE/PATH/updater.key \
  --tauri-cli /PATH/TO/tauri.js
```

The framework signs native files under Resources, including Python and SDK
libraries, inside-out before signing the app. It requires timestamps and hardened
runtime; it does not disable library validation. Only the Python sidecar receives
`com.apple.security.cs.allow-unsigned-executable-memory`, required by the pinned
LLVM/Numba runtime. Libraries inherit that host entitlement; the native shell does
not receive it. A full signed SDK self-check must pass before submission. The
pipeline then notarizes and staples the app,
then creates, signs, notarizes, staples and verifies the DMG. Only then does it
create and sign the final updater tarball with the app version in the signature's
trusted comment. A failed step stops the pipeline and never publishes anything.

Retain the update private key in protected storage and back it up separately.
Losing it prevents future updates to apps pinned to that public key. Key material
must never enter Git. This Mac's release key is held under
`~/.config/nexus-studio/release-signing/` with private directory/file permissions.

The output includes `SHA256SUMS.txt`, notarization submission IDs in `release.json`,
the DMG, updater archive/signature and a prepared `nexus-studio-latest.json` feed.
Existing betas without this updater need one manual application replacement.
The ad-hoc 1.0.2 candidate must also be replaced manually by a trusted build of
the same version; it does not automatically reinstall an equal version.

Publishing those artifacts is a separate release operation. The manifest URL
must match the actual release tag/assets; a local prepared file is not a live feed.

## Native update behavior

Select Workspace → Check for Updates. There is no startup auto-check, automatic
download or webview update permission. The native menu displays release notes and
requires confirmation. The download must use HTTPS. The official Tauri updater
checks the pinned key, payload signature and signed app version before stopping
Studio or replacing application files. Downgrades are not enabled.

After verification, the shell stops the backend and invokes the frozen framework
backup consumer. It takes the workspace lock, creates/verifies a private snapshot
and preserves the previous application under the application's `updates` folder.
Failure prevents installation. On success, installation verifies the bytes again
and restarts Studio. Keep the previous app and compatible workspace backup until
the update is accepted; restoring an old executable alone cannot reverse a schema
migration. SQL/external artifact configurations without a complete supported
snapshot fail preparation rather than install without backup.

Current installed-update support is macOS only. Windows/Linux entry points report
that native release acceptance is pending. Cross-platform installers, Windows
signing, live feed publication and an installed two-release update/rollback drill
remain separate gates. Cryptographic fixture tests are not substitutes for those
checks. [Official updater documentation](https://v2.tauri.app/plugin/updater/).
