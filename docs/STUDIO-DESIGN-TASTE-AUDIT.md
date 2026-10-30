# Studio design guideline audit

Date: 30 October 2026. Result: **not fully aligned**. This is a source-based audit,
not approval of every rendered screen or an implementation of the fixes below.

## Design read and scope

Reading this as: an existing agent workspace for daily use, with a calm,
functional visual language, leaning toward its existing native CSS component
system. Mode: redesign-preserve. `DESIGN_VARIANCE: 3`, `MOTION_INTENSITY: 2`,
`VISUAL_DENSITY: 5`. Predictable controls and readable work take precedence over
marketing composition and decorative motion.

The requested skill explicitly excludes dashboards, data tables and multi-step
product UI. Its applicable principles are preservation, tokens, typography,
contrast, interaction states, responsive behavior, accessibility and performance.
Its recommendations for Fluent, Carbon and other product systems are alternatives
for that problem class, not a mandate to replace this existing interface. The
approved project constraint preserves this frontend without a framework rewrite.

Audited first-party Studio HTML, CSS and JavaScript, including shared conversation
and document viewers, sign-in markup and the desktop startup surface. The desktop
shell opens the same Studio UI. Vendor libraries, rendered user/AI documents,
third-party login pages and the separate legacy example visualizer are not
redesign targets. Do not rewrite user content to enforce editorial preferences.

No app code, branding, routes, navigation labels, form order, credentials, VM or
release assets changed. No new package, font, image or component framework was
installed. In particular, the skill's ban on inventing a generic brand does not
authorize renaming the existing Nexus brand.

## Existing language to preserve

The app uses IBM Plex Sans/Mono, a restrained teal accent (`#1b6c80`), neutral
surfaces and green/red semantic states. Primary controls are dark with white
text. Sidebar navigation, the Nexus mark, task-specific empty states, shared
component styling, labels and existing keyboard behavior should remain.

Good foundations visible in source:

- Core light-theme body, muted text, accent and primary-button pairs pass the
  calculated text contrast target below. Semantic status colors are meaningful;
  they are not automatically extra decorative accents.
- The static main document has 197 controls. Each has a label, accessible-name
  attribute or button text; its IDs are unique. This structural check does not
  verify names' accuracy, native announcements or generated forms.
- A skip link and focus-visible treatment exist. Native dialogs, labels and
  disclosures are reused. Chat settings collapse on mobile and close on send;
  the code includes Escape/focus restoration. The main mobile response body is
  explicitly 16px with 1.7 line height.
- Responsive grid collapse, dynamic viewport sizing, safe-area padding, coarse
  pointer rules and 44px primary mobile targets are present. The title is kept
  intact and the guide/install actions have mobile icon treatments.
- Rich responses use the shared Markdown/sanitization implementation. Loading,
  empty, error and job-progress states exist; no fake product screenshot is used.

## Findings, highest priority first

### DT-01: Text contrast fails in the main composer

Priority: P1. Skill sections 4.5, 6 and 8.

`nexus/studio_static/studio.css:3` sets `.composer textarea::placeholder` to
`#96958f`, about **3.00:1** on its white surface. The same line sets desktop
`.chat-footnote` text to `#92918b`, about **3.16:1**. Both are small normal text,
below 4.5:1. The general `textarea::placeholder` rule at line 513 has lower
specificity and does not repair the composer placeholder.

Use a shared text token with sufficient contrast in each theme. Preserve labels,
wording and routes. Verify the composer and helper text using computed colors,
including inheritance, opacity and focus/disabled states. Do not darken every
decorative divider as if it were body text.

### DT-02: Web dark mode is absent

Priority: P1 for the requested dual-theme alignment. Skill sections 4.11, 6.C and 8.

`nexus/studio_static/studio.css:2` forces `color-scheme:light` and white surfaces.
No first-party web dark token set or theme selection was found. The standalone
startup CSS has a dark media query, so dark-mode users can move from a dark
startup screen into a light workspace. Browser-forced darkening is not a designed
or tested theme.

Introduce theme tokens for surfaces, text, controls, semantic states, previews
and diagrams; retain the brand accent. Cover shared/artifact viewers and overlays
as well as the main app. Remove hardcoded light overrides as part of that work.
Do not invert isolated sections or claim dark mode from startup styling alone.

### DT-03: Typography depends on a remote CSS import

Priority: P2. Skill sections 3.A, 3.F and 6.D.

`nexus/studio_static/studio.css:1` imports Google Fonts. The shell cache lists no
font files, and no self-hosted `@font-face` was found. Offline or restricted
connections fall back to another face, changing text geometry; first loading
also depends on the external CSS/font requests. No actual network timing or
layout shift was measured in this audit.

Preserve IBM Plex with locally packaged, licensed font files and
`font-display:swap`, or deliberately use a consistent system stack. Keep wheel,
desktop and service-worker packaging in agreement. Do not replace the existing
font merely because another font appears in the skill's greenfield defaults.

### DT-04: Reduced motion does not disable the attachment animation

Priority: P2. Skill sections 5.D and 6.B.

`studio.css:159` applies `fadeInChip` unconditionally; line 160 animates opacity
and vertical translation. Both reduced-motion blocks (lines 6 and 580) suppress
transitions/scroll behavior but do not disable animations. The guide's separate
reduced-motion handling does not disable this CSS animation.

Gate this entrance animation on `prefers-reduced-motion:no-preference`, or disable
it explicitly in reduce mode. Keep attachment state updates immediate. This is a
source-confirmed issue even though the chosen design dial is low.

### DT-05: Mobile target rules have specific exceptions

Priority: P2. Skill sections 3.E, 4.5 and 11.C.

The 44px mobile/coarse-pointer baseline is good. However,
`studio.css:168` sets `.chat-chip-remove` to `min-height:0`, no vertical padding
and a 16px line box. `studio.css:657` explicitly uses a 26px hint-action minimum.
Those class selectors override generic `button` sizing. These small secondary
actions do not inherit the intended mobile baseline.

Give touch controls deliberate hit areas without making every desktop toolbar
oversized. Check spacing and hit boxes at 360/390px widths and on a real device.
This finding identifies inconsistent touch sizing; it does not assert a blanket
WCAG target-size failure without testing spacing exceptions and actual geometry.

### DT-06: Control boundaries need a non-text contrast pass

Priority: P2. Skill sections 4.5 and 6.

`--control:#cdced1` is **1.57:1** against white; `--line:#e8eaec` is **1.21:1**.
The form/input styles at lines 512, 689, 754 and 771 reuse these weak boundaries,
including white fields against white surfaces. Where the boundary is needed to
identify the field, it should reach the relevant 3:1 non-text contrast target.
The focus accent itself is about 5.99:1 against white.

Use a distinct control-border token instead of strengthening all separators.
Verify which boundaries convey control identity, selected state or purely
decorative grouping. Inspect actual focus outlines and high-contrast modes before
claiming accessibility conformance.

### DT-07: Icons do not have a documented library family

Priority: P2 for skill alignment. Skill sections 3.C and 9.E.

`index.html` contains 13 inline SVG instances; first-party JavaScript adds others.
The help/download/search glyphs, speech and file icons use embedded paths and
several stroke widths, including 1.7, 1.8 and 2. No canonical approved icon package,
vendored family or attribution was found. Inline SVG itself is not an accessibility
defect; the missing family/provenance and inconsistent weights are the design gap.

Choose one approved family, package its required assets locally with license
metadata, and standardize sizing/strokes. Preserve accessible labels and hide
decorative paths from assistive technology. Do not add a runtime CDN or a React
dependency solely to replace icons in a vanilla interface. Charts, workflow
graphs and the existing logo are not UI icon-family violations.

### DT-08: Token and layout policy needs consolidation

Priority: P2. Skill sections 4.2, 4.4, 6.F and 11.

`studio.css` has an existing token layer at lines 2 and 463, but many later rules
still hardcode colors, radii and elevations. Quick Build uses a separate green
focus/accent vocabulary at lines 14 and 24. Attachment image glyphs use purple
at lines 163/183. Existing shadows include raw black RGBA values. Radii and
z-index values are spread across overrides without a documented role scale.
Multiple radius values are not automatically wrong; their component roles need
one explicit policy. Shared buttons also lack a common pressed state; explicit
`:active` feedback is currently limited to selected guide/dropzone treatments.

Define roles for control/panel/dialog/pill radii, semantic versus decorative
color, border strength, elevation and layers. Apply tokens in small behavior-
preserving passes. Document any meaningful category-color exception. Do not
flatten semantic success/error colors into one accent, rewrite the framework,
or normalize all component geometry to one radius. Add shared pressed feedback
without introducing perpetual animation or shifting focused controls.

### DT-09: Long dash separators remain in interface literals

Priority: P3, editorial skill alignment. Skill section 9.G.

`operations.js:38`, `:158`, `:405`, `:439` and `graph.js:25` contain long dashes
in interface copy or numeric ranges. `workbench.js:143` formats segment time
ranges with one. These are real skill mismatches, not runtime faults.

Change interface-authored separators/ranges to plain punctuation in a copy pass.
Do not alter imported documents, transcripts, answers, filenames, identifiers or
other user data to satisfy this preference. Keep existing form field names,
navigation labels and legal/consent wording intact.

### DT-10: Dialog resizing animates layout properties

Priority: P2 for performance alignment. Skill section 6.A.

`studio.css:814` transitions width, height, max-width and max-height on the detail
dialog. That contradicts the transform/opacity-only animation rule and can add
layout work during expansion. No frame timing regression is asserted here.

Use immediate geometry changes, or a measured transform/opacity transition that
does not compromise text clarity, focus or reduced-motion behavior. Do not add
Motion or GSAP for this small interaction.

## Calculated light-theme samples

These ratios use sRGB relative luminance for literal source colors. They are not
browser-computed colors, do not include every state, and do not prove whole-page
WCAG conformance.

| Pair | Contrast | Interpretation |
| --- | ---: | --- |
| Body `#131416` on white | 18.43:1 | Pass for normal text |
| Muted `#5d5f60` on white | 6.42:1 | Pass for normal text |
| Primary button white on `#1d1f20` | 16.55:1 | Pass for normal text |
| Accent `#1b6c80` on white | 5.99:1 | Pass for normal text |
| General placeholder `#6b7479` on white | 4.77:1 | Pass in this combination only |
| General placeholder `#6b7479` on raised `#f4f6f8` | 4.41:1 | Below normal-text target if this pair occurs; inspect computed backgrounds |
| Composer placeholder `#96958f` on white | 3.00:1 | Confirmed source pair below text target |
| Desktop helper `#92918b` on white | 3.16:1 | Confirmed source pair below text target |
| Control border `#cdced1` on white | 1.57:1 | Below 3:1 where the boundary identifies a control |
| Separator `#e8eaec` on white | 1.21:1 | Keep decorative separators distinct from required control boundaries |

## Pre-flight matrix

Every box in skill section 14 was considered. Numbers below preserve that list's
order. `Source pass` means limited static evidence, not rendered acceptance.
`Pending` means insufficient proof. `N/A` applies the skill's explicit scope and
preservation rules; it is not a claim that a missing behavior passes.

| Box | Check | Result and reason |
| ---: | --- | --- |
| 1 | Brief inference | Pass: declared above. |
| 2 | Explicit dials | Pass: preserve mode; predictable daily product UI. |
| 3 | Chosen foundation | Pass in context: existing native CSS system, no official system impersonated. |
| 4 | Redesign audit | Pass: tokens, structure, preservation and findings recorded. |
| 5 | Zero long dashes | Fail: interface literals, DT-09. User content excluded. |
| 6 | Page theme lock | Partial: main light palette is consistent; dark startup/main mismatch, DT-02. |
| 7 | Accent consistency | Partial: teal core plus meaningful status colors; decorative/category/Builder exceptions need policy, DT-08. |
| 8 | Shape consistency | Partial: role-based radius scale is not documented, DT-08. |
| 9 | All button contrasts | Pending: sampled primary/secondary pairs pass; all computed hover/active/generated combinations unverified. |
| 10 | Desktop button wrapping | Pending: long workflow/library labels and added actions need actual layout checks. |
| 11 | Form contrast | Fail: composer placeholder, DT-01; boundary concerns in DT-06. |
| 12 | Serif discipline | Source pass: existing IBM Plex sans/mono; no new serif introduced. |
| 13 | Premium-consumer palette | N/A: agent workspace, not a consumer marketing brief. |
| 14 | Italic display descenders | N/A: no relevant first-party italic display treatment found; user prose excluded. |
| 15 | Hero fits viewport | N/A as a marketing rule; initial workspace/Quick Build action visibility still pending browser checks. |
| 16 | Hero top padding | N/A: product entry states. Do not redesign them to impose a marketing hero. |
| 17 | Hero stack limit | N/A: task controls are real product controls. |
| 18 | Eyebrow quota | N/A across hidden routes: 19 static eyebrow instances do not mean 19 visible sections. Agents' nested headings remain a typography review candidate. |
| 19 | Split-header ban | N/A to heading/action toolbars. Existing supporting copy sits with its heading. |
| 20 | Zigzag cap | N/A: no marketing section sequence. |
| 21 | Duplicate CTA intent | N/A as a global ban: repeated contextual product actions can be valid. Preserve intent clarity. |
| 22 | Logo-only wall | N/A: no first-party customer logo wall. |
| 23 | Bento visual variation | N/A: resource cards contain real product records, not marketing bento. |
| 24 | Real logos below hero | N/A: no customer proof wall. |
| 25 | Every visible string reviewed | Pending: static labels sampled; dynamic account/error/import states need rendered copy review. DT-09 is confirmed. |
| 26 | Motivated motion | Partial: state feedback exists; attachment preference and dialog geometry need correction, DT-04/10. |
| 27 | One marquee maximum | Source pass: no first-party marquee found. |
| 28 | Single-line navigation | N/A for vertical sidebar; 69px desktop topbar fits nominal height cap. Title/search/actions need width checks. |
| 29 | Different layout per section | N/A: repeatable product templates intentionally share layout. |
| 30 | Exact bento cell count | N/A: no marketing bento. |
| 31 | Long list component | Partial: search/filter/tabs exist; data tables and execution histories are intentionally preserved. Large-data rendering unverified. |
| 32 | Real marketing images | N/A: actual workspace UI; do not generate decorative photos or fake product screenshots. |
| 33 | No labels over photos | N/A: document/user-media previews are product content. |
| 34 | No decorative photo credits | Source pass: no first-party decorative photo-credit pattern found. |
| 35 | No marketing version footer | N/A: real product/runtime version information can remain. |
| 36 | No eyebrow meta prose | N/A as a marketing limit; functional permission/billing guidance must remain. |
| 37 | No hero decoration strip | N/A: no marketing hero. |
| 38 | No floating explainer | Source pass: section description lives with heading; actual geometry still pending. |
| 39 | No comparison progress tracks | N/A for operational progress or score data. |
| 40 | No locale decoration | Source pass: no decorative city/weather header; real timezone/tool data excluded. |
| 41 | No scroll cues | Source pass: no first-party decorative scroll cue found. |
| 42 | No hero version labels | N/A: actual product runtime/status information is not marketing decoration. |
| 43 | No numbered eyebrows | N/A to real setup progression labels such as 2 OF 3; retain navigable progression. |
| 44 | No decorative dots | Partial: runtime status dot exists; its actual state association needs review, not a blind visual removal. |
| 45 | Sparse row dividers | N/A as a marketing prohibition on operational tables/history; retain useful grouping. |
| 46 | Content density | Pending: mobile collapse exists; controls, nested disclosures and long settings forms need screen review. |
| 47 | Short testimonials | N/A: no product testimonial section; user/AI responses may be long. |
| 48 | Motion matches dial | Pass in context: low motion chosen, no cinematic treatment required. |
| 49 | GSAP pin/scrub pattern | N/A: no first-party GSAP scroll stack/pan. |
| 50 | No raw window scroll animation | Source pass: no first-party forbidden scroll-animation listener found. |
| 51 | Reduced motion | Fail: attachment entrance animation remains enabled, DT-04. |
| 52 | Tested dark mode | Fail: no main-app dark theme; both-mode visual acceptance unperformed, DT-02. |
| 53 | Explicit mobile collapse | Source pass with limits: dedicated mobile rules and settings disclosure exist; actual overflow/touch testing pending. |
| 54 | Dynamic viewport sizing | Partial: main app uses dvh; startup still uses 100vh. Desktop-only startup is not a mobile hero defect. |
| 55 | React effect cleanup | N/A: vanilla frontend, no React effect tree. Existing listener ownership still needs its normal behavioral tests. |
| 56 | Empty/loading/error states | Partial: states exist and interface logic passes; all rendered/generated states not inspected. |
| 57 | Restraint with cards | Pass in context: resource grouping and dialog hierarchy have purpose. Three chat task starters are not fake feature cards. |
| 58 | Approved icon family | Fail: no documented family/provenance; mixed embedded glyphs, DT-07. |
| 59 | Client-leaf motion | N/A: no React/Next framework migration authorized or necessary. |
| 60 | No generic AI tells | Partial: real brand preserved, no decorative gradient hero. Token/icon/copy findings remain; no blanket aesthetic certificate. |
| 61 | Core Web Vitals | Pending: no Lighthouse, LCP/INP/CLS measurements or large-state performance run. DT-03/10 identify risks only. |
| 62 | One design system | Pass in context: one existing shared stylesheet/component system; no new library mix. Token debt is DT-08. |

## Validation and next work

`node --test tests/studio_*.test.cjs` passes **267 tests**, zero failures, skips or
cancellations. These tests use Node logic/fixtures/source assertions; the word
"browser" in a filename does not make them real browser rendering tests. The
static HTML-name check covers main/shared/artifact/startup documents and finds
no unnamed controls or duplicate IDs in those source documents. It does not
certify generated forms or accessible announcements.

No browser or Lighthouse was run. The earlier local-origin browser access denial
was not retried or bypassed. Phone/tablet/desktop layout, two-mode visual review,
real touch/keyboard/screen-reader behavior and all computed state contrasts remain
pending. No VM was contacted and no DMG was rebuilt. The existing signed DMG does
not acquire these later source changes automatically.

Recommended correction order:

1. Fix text/control contrast, theme token coverage, motion preferences and touch
   sizing. Protect existing controls, routes, labels and mobile chat behavior.
2. Package fonts locally; standardize icon assets and role-based visual tokens.
   Remove layout-property animation and clean interface-only punctuation.
3. Perform rendered acceptance at phone/tablet/desktop widths and in both themes;
   include sign-in, chat, Quick Build, Library/evaluation actions, file browsing,
   shared/artifact views, dialogs and error/empty/loading states. Run performance
   and accessibility tooling against actual pages. Report remaining failures
   explicitly rather than promoting this source audit to visual acceptance.

This design work takes priority over the optional offline evaluation comparison.
It does not reopen accepted workflow/connector implementations or authorize a VM
deployment, framework rewrite, public release or a new desktop package.
