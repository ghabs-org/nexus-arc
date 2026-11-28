# Decision image inputs

Explicit workflow decision `images` entries resolve artifact references. Owned
artifact revisions pin when queued; image bytes go only to explicitly selected
OpenAI gpt-6-luna or OpenRouter openai/gpt-6-luna-decisions. No remote image URL
fetch. PNG/JPEG/WebP determined from content. Defaults 5 images/20 MiB; configurable
ceilings 128/64 MiB. Zero selects defaults. Stored sizes checked before reading;
long side above 8000 downscales, but such images above 40 MP fail input_too_large.
Missing/non-image/corrupt artifacts fail, never provider-error escalation.

OpenAI emits user content input_text/input_image; OpenRouter emits plain text and
image_url parts in top-level state. Existing text calls retain request formats.
