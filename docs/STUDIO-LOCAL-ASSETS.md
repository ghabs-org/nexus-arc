# Studio local typography and icons

Date: 31 October 2026. Scope: reusable framework UI, exact-path static delivery,
wheel/offline-shell packaging and scoped browser validation. This is not a VM
upgrade, desktop rebuild or completion certificate for the whole design audit.

## Assets and maintenance

IBM Plex Sans remains the interface face; IBM Plex Mono remains the code face.
Eight complete-distribution WOFF2 files cover Sans 400/500/600 normal and italic,
and Mono 400/500 normal. Each face uses `font-display:swap`. Sans regular is
preloaded; other faces load on demand. Font CSS no longer imports Google Fonts.
Original OFL-1.1 licenses accompany the files.

Twenty canonical Tabler outline symbols form one local SVG sprite. Twenty-six
literal interface SVG instances use it, preserving their accessible button names
and marking decorative glyphs `aria-hidden`. Shared sizes are 16, 20 and 24px,
with currentColor and a 1.75 stroke. Native select chevrons use the same canonical
Tabler glyph. Text punctuation, data visualizations, sanitized Mermaid diagrams
and the existing brand mark are not replaced with interface icons.

Sources are pinned to `@ibm/plex-sans@1.1.0`, `@ibm/plex-mono@2.5.0` and
`@tabler/icons@3.50.0`. Registry archive SHA-512 values were verified before
copying exact files or canonical SVG inner markup. No package installation hooks
ran. The packages' original licenses, source member names, archive integrity,
file SHA-256 hashes and glyph fingerprints ship in
`nexus/studio_static/vendor/studio-assets.json` and the adjacent license files.
General vendor metadata also lists the three pinned packages.

Primary upstream sources: [IBM Plex](https://github.com/IBM/plex),
[Tabler Icons](https://github.com/tabler/tabler-icons) and
[Tabler SVG usage](https://docs.tabler.io/icons/static-files/svg).

Update the source archive integrity, licenses, selected members, hashes and
metadata together when upgrading assets. Preserve icon names or update every
`use` reference. Keep public shell URLs, Python package data and static MIME
inventory aligned; increment the shell cache using the existing explicit update
flow. No font CDN, icon runtime or frontend build step is needed.

## Delivery and layout

The existing static asset inventory includes local WOFF2/SVG and their metadata.
Responses retain the existing no-sniff, CSP and no-store headers. Public shell v68
explicitly caches all eight font files and the sprite; private-data cache policy
is unchanged. Python wheel data includes WOFF2/SVG. Future native bundles already
copy the entire static directory; no new native build was produced here.

Browser inspection exposed a clipped welcome state at 1280x720: its content was
279.875px high inside a 212.5px messages panel. Empty desktop chats now scroll as
a page, making all welcome actions reachable. Real conversations retain their
bounded messages panel. The rule is desktop-only; mobile settings remain collapsed.
The existing `examples/nexus-bot/src/studio.py` consumer serves these shared assets
and documents manual inspection without a provider call.

## Verified checks

- All 284 frontend Node checks pass with zero failures, cancellations or skips.
  Five new asset tests cover font signatures/hashes/licenses, local face mappings,
  canonical sprite hashes/safe markup, all literal glyph references and offline
  shell inclusion. The existing theme and picker contracts remain in the suite.
- Two real static GETs pass twelve Postman assertions: status, MIME, font/sprite
  marker, no-sniff, CSP and no-store. `postman/scripts/studio-local-assets.js`
  is the reusable local post-request script. Calls used `--no-report-events`;
  no remote workspace publication or user credentials were involved.
- An isolated `uv build --wheel` succeeds. Fourteen checked wheel entries match
  source bytes: eight fonts, three licenses, sprite, asset manifest and theme.js.
  Normal build dependencies were isolated by uv; no runtime frontend dependency
  or framework was added. The wheel is not a DMG or release candidate.
- Ruff, Python parsing, six changed JavaScript syntax checks and whitespace checks
  pass. The existing Ruff configuration emits a deprecation warning only.
- Actual browser checks used a disposable local workspace: desktop 1280x720,
  tablet 1024x768, phone 390x844 and 360x780. Sampled conversation/Quick Build
  layouts fit horizontally. Plex fonts report loaded; visible local sprite icons
  render at the intended shared stroke and sizes. Observed asset inventory has
  no external HTTP(S) font/icon requests.
- Light and Dark selections render their respective palettes. Selecting System,
  reloading and reopening Menu retains System and resolves the current dark OS.
  Desktop Tab reaches the guide from the theme picker. Mobile Tab stays within
  navigation; Escape closes Menu and returns focus to its trigger. Settings are
  collapsed on phone, and the Conversations label remains visible.

Screenshots and raw local reports are recorded by path/hash in the checkpoint.
Those temporary files are convenience evidence, not required runtime files or
permanent Git artifacts. Browser viewport overrides were reset, the temporary tab
closed and the owned preview server stopped. It reported zero created runs.

## Remaining acceptance

DT-03 typography and DT-07 icon provenance are implemented with scoped browser
acceptance. Original audit JSON and earlier checkpoints remain historical
snapshots. DT-08's remaining radius/layer/pressed-state policy, DT-09 interface
punctuation and DT-10 dialog layout animation are separate work.

No claim is made for every page, complete screen-reader coverage, live OS change
behavior in the browser, first-paint timing, Core Web Vitals, real phone/tablet
hardware, microphone recording or installed/offline PWA behavior. Offline shell
membership is fixture-verified, not an offline browser acceptance run. Shared
snapshot/source viewers and complex generated content retain broader visual gates.
No AI provider was invoked, VM contacted, DMG rebuilt or release published.
