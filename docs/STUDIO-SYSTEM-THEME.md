# Studio system theme and control contrast

Date: 30 October 2026. Scope: DT-02 and the shared control-boundary source issues
in DT-06 from the design audit. The complete audit and implementation plan remain
open. This does not certify every rendered state or physical device.

## Behavior

Studio now follows the system light/dark preference through a shared CSS token
palette. No account preference, browser storage, settings route or theme framework
was added. A preference change updates the current interface through CSS, without
submitting or restarting runs. Existing layout, navigation, field order, interface
wording and approval behavior remain intact.

The stylesheet covers the main workspace, sign-in surface, dialogs, chat controls,
Quick Build, Library, sharing/document viewers, code/text/table previews and native
workflow/execution diagrams. Fixed light fills, dark foregrounds and shadows were
replaced with role tokens. Native startup uses the same canvas, text, muted and
accent colors. Semantic success/error/waiting colors remain distinct from the
teal interaction accent.

Main-page theme metadata and standalone viewer metadata declare system-theme
support. Restricted HTML previews receive an application-owned color-scheme meta
tag. Their empty sandbox, no-referrer policy, sanitizer exclusions and restrictive
no-network/no-style CSP are unchanged. Artifact-supplied metadata remains forbidden.
Images, PDFs, audio and original downloaded documents are not inverted or rewritten.
Browser-controlled preview widgets can retain their own platform presentation.

The dark palette applies to screens only. Existing light print/export rendering
is preserved. The public-shell cache advances from v65 to v66 using the existing
explicit update flow; no additional origin or private data is cached.

## Color roles

| Role | Light | Dark |
| --- | --- | --- |
| Canvas | `#f9fbfc` | `#15191c` |
| Surface | `#ffffff` | `#1c2227` |
| Raised surface | `#f4f6f8` | `#252d33` |
| Main text | `#131416` | `#edf2f5` |
| Muted text | `#5d5f60` | `#b5bfc6` |
| Interaction accent | `#1b6c80` | `#8bd1df` |
| Enabled control boundary | `#7c858b` | `#87939d` |
| Decorative divider | `#e8eaec` | `#354047` |

Enabled form/search/select/secondary-action boundaries use the control role.
Decorative separators retain a quieter line role. Selected states, hover colors,
native controls, graph edges and select arrows receive corresponding theme colors.
The light control boundary improves from 1.57:1 to 3.76:1 against white. Palette
contracts require enabled boundaries to reach 3:1 against normal, raised and
selected surfaces. This is not a claim that every separator must reach that ratio.

Preview aliases (`--surface-muted`, `--surface-alt`, `--border`, `--text`, `--hover`
and `--focus`) resolve to shared roles instead of falling back to light literals.
Sanitized diagram shapes, text, edges and markers use theme roles without changing
their source or weakening SVG sanitation. Complex generated diagram types and
platform preview widgets still require rendered inspection.

## Verification

The initial five palette/preview tests failed before the changes, including the
1.57:1 control border and missing dark palette. The final focused gate contains
six passing tests, adding startup/workspace consistency coverage. The full
frontend gate passes 273 tests with zero failures or skips, including these six.
Counts overlap; do not sum the focused and full gates.

Numerical checks cover 54 workspace text pairs at 4.5:1, 20 control/diagram pairs
at 3:1, and two startup text pairs at 4.5:1. They read declared CSS tokens and
resolve aliases. They do not emulate the browser cascade or measure all computed
foregrounds, borders, opacity, disabled states or backgrounds. A separate fixture
checks the HTML preview's sandbox, CSP, sanitizer configuration and owned metadata;
it does not replace browser security acceptance.

JavaScript syntax, stylesheet delimiter checks, preservation of existing CSS
comments, example compilation/lint and whitespace checks pass. The example
consumer continues to serve the framework UI and documents manual theme checks;
it introduces no duplicate implementation or provider call.

No browser/Lighthouse or physical-device check was run. The earlier browser access
denial was not retried or bypassed. No VM operation, dependency installation,
release upload or DMG rebuild occurred. Existing installed/deployed copies do not
automatically acquire this source change.

## Remaining acceptance

Use an authorized browser path to inspect light/dark on phone, tablet and desktop
widths, including open dialogs, Quick Build, selected resources, error/empty/loading
states, shared/source views and diagrams. Change the system preference while those
views are open; confirm no run is submitted or restarted. Inspect focused/hovered
control boundaries and native controls, keyboard/screen-reader behavior and print
output. Verify installed PWA chrome/splash behavior on real devices; platform
launch screens may use the manifest's static fallback colors.

Local fonts and a documented icon family remain the next source slice. Broader
radius/layer/interaction consistency, interface punctuation and dialog layout
animation remain open. Desktop packaging stays last and requires an explicit
new build request; no VM deployment is implied.
