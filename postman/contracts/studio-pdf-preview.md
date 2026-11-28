# PDF preview failure contract

Existing authenticated `GET /api/catalog/artifacts/{id}/preview` behavior stays read-only.
Successful PDF previews retain pages, text, immutable revision and existing fields.
Unavailable extraction returns the existing preview envelope with
`extraction_available: false`, empty text and a safe message. It now also includes
an additive `error_code`: `pdf_timeout`, `pdf_dependency_missing`, `pdf_unreadable`,
`pdf_worker_unavailable`, `pdf_worker_terminated`, `pdf_worker_failed`, or
`pdf_invalid_response`.

No raw stderr, exception values, document bytes, credentials or server paths are
returned. Missing dependencies alone receive installation guidance. Timeouts do
not imply a missing SDK. Extraction failure never switches providers or sends the
file to an external API. Worker wall/CPU/memory/input limits stay in force.

Real local acceptance must cover valid, damaged and encrypted PDFs through the
worker and preview service. Controlled subprocess failures verify timeout,
termination, launch and malformed-output classification. These are worker failure
injections, not simulations of an external HTTP service. The relocated native
bundle must exercise the same worker dispatch without host Python.

PDF startup isolation follow-up: the source extractor now runs its trusted script
with Python isolated mode, avoiding Nexus/provider imports. Frozen builds dispatch
the allowlisted PDF worker from the trusted packaged script before loading Studio.
Successful JSON/page-selection behavior, authorization and all extraction limits
remain unchanged. No new route, credentials, automatic retry or provider fallback
is introduced. Historical timeout evidence must remain separate from the confirmed
provider-import coupling regression and its fix.
