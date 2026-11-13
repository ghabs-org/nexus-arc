# Studio interface accessibility fixes

Date: 30 October 2026. Scope: the first small correction slice from the design
audit. Dark mode, fonts, icons and complete visual acceptance remain separate.

## Changes

The composer placeholder, chat helper text and general input placeholders now
use the existing muted-text token. The more specific composer selector no longer
overrides the shared placeholder with a low-contrast gray. Skill-filter placeholder
opacity is now 1, preserving the token's contrast rather than fading it again.
The approved color palette and all interface wording remain intact.

Attachment chips animate only when `prefers-reduced-motion:no-preference`
matches. Reduced-motion users and browsers without that preference feature get
immediate chip insertion. Existing reduced-motion transition/scroll handling stays
in place. Attachment selection, removal and run submission logic is unchanged.

At widths up to 767px or with a coarse primary pointer, attachment removal,
dialog hint actions and skill selection/import actions have 44px minimum hit
heights. Attachment removal and hint actions also have 44px minimum widths.
The compact skill-button rules previously used important 24px minima; the scoped
override accounts for those declarations. Action groups can wrap to avoid forcing
their enlarged buttons into one row. Fine-pointer desktop compact sizing remains.

The public shell cache is now `nexus-studio-shell-v65`, so an explicitly accepted
PWA update can cache the new stylesheet. Cache contents, origin restrictions,
private-data exclusions and the existing update approval flow are unchanged.
This source change is not a VM deployment or desktop rebuild.

## Evidence and limits

Literal sRGB contrast checks on current tokens:

| Foreground | Surface | Canvas | Raised surface |
| --- | ---: | ---: | ---: |
| Shared muted `#5d5f60` | 6.42:1 | 6.19:1 | 5.93:1 |
| Existing Builder muted `#666660` | 5.78:1 | 5.57:1 | 5.33:1 |

All six checked pairs exceed 4.5:1. Previously the composer placeholder measured
3.00:1 and desktop chat helper text measured 3.16:1 against white. These are source
color calculations, not a claim that every browser-computed state passes WCAG.
Control-border contrast from DT-06 remains open for the shared theme-token pass.

All 267 existing frontend logic/source checks pass with zero failures or skips.
Affected-rule inspection confirms the preference gate, placeholder selectors and
final touch overrides. JavaScript syntax, example compilation/lint and whitespace
checks pass. No implementation-mirroring tests or frontend dependencies were added
for these stylesheet fixes.

The existing `examples/nexus-bot/src/studio.py` consumer continues serving the
framework UI and now documents manual inspection of the affected states. It does
not duplicate styles or invoke AI to demonstrate them.

No browser was opened. The earlier local-origin access denial was not retried or
bypassed. Real rendered geometry, keyboard/screen-reader use, preference changes
and physical touch acceptance remain pending. No VM, provider, installation,
release upload or DMG operation occurred. The overall design audit and complete
implementation plan remain open.

## Manual acceptance when an authorized browser path is available

1. Open the example's Studio UI with a disposable workspace. Inspect composer,
   helper and skill-filter text in normal, focused and empty states. Confirm the
   expected text tokens in computed styles.
2. Attach a small local file without sending a message. Compare chip insertion
   with Reduce Motion enabled and disabled. Remove the attachment with keyboard
   and touch controls; no AI run is necessary.
3. At 360px, 390px and tablet widths, inspect the remove, hint and skill-import
   actions. Confirm at least 44px target height and no clipped labels or horizontal
   page overflow. Include a coarse-pointer tablet wider than 767px.
4. Check a fine-pointer desktop width to confirm compact secondary controls and
   existing chat navigation/focus behavior remain usable. Accept the normal PWA
   update when offered and verify the stylesheet is available offline afterward.

Next source slice: coherent dark theme and control-border tokens, followed by
local fonts/icons. Preserve the existing routes, brand, navigation and form order.
