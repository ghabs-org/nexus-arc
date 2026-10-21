# Integration requirements and setup

Open **Tools → Integration catalog**. Every supported built-in/community entry
now shows its billing basis and an expandable **Requirements and setup** section.
It describes required credentials, models, software, configuration selections and
installation instructions. Templates contain references rather than secret values.
The catalog preserves all 27 supported entry identities and four agreed vendor
exclusions. SQL agent/tool entries explicitly share one implementation.

The same passive metadata is reusable through `PluginSetup`, the optional `setup`
field on `PluginSpec`/`make_plugin_spec`/`register_factory`, and
`PluginRegistry.catalog()`. Existing plugin constructors, factories, lifecycle
hooks and registration calls retain their behavior. Catalog inspection does not
instantiate plugins or call their health/lifecycle hooks.

## What the status means

- **Setup required:** select configuration, permissions and credentials through
  existing Nexus controls. Listing alone never enables a resource.
- **Configuration present:** the existing tool configuration is present. Account
  access, credentials, installed worker images/models and availability remain
  unverified until an explicit execution/health check.
- **Dependencies missing:** an optional Python distribution is absent in the
  server environment. The catalog tool-run control stays disabled; setup remains
  readable. Normal server authorization and runtime checks still apply.

Installed distribution/version facts come from Python package metadata. They do
not import optional packages, choose an account, resolve credential references,
read environment values, launch programs, access Docker or contact a service.
An installed version differing from the installation pin is reported faithfully;
its compatibility is not automatically certified. Dependency checks do not replace
account, resource and selected-tool permission checks.

Instructions are display-only. Review and run an installation separately, then
review the resulting connection/configuration before enabling it. Worker images
must be built explicitly and selected by immutable ID. Model weights must already
be installed and selected. See [isolated workers](STUDIO-DATA-WORKERS.md).

## Templates and installation pins

Connector templates start disabled. Replace `YOUR_...`, `SELECT_...` and similar
selection placeholders before saving through existing validators. SQL dialect and
its matching driver remain explicit. A template does not grant execution access.
Remote MCP accounts start with discovery only and no selected callable tools.
Writes/sends retain their existing reviewed approval path.

Python installation instructions pin the selected optional adapter packages.
Filesystem MCP pins `@modelcontextprotocol/server-filesystem@2.0.0`, with an
explicit installed executable and workspace root. Studio never invokes `npx` to
fetch a missing server. These are reproducible setup selections, not guarantees
about external service entitlement or every optional dependency combination.
SQL Server and Oracle installation pins are now included. Reviewed-build local
activation checks are described in [activation setup](STUDIO-ACTIVATION-SETUP.md);
complete per-entry activation readiness remains separate acceptance work.

Coding subscriptions cover their selected CLI provider. Search/media/cloud/email
services require their own selected accounts or explicitly configured local
services. Catalog billing text makes that separation visible before setup.
Credentials stay in existing server-managed account storage or environment
references; never paste values into a configuration template.

## API and compatibility

`GET /api/studio/integrations` adds `setup_metadata` and `readiness` to each
supported row. Existing fields, authentication and configure routes are preserved.
No state migration or persisted resource rewrite is required. Plugin `setup`
defaults to `None`; older plugins remain readable. The frontend also tolerates
older servers without setup metadata. Public shell cache advances to version 37;
private API responses remain outside that cache.

The guide and templates add no frontend build dependency. Setup details use native
keyboard-accessible disclosures and wrapping templates at phone/tablet/desktop
widths. The framework consumer is
`examples/nexus-bot/src/studio_catalog_setup.py`; it inspects plugin and Studio
metadata in disposable state without configuring accounts or running tools.

This is passive setup coverage. It does not complete per-entry semantic invocation
acceptance, full activation readiness, account validation or the whole M7 catalog.
