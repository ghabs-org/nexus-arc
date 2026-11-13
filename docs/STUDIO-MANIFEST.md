# Portable Declarative Manifest Compatibility

## Overview

Milestone 10 delivers bidirectional translation for portable declarative manifest files (`kind: manifest`). Declarative manifests allow packaging and distributing related workspace resources—including agents, workflows, connectors, toolsets, skills, entrypoints, datasets, evaluators, experiments, and models—into cohesive bundles targeted at specific runtime environments (`desktop` or `personal-vm`).

## Manifest Specification

A manifest file is a YAML document with `kind: manifest`:

```yaml
kind: manifest
name: support-triage
description: Support-ticket triage bundle for Acme Help Desk
target:
  name: desktop
variables:
  API_HOST: 127.0.0.1
  API_PORT: "8950"
  TIMEOUT_SECONDS: "45"
resources:
  connectors:
    - helpdesk-api
  toolsets:
    - sla-clock
  skills:
    - support-playbook
  agents:
    - ticket-triage
  workflows:
    - ticket-intake
  entrypoints:
    - helpdesk-webhook
  datasets:
    - triage-cases
  evaluators:
    - fields-judge
  experiments:
    - triage-regression
  models:
    - ticket-router
```

### Supported Resource Groups

The manifest supports 10 resource groups mapping directly to Studio definitions and catalog kinds:

1. `connectors`: Integrations, REST endpoints, SQLite connections.
2. `toolsets`: Bundled execution tools.
3. `skills`: Domain instructions and SOP playbooks.
4. `agents`: Autonomous agent definitions.
5. `workflows`: Directed acyclic graph execution flows.
6. `entrypoints`: Inbound webhooks, channel gateways, or scheduled triggers.
7. `datasets`: Evaluation cases and golden test sets.
8. `evaluators`: Rubrics, judges, and quality scoring definitions.
9. `experiments`: Variant matrices and comparative threshold tests.
10. `models`: Language model and provider configurations.

Items within each group can be string identifiers referencing existing resources in the catalog or accompanying documents in a multi-document YAML stream, or inline object specifications.

### Target Environments

The `target.name` specifies the target execution environment:
- `desktop`: Local standalone application environment.
- `personal-vm`: Private VM or server deployment environment.

Inline resources or manifests can specify `environment` or `targets` to conditionally include resources based on the deployment target.

### Variable Interpolation

String values in manifest documents support shell and environment variable expansion:
- `${VAR_NAME}`: Resolves to the value in the manifest's `variables` block, request parameters, or system environment.
- `${VAR_NAME:-default_value}`: Resolves to `default_value` if the variable is unset.
- Missing variables without defaults produce structured diagnostics (`code: missing_variable`) without raising unhandled exceptions or leaking secret values.

## Bidirectional Translation

### Manifest to Native Configuration Release (`convert-manifest`)

Translates a portable manifest into a native `nexus-studio-config/v1` document:
- Resolves all resource references from current workspace catalog or accompanying documents.
- Interpolates variables across all definition and resource fields.
- Validates target environment compatibility.
- Prepares the document for standard non-executing review via `nexus config plan`.

### Native Configuration to Manifest (`export-manifest`)

Translates a native configuration release or the current active workspace into a portable manifest:
- Groups definitions and catalog resources into their respective resource categories.
- Formats output conforming to the standard `kind: manifest` schema.
- Emits clean YAML ready for version control or distribution.

## CLI Usage

```sh
# Convert manifest to native release document with optional variable overrides
nexus config convert-manifest manifest.yaml --env-file env.yaml --target desktop -o converted.json

# Review and apply through standard Studio configuration controls
nexus config validate converted.json
nexus config plan converted.json -o reviewed-plan.json
nexus config apply reviewed-plan.json -o release.json

# Export current workspace to a portable declarative manifest
nexus config export-manifest --target personal-vm -o exported-manifest.yaml
```

## HTTP API

- `POST /api/studio/configuration/manifest/convert`:
  - Body: `{"text": "<yaml_string>", "variables": {...}, "environment": "desktop"}`
  - Returns: `{"valid": bool, "diagnostics": [...], "document": {...}}`
- `POST /api/studio/configuration/manifest/export`:
  - Body: `{"document": {...}, "environment": "desktop", "name": "my-manifest"}`
  - Returns: `{"valid": bool, "diagnostics": [...], "manifest": {...}, "yaml": "..."}`

Both endpoints require team owner authorization in team workspaces and local origin/CSRF protection.

## Safety & Invariants

1. **Parser Safety**: YAML parsing utilizes `SafeManifestLoader`, enforcing strict node count (<=20,000) and depth (<=50) limits and rejecting YAML aliases and anchors.
2. **Payload Bounds**: Maximum manifest document size is 1 MiB; environment variable files are bounded at 64 KiB.
3. **Secret Redaction**: Diagnostic reports disclose failing paths and variable names, but never leak resolved passwords, bearer tokens, or sensitive values.
4. **Non-Executing Review**: Manifest conversion produces plans only; resources are never activated or created without explicit `apply`.
