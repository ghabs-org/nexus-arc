# OpenAPI contract handling

Selected OpenAPI 3.0 and 3.1 operations support bounded local path, parameter,
request-body and schema references. No external schema retrieval occurs.
Operation parameters override path parameters by location/name; duplicate entries
within one scope fail import. Configured authentication must satisfy a declared
security alternative before saving and again before invoking. API-key headers,
query credentials and HTTP Basic/Bearer remain account-scoped. Externally managed
OAuth access tokens can be supplied by Bearer reference; resource servers enforce
their scopes. This does not claim automatic OAuth consent or refresh for OpenAPI.

Existing flat parameter names remain valid. Duplicate names across locations
require `path:name`, `query:name` or `header:name`. Query arrays support form,
spaceDelimited and pipeDelimited; query objects support form/deepObject. Path and
header values use simple serialization. JSON parameter content and JSON request
bodies validate against their declared schemas. Nested OpenAPI 3.0 nullable and
exclusive bounds normalize without modifying examples/default data.

Unsupported parameter/body formats are reported before save. Operation parameters
cannot replace credential or transport headers, or collide with query credentials.
URLs remain on the configured origin; redirects and unapproved mutations remain
blocked. Existing reviewed invoke/approval behavior is unchanged.

`tests/test_studio_openapi_semantics.py` includes real HTTP serialization checks.
`examples/nexus-bot/src/studio_extended_integrations.py` exercises local references
and declared security through a runnable Postman peer. Vendor-account acceptance,
other media types and broader schema/serialization constructs remain separate
from this verified JSON-oriented profile.
