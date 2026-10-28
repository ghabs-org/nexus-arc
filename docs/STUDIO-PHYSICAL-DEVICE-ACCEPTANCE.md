# Nexus Studio physical-device acceptance

Status: prepared; not executed on a physical phone/tablet by the agent. Browser
width emulation and source tests are separate evidence. Record real device model,
OS/browser versions, date, result and any failure for each check below. Never put
credentials, session references, private conversation contents or audio into Git.

Use the existing personal HTTPS Studio and its normal Nexus login. Enter any
login reference only in the login page. The synthetic acceptance conversation
created on 28 October 2026 contains `STUDIO_ACCEPTANCE_OK` and can be reused for
reading/layout checks without making another model call.

1. **Conversation space:** open Conversations in portrait and landscape. Confirm
   the title remains readable and the settings section starts collapsed at phone
   width. Open/close Settings; confirm response and composer remain reachable.
2. **Touch and keyboard:** focus the composer, show/dismiss the keyboard, scroll
   the response and use navigation. Confirm no horizontal scrolling or controls
   obscured by the keyboard/safe-area boundaries. Do not submit a test message
   unless an actual provider check is intended.
3. **Accessibility:** enable VoiceOver or TalkBack. Check navigation, Settings,
   Attach files, Record voice, response feedback and playback controls have
   understandable names and a usable focus order. Test enlarged text.
4. **Device playback:** play the synthetic response using device speech. Verify
   stop and speed controls. Record any OS/browser limitation; this is not server
   or API speech-generation evidence.
5. **Recording:** only when the person testing is ready, press Record voice and
   handle the browser/OS microphone permission themselves. Say “Nexus Studio voice
   test,” stop recording and verify transcription using an already configured
   provider. Record the actual provider and whether this used local inference or
   a billed API. Do not silently substitute a paid provider for an unavailable
   local one. Also test denied permission and retry after the user changes it.
6. **Installation:** use Install app or the browser's supported installation
   action. Open the installed web app, confirm HTTPS/login and conversation
   navigation, then close/reopen. Record unsupported browser/device behavior
   explicitly instead of counting it as a pass.

A result can be pass, fail or unavailable. Microphone permission must be handled
on the real device. No physical-device gate closes until its actual result is
recorded. A later desktop test uses its existing beta testing instructions;
creating a new DMG still requires an explicit user request.
