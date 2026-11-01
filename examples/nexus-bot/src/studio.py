"""Serve the framework's responsive workspace, including mobile navigation, the approval inbox, setup guides and installation.

The same Studio UI adapts to phones, tablets and desktop browsers. For access
from a separate phone, configure authenticated team hosting over HTTPS; a phone's
localhost address does not point to the computer running this example.

This consumer serves the framework's shared stylesheet, including readable
composer/helper text, reduced-motion attachment feedback and touch-sized secondary
actions. To inspect those states, attach a small local file without sending a
message, compare the attachment entrance with the system's Reduce Motion setting,
and inspect the remove/import actions at a phone width or on a touch device.
These manual checks do not require an AI call. Rendered/device acceptance remains
separate from source and interface-logic checks.

The framework also follows the system light/dark preference. Switch that setting
while Studio is open and inspect focused fields, selected tabs, status labels,
file previews and existing diagrams. The same palette applies to shared/document
viewers and the native startup screen. Inspect control outlines separately from
decorative dividers; changing the theme does not submit or restart work.

Use Theme in the header, sidebar on narrow desktops, or mobile Menu to select
System, Light or Dark. This browser remembers the choice for the current Studio
address; System remains the default. Shared/source viewers offer the same picker.
Storage-disabled browsers keep the choice only while the page is open. Native
startup still follows the OS; this preference belongs to the served UI origin.

Fonts and interface icons also come from the framework's local static assets:
IBM Plex Sans/Mono and the licensed Tabler outline sprite. No font CDN is needed.
Inspect the header, file categories, audio controls and expanded chat controls in
both themes. At phone widths, open Menu, change Theme, then use Tab and Escape to
check focus containment and return. Empty desktop conversations scroll as a page
so welcome actions remain reachable on shorter screens; existing conversations
retain their message panel. Shared radius, elevation and layer tokens keep button
pressed states and detail-dialog full-view toggles immediate without layout-property
transitions. These checks do not require sending a message.
"""

import os

from nexus.studio import serve_studio


def main():
    serve_studio(
        os.environ.get("NEXUS_STUDIO_DIR", "."),
        port=int(os.environ.get("NEXUS_STUDIO_PORT", "5003")),
        host=os.environ.get("NEXUS_STUDIO_HOST", "127.0.0.1"),
        team_config=os.environ.get("NEXUS_STUDIO_TEAM_CONFIG"),
        state_dir=os.environ.get("NEXUS_STUDIO_STATE_DIR"),
    )


if __name__ == "__main__":
    main()
