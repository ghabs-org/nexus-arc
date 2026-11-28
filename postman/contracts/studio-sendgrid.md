# SendGrid catalog contract

Source authority: official [Mail Send](https://www.twilio.com/docs/sendgrid/api-reference/mail-send/mail-send)
and the frozen community `send-grid` README at commit
`f3a733c1470fc79d7dbc7fc21ddeab077f4645f4`.

The maintained selected-operation schema is `SENDGRID_SPEC` in
`nexus/core/studio_recipes.py`. It uses the existing OpenAPI connector; this change
adds no endpoint or second mail transport. Version 2 adds bearer-auth declaration,
recipient/sender/content validation, template-only composition and recipient-level
subjects. Existing persisted specifications remain unchanged.

Required behavior:

- Configuration yields a disabled connector and credential reference only.
- The selected operation is `POST /v3/mail/send`, named `sendMail`.
- Ordinary calls require an enabled connector and explicit external-effect review.
- Read probes and model-tool self-approval cannot send mail.
- Invalid composition fails before network access.
- One reviewed invocation emits one request with the scoped bearer key.
- Plain text/HTML and existing provider templates preserve the input JSON.
- Empty HTTP 202 yields `{}`. Acceptance must never be presented as delivery.
- Rejection and redirect yield a sanitized error; no retry or redirect is followed.

Run `postman/simulations/studio-sendgrid.sim.yaml` locally and execute
`tests/studio_sendgrid_acceptance.py`. Its example consumer exercises real Nexus
connector code over HTTP, not a mocked connector function. The fixture's
`GET /fixture/attempts` is test-only instrumentation, not a claimed SendGrid API.
Its 429 and 307 cases use synthetic subject markers, not real provider behavior.
Other operations explicitly return 501.

This mock was authored from the vendor contract. The CLI has no signed-in account,
so an owner's published mock could not be searched. Orbit discovery returned 403;
no provider credentials or external mail accounts were used. Live sender identity,
quota, template definitions, delivery and engagement remain separately pending.
