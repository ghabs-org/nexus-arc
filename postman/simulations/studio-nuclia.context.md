# Nuclia local simulation

Updated 25 October 2026. Shapes come from the pinned vendor models and frozen
tool evidence cited in `../contracts/studio-nuclia.md`; no provider account is used.

## Purpose

Exercise the real Nexus connector, transport and consumers against the single
`nuclia` dependency. Request and failure contracts live in the adjacent
simulation and `../mocks/studio-nuclia/default.js`.

## Execution

Run `postman simulation run postman/simulations/studio-nuclia.sim.yaml --port auto
--no-history --output ndjson`. Take the actual member address from its listening
event. Set `NEXUS_ACCEPTANCE_NUCLIA_URL` to that address, then run
`PYTHONPATH=. python -m pytest tests/studio_nuclia_acceptance.py
tests/studio_nuclia_context_acceptance.py tests/studio_nuclia_attachments_acceptance.py
-q -o addopts=''`. Stop only this simulation after inspecting its request log.

## Assumptions

- Context defaults use the server workspace directory name. The audit consumer
  creates a directory named `workspace`; a different name intentionally fails
  the fixture's selected-context check. (observed 24 October 2026)
- The stand-in validates outbound metadata and request count, not real Nuclia
  audit-log persistence, retention, permissions or account billing. Those need
  separate account acceptance. (reasoned 24 October 2026, the mock handler)

## Observations

<!-- begin:observations -->
25 October 2026: all 26 HTTP checks passed after adding explicit full-retrieval
capture. The sole `nuclia` member served the new consumer and all existing paths;
none stayed silent. Uncited text stayed out of ordinary agent prompts and run
events, then reached only the explicitly selected synthetic judge after promotion.
25 October 2026: all 25 HTTP checks passed. The sole `nuclia` member served all
three citation modes in answer-file and inline variants, including explicit
provider failures without replay. No member stayed silent. These checks verify
identifier-based footnotes, not provider-specific positional context mappings.
24 October 2026: all 19 HTTP checks passed after adding explicit context strategy
preservation. The sole `nuclia` member served both legacy and expanded descriptor
calls; no member stayed silent. Expanded paragraph and title citations produced
private files, and a caller could not remove fixed strategies or cause replay.
24 October 2026: the sole `nuclia` member served search, answers, context,
conversation/original downloads and audit requests. No member stayed silent.
The 18 HTTP checks passed, including audit preflight rejection without traffic
and one provider rejection without replay.
<!-- end:observations -->
