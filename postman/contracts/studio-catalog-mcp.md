# Catalog MCP invocation acceptance

Run `postman simulation run postman/simulations/studio-catalog-mcp.sim.yaml --port 49911 --output ndjson`,
then run `python -m pytest -o addopts='' tests/studio_catalog_mcp_acceptance.py -q`.

The real Nexus recipe/configuration/permission path and installed MCP client SDK
must discover and invoke a selected tool. Tavily uses Streamable HTTP and Bearer
authentication; Bright Data uses legacy SSE with a query credential; Zapier uses
legacy SSE with a credential-bearing path. Credential values remain synthetic.
Disabled connections, unselected tools and unconfirmed external actions must not
invoke a tool. Explicitly confirmed calls retain arguments and returned text.
An error after dispatch must produce exactly one request and never retry the action.

The independent stand-in follows the MCP transport contract:
https://modelcontextprotocol.io/specification/2025-03-26/basic/transports

This checks transport, configuration and invocation, not vendor-specific tool
availability, search quality, tenant authorization or actual external effects.
No owner mock lookup is used: this is a local protocol fixture, not a live vendor mock.
