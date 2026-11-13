# Explicit image evidence for workflow decisions

Decision nodes accept `images`, a list of artifact IDs, filenames, names or
`artifact://NAME:REVISION` references. Imported external templates can resolve to one
reference, a list, or no optional image. Owned artifact revisions are pinned when
the workflow queues; replacing a file afterward does not change queued evidence.
Current and historical ownership are checked before bytes are read and again
before provider dispatch. Remote image URLs are never fetched.

Only the configured OpenAI `gpt-6-luna` or OpenRouter
`openai/gpt-6-luna-decisions` profile accepts these images. No provider/model
fallback occurs. Existing subscription providers and text decisions retain their
behavior. Images are an explicit API call and use the selected account's billing.

Single-artifact tool outputs expose `created_artifact`, `created_artifact_name` and
`created_artifact_version` in imported workflows. Generated files are accepted
only when tagged by this run; their first consumed revision is saved durably.
Multiple-artifact calls do not invent a single created-artifact reference.

Install `nexus-arc[studio-images]` for Pillow. Image type comes from content:
PNG, JPEG and WebP are accepted. Defaults are five images and 20 MiB combined;
`max_images` and `max_image_bytes` in the decision block allow at most 128 and
64 MiB. Zero selects defaults. Stored size is checked before loading. Images
above 8000 pixels on the long side downscale to 8000; if such an image exceeds
40 megapixels it fails as `input_too_large`. Downscaled PNG/WebP becomes PNG.
Invalid, missing, ambiguous or corrupt artifacts fail without escalation. Size
errors follow the existing explicit provider-error escalation policy.

OpenAI receives user-message `input_text`/`input_image` parts; OpenRouter receives
text and `image_url` parts in its top-level state array. Inline bytes are not
stored in decision history; existing artifact storage retains original revisions.
Transport size bounds account for base64 without weakening text-only limits.
The formats follow [OpenAI Decisions](https://developers.openai.com/api/docs/guides/decisions)
and [OpenRouter multimodal Decisions](https://openrouter.ai/docs/guides/community/multimodal-decisions),
inspected 20 October 2026. No live provider invocation was made for acceptance.

Optional acceptance runs explicitly with Pillow installed:

```bash
python -m pytest tests/studio_decision_images_acceptance.py -q -o addopts=''
```

The example `examples/nexus-bot/src/studio_extended_integrations.py` also consumes
an actual private-image workflow through the local Postman image peer.

Optional nil and intentionally pruned image entries are omitted as complete entries.
Failed, unfinished, unresolved and stale-field references fail before dispatch;
branch skip reasons survive checkpoints. Required pure-reference tests cover these
cases separately from optional Pillow/provider-wire acceptance.

Remaining interoperability scope includes broader external artifact URI namespaces,
cross-role handler/body scopes and independent provider acceptance. These are not
marked complete.
