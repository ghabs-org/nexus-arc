# Studio configured media tools

Four registered tool actions reuse authenticated tool runs and workflow nodes:
`image_generate`, `image_edit`, `image_describe`, `audio_describe`. Each requires
an explicitly selected `profile`, `prompt`, and an enabled matching private
runtime media profile. Inputs for edit/description use owned `artifact_id` and
optional positive `revision`. Editing optionally accepts `mask_artifact_id` and
`mask_revision`. No input URL or local filesystem path is accepted.

Profiles specify action, explicit model, enabled boolean (default false), URL
(default https://api.openai.com/v1), allow_private (default false), and shared
header authentication references. Credentials come from the executing Nexus user;
a shared worker never borrows host credentials. Optional configured timeout is
1–180 seconds. No model, endpoint or API operation changes after a failure.
Compatible explicitly selected local endpoints are supported via allow_private;
the endpoint must implement the documented wire profile, not merely advertise
OpenAI compatibility.

Generation sends JSON to /images/generations. Editing uses the current JSON
/images/edits contract with inline `images[].image_url` and optional inline mask.
Output is one PNG (`n=1`, `output_format=png`); sizes are auto, 1024x1024,
1024x1536 or 1536x1024. Background may be auto, opaque or transparent.
Description sends inline image or WAV audio to /chat/completions with text-only
output, bounded completion tokens and store=false. Models must support the
selected operation. Images are valid single-frame PNG/JPEG/WebP; masks must be
PNG with matching dimensions. Audio is validated PCM WAV only, not automatic
transcription or a conversion from browser WebM.

Bounds: 16 MiB per image, 40 million pixels, max edge 8000; audio 4 MiB and
120 seconds, 1–2 channels, 8–192 kHz and 1–4 sample bytes. Prompt 1–4000 chars;
description 32 KiB; HTTP JSON response 24 MiB; output PNG 16 MiB. Returned image
URLs, invalid/truncated media, nonfinite JSON, refusals and incomplete text
results fail without publishing an artifact. Header/body parsing and transport
failures never expose raw account/provider errors.

Queued runs carry server-generated profile digests. A changed/disabled profile
requires a reviewed new request before another API invocation; forged client
digests cannot bypass this guard. Direct queued media tools also pin omitted
source/mask revisions. Dynamic agent/workflow tool calls select their input at
invocation unless an explicit revision is supplied. Credential values are never
persisted in these guards.

Before and after provider work, execution rechecks current Nexus access and
cancellation. Cancelled/revoked work cannot publish a successful artifact; it
cannot undo provider-side processing or billing. Requests are single-attempt.
Existing durable tool receipts retain uncertain-effect handling after a crash.
No image/audio bytes appear in traces. Generated files are immutable private
artifacts marked with generated_by_run and explicit revision references.
Existing safe preview/download and conversation structured-content paths render
results and recheck identity. Reopening uses stored artifacts, never regeneration.

Catalog inspection performs no credential resolution, service probe, download
or paid action. Billing explicitly says separate API account; coding subscription
usage is not a media API entitlement. No profile is enabled by installation.
