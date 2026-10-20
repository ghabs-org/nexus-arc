# Selected OpenAPI 3.0/3.1 operations

Local references for paths, parameters, request bodies and schemas remain local;
external references never fetch. Path-level parameters merge with operation-level
parameters by location/name. Security alternatives must match configured Nexus
credential references before saving. Bearer references may hold externally
managed OAuth access tokens; the service enforces the token's actual scopes.

Arguments retain existing flat parameter names. Ambiguous names require qualified
`path:name`, `query:name` or `header:name`. Unknown/missing parameters fail before
network calls. Query arrays support form, spaceDelimited and pipeDelimited;
objects support form and deepObject. Path/header simple serialization is explicit.
Other serialization styles fail during import rather than silently changing data.
JSON request bodies retain schema validation, including nested OpenAPI 3.0
nullable schemas and local component references. Non-JSON bodies are reported as
unsupported before save. Selected mutating operations still require reviewed
invoke; credential headers and query keys cannot be overwritten.
