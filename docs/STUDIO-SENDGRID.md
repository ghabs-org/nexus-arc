# Reviewed SendGrid email

The community SendGrid catalog entry configures the existing Nexus OpenAPI
connector. It supports plain text, HTML, per-recipient subjects and provider
email templates. It never installs a package or sends a message during browsing,
configuration, discovery or connectivity inspection.

1. Configure **SendGrid** in the integration catalog. The new connector is disabled.
2. Store the API key through existing scoped credentials using the selected
   environment reference (`SENDGRID_API_KEY` by default). Use a SendGrid-verified
   sender identity and mail-send permission.
3. Inspect the selected `sendMail` operation. Explicitly enable the connector.
4. Supply `action: invoke`, `operation_id: sendMail` and the JSON message body.
   Review the external effect through an approved integration workflow node or
   explicit Studio action. Agent tools cannot self-authorize with `confirm: true`.

A basic body is:

```json
{
  "from": {"email": "verified-sender@example.com"},
  "personalizations": [{"to": [{"email": "recipient@example.com"}]}],
  "subject": "Reviewed notification",
  "content": [{"type": "text/html", "value": "<p>Your report is ready.</p>"}]
}
```

For an existing provider template, replace `subject` and `content` with
`template_id` and put `dynamic_template_data` inside each personalization.
Provider template configuration must supply any omitted subject/content.
Additional SendGrid fields, including tracking settings and attachments, remain
available through the same JSON body. Provider-specific combinations and account
policies are validated by SendGrid. Nexus validates composition structure and
bounded action arguments; it does not claim complete SendGrid payload validation.

The provider's empty HTTP 202 response yields `{}` through the existing connector
contract. This means acceptance for processing, not successful delivery. Provider
rejections and redirects become the existing sanitized integration error. Nexus
does not automatically retry a mail mutation. Check the recorded remote outcome
and provider activity before explicitly retrying an uncertain send. Delivery and
engagement tracking remain in SendGrid; no tracking webhook is added by this entry.

## Local acceptance

Run from the repository root using an environment with the Studio connector extra:

```sh
postman simulation run postman/simulations/studio-sendgrid.sim.yaml --port 49871 --output ndjson
```

In another terminal:

```sh
PYTHONPATH=. python examples/nexus-bot/src/studio_sendgrid.py
PYTHONPATH=. python -m pytest tests/test_studio_sendgrid.py tests/studio_sendgrid_acceptance.py -q -o addopts=''
```

The consumer creates a disposable workspace and maps only its connector URL to
loopback. Only synthetic credentials/addresses are used. It checks HTML and
provider-template acceptance; unreviewed, model-self-approved and malformed sends
produce zero outbound mail requests. Rejection and redirect produce exactly one
attempt each. The local fixture emits no email and uses official documented
request/response shapes. Owner-mock discovery was unavailable because the Postman
CLI is signed out. Orbit discovery returned HTTP 403; the official vendor
contract and frozen plugin README supplied the source evidence.

## Compatibility and scope

New catalog configurations use mail contract version 2. Existing saved connectors
and configuration exports retain their exact versioned specification and are not
silently rewritten. Recreate or explicitly edit an older connector to adopt the
stronger schema. No state migration, frontend build, credential migration or new
provider adapter is needed. The generic OpenAPI response shape remains unchanged.

Real-account sender verification, permissions, quota, template contents,
deliverability and engagement reports remain live validation pending. This is
local semantic acceptance of the catalog entry, not completion of milestone 7.

Sources:

- Frozen plugin README (archived audit evidence)
- [Official Mail Send contract](https://www.twilio.com/docs/sendgrid/api-reference/mail-send/mail-send)
- [Official error contract](https://www.twilio.com/docs/sendgrid/api-reference/mail-send/errors)
