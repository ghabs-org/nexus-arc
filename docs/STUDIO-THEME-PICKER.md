# Studio theme picker

Date: 31 October 2026. Adds the explicit choice missing from the system-theme
implementation. This source update is not a VM deployment or desktop rebuild.

Choose **System**, **Light** or **Dark** from Theme. The control appears in the
wide desktop header, in the sidebar on narrower desktops, and inside Menu on
phones. Embedded single-agent views retain a header picker. Shared conversations
and document-source views have the same control. Native selects preserve keyboard
and screen-reader behavior; mobile controls retain the 44px minimum height.
The navigation focus trap includes the new select.

System is the default and follows operating-system changes. Light and Dark remain
fixed until another choice is selected. The browser stores only the selected mode
under `nexus-studio-theme` in local storage for this origin. This is a display
preference, not an account/workspace setting or a credential. Same-origin tabs
and standalone views synchronize through storage events. Invalid stored values
fall back to System. If storage reads/writes are unavailable, the current page's
picker remains usable without persistence.

The controller loads from the existing static asset handler before the stylesheet
is painted. It sets bounded root attributes, updates native theme metadata and
synchronizes visible pickers. Explicit dark and automatic dark use matching
palette declarations, protected by a consistency test. Explicit light disables
the automatic dark selector. Both dark paths apply only to screens, preserving
light print behavior and existing contrast roles.

The controller also updates application-owned scheme metadata in registered
restricted HTML previews. Their already sanitized content, sandbox and CSP stay
intact. Other frames are ignored. Reapplying the same resolved theme does not
reload an unchanged preview. No run, provider call, API mutation or page navigation
is triggered by the choice. The public-shell cache is v67 and includes the new
local script through the existing explicit update flow.

The remembered choice is scoped to the current Studio address. Another browser,
origin or changing desktop backend port can have a separate preference. The native
startup document still follows the OS before the served UI opens. Cross-origin
native preference storage/startup synchronization is outside this browser slice;
no installer or native shell was rebuilt.

## Follow-up acceptance

The [local-assets slice](STUDIO-LOCAL-ASSETS.md) adds local fonts/icons, advances
the public cache to v68 and records fresh scoped browser checks. The checks below
are the original picker snapshot: its browser-pending statement is historical.
Current evidence covers sampled phone/tablet/desktop layouts, selecting all three
modes, persisted System after reload and mobile Tab/Escape/focus behavior. It does
not establish first-paint timing, every screen or real-device/PWA acceptance.

## Evidence and remaining checks

Twelve focused theme/picker checks pass. The full frontend gate passes 279 tests
with zero failures/skips; the focused checks are included in that total. Tests
cover initial saved preference, reload, synchronized controls/tabs, OS changes,
fixed overrides, invalid values, denied storage, registered preview updates,
palette consistency, contrast contracts and existing HTML isolation settings.
Syntax, example compilation/lint and whitespace checks pass.

These are Node/source fixtures, not browser layout acceptance. No browser was
opened and the earlier access denial was not retried or bypassed. Verify actual
desktop/tablet/mobile layout, keyboard focus, native dropdown behavior, first-paint
appearance, same-address tabs, shared/source views, previews and print output
through an authorized browser path. Real device/PWA behavior remains pending.
No VM, provider, dependency installation, publication or DMG operation occurred.

Next source work remains locally packaged fonts and a documented icon family.
The complete design audit and implementation plan remain open.
