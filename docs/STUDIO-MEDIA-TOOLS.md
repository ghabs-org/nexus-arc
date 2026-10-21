# Configured image and audio tools

Studio provides four explicitly selected media operations through its existing
Tools surface, agent tool grants and workflow nodes:

- `image_generate`: create one image from a prompt.
- `image_edit`: edit one selected image, optionally with a matching PNG mask.
- `image_describe`: describe one selected image.
- `audio_describe`: describe one selected PCM WAV recording.

These tools use the configured API or compatible local endpoint. They do not
use a coding CLI subscription as media API authentication. Existing Codex and
Claude Code conversation/attachment support stays intact. No profile is enabled
by installing this feature, browsing Tools or opening the integration catalog.

## Configure a profile

Use the existing private runtime JSON file outside the shared workspace, with
mode 0600. Add a named mapping under `media`. Every profile requires an explicit
model and action. Replace model placeholders with models supporting each chosen
operation, then enable only the profiles you intend to use:

```json
{
  "media": {
    "make-images": {
      "action": "image_generate",
      "model": "YOUR_IMAGE_MODEL",
      "enabled": false,
      "token_env": "MEDIA_API_KEY"
    },
    "edit-images": {
      "action": "image_edit",
      "model": "YOUR_IMAGE_MODEL",
      "enabled": false,
      "token_env": "MEDIA_API_KEY"
    },
    "read-images": {
      "action": "image_describe",
      "model": "YOUR_VISION_MODEL",
      "enabled": false,
      "token_env": "MEDIA_API_KEY"
    },
    "read-audio": {
      "action": "audio_describe",
      "model": "YOUR_AUDIO_MODEL",
      "enabled": false,
      "token_env": "MEDIA_API_KEY"
    }
  }
}
```

Default URL is `https://api.openai.com/v1`; default credential reference is
`OPENAI_API_KEY`. Neither default enables a profile or selects a model.
For a selected compatible endpoint, set `url` explicitly. Private HTTP requires
`allow_private: true`. The service must implement the wire formats described
below; a generic OpenAI-compatible label alone does not establish support.

Profiles can instead use shared `auth` settings with environment references for
Bearer, API-key or Basic headers, or explicit `{"type":"none"}` for an operator
selected unauthenticated endpoint. Secrets remain in existing Nexus per-user
credential storage or environment references. Shared workers never fall back to
host credentials. Catalog inspection does not resolve credentials or probe models.

`timeout` optionally sets a 1–180 second read timeout/response-stream budget.
Connection timeout remains five seconds. Provider work may continue after local
cancellation or a lost response; cancellation never claims to undo provider usage.
No automatic retry, alternate provider or alternate model is attempted.

The Tools readiness payload lists enabled profile IDs/models and distinguishes
API billing from coding subscriptions. Local setup readiness does not prove
account entitlement, selected model support or live service availability.

## Invoke a tool

Tool IDs are workspace-specific and end with the operation name. Select one in
Tools, grant it explicitly to an agent, or use an existing workflow tool node.
For generation:

```json
{
  "profile": "make-images",
  "prompt": "A simple blue geometric illustration",
  "size": "1024x1024",
  "background": "transparent"
}
```

For editing or description, select an owned file and optional revision:

```json
{
  "profile": "edit-images",
  "prompt": "Change the blue shape to green",
  "artifact_id": "my-image",
  "revision": 1,
  "mask_artifact_id": "my-mask",
  "mask_revision": 1
}
```

Masks are optional and must be PNG with the input image dimensions. Both current
and historical files pass existing authorization. A missing/unauthorized source
has the same generic error. URLs, filesystem paths and arbitrary provider options
are not accepted as inputs.

Direct queued tool runs pin omitted source/mask revisions before dispatch.
Dynamic agent/workflow calls select input at invocation unless a revision is
explicit. All newly queued runs carry server-generated media profile digests.
Configuration drift or disabling a profile requires reviewing a new request;
a queued job cannot silently switch models/endpoints. Credential rotation still
uses the current user's configured secret values. Client-supplied digests cannot
bypass these guards.

## Results and limits

Generated/edited output is a validated private PNG artifact with immutable
revision, `generated_by_run` provenance, model/profile identity and available
nonnegative token usage. Original input references identify exact revisions and
digests. No provider-returned URL is downloaded. Invalid/nonfinite responses,
refusals, incomplete descriptions and malformed media fail without publication.

Image limits: 16 MiB, single-frame PNG/JPEG/WebP, at most 40 million pixels and
8000 pixels on either edge. Images require `nexus-arc[studio-images]`; missing
validation software fails before API invocation. Generated output is PNG only,
one image per call. Supported size options are auto, 1024x1024, 1024x1536 and
1536x1024. Background options are auto, opaque and transparent. Model-specific
support remains the selected provider's responsibility.

Audio description accepts complete PCM WAV up to 4 MiB and 120 seconds, with
one/two channels, 8–192 kHz and 1–4 sample bytes. Browser WebM/MP3 are not silently
converted. Existing transcription/speech-generation profiles remain separate.
Prompts allow 1–4000 characters, descriptions 32 KiB, and HTTP JSON responses
24 MiB. These are deliberate bounded profiles rather than every upstream media
option or format.

Before and after provider work, execution rechecks identity and cancellation.
Revoked/cancelled work cannot publish successful artifacts. Existing durable
workflow tool receipts preserve uncertain-effect handling after interruption;
recovery never automatically repeats an unknown paid request. Traces contain
operation/model/profile metadata, not image/audio bytes or credentials.

Generated-file links survive final typed workflow output and completed chat
history. Existing sanitization, pinned previews and authenticated downloads render
results. Reopening never regenerates media. Public sharing still requires a
separate explicit snapshot/artifact selection. Existing plain-text `output` and
structured JSON workflow results remain compatible.

No bulk state migration, new authentication service or frontend build is required.
Optional metadata is additive. Previously queued jobs without media guards remain
readable; new jobs receive server-generated guards. No user runtime settings,
accounts or running Studio were changed during implementation.

## Wire profile and evidence

Generation uses `/images/generations`; editing uses the current JSON
`/images/edits` shape with inline `images[].image_url` and optional inline mask.
This profile targets GPT Image or endpoints implementing that same contract;
legacy multipart-only image endpoints are unsupported. Description uses
`/chat/completions` with inline image or WAV input, `store=false`, bounded
completion tokens and text-only audio output. The selected model must support it.

Contracts inspected on 21 October 2026: [image generation](https://developers.openai.com/api/docs/guides/image-generation),
[JSON image edit](https://developers.openai.com/api/reference/resources/images/methods/edit),
[image input](https://developers.openai.com/api/docs/guides/images-vision), and
[audio input](https://developers.openai.com/api/docs/guides/audio-chat-completions).
No live account/model quality or entitlement is claimed by local fixtures.

Run the account-free consumer:

```sh
PYTHONPATH=. python examples/nexus-bot/src/studio_media.py
```

It invokes all four tools in an actual Nexus workflow with synthetic media and an
injected local client. It sends no external HTTP requests or paid model calls.

For independent HTTP acceptance, start the owned local Postman simulation:

```sh
postman simulation run postman/simulations/studio-media.sim.yaml --port 49830 --output ndjson
```

In another terminal:

```sh
NEXUS_MEDIA_SIM_URL=http://127.0.0.1:49830/media/v1 PYTHONPATH=. python -m pytest tests/studio_media_http_acceptance.py -q -o addopts=''
```

Stop the simulation with Ctrl-C afterward. Fixtures exercise actual request
construction, authentication, inline files, result validation, quotas, redirects,
timeouts, response budgets and the full workflow consumer. They are synthetic
stand-ins, not vendor-account or independent SAM-runtime acceptance. Physical
devices and the personal HTTPS VM remain separate operational checks.

Local interface contract: [studio-media.md](../postman/contracts/studio-media.md).
