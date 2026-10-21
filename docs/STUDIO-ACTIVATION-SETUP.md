# Local prerequisites before build activation

Quick Build's existing **Resources & readiness** view now lists **Software and
worker setup** for referenced components. It checks local SDK package metadata,
explicit tool configuration and worker-profile/model prerequisites. Expanding a
saved build does not start a container or contact a provider.

**Review activation** additionally runs fixed software checks in the selected
isolated workers. These checks use the existing Docker sandbox: immutable installed
image, no image pull, no network, non-root user, read-only root filesystem,
bounded resources and only an explicitly selected read-only model mount. The
checks never run workflow instructions, registered tool actions, input files or
model inference. No generated artifact is retained. Worker checks are reused
within one inspection when multiple components reference the same action/profile.

Missing packages, disabled/unconfigured tools, missing Docker/profile/model/image
or missing worker software block the reviewed activation. Chart workers require
the tested minimum of 256 processes and 256 open files; global upper bounds remain
512 processes and 1024 open files. Other worker profiles retain existing defaults.

The selected SQL dialect determines its required driver. SQLAlchemy and the actual
PostgreSQL/MySQL/MariaDB/SQL Server/Oracle driver must be installed. MCP,
Bedrock/MongoDB, Slack, image tools and MCP entrypoints check their selected local
software prerequisites. Existing explicitly injected adapter implementations keep
their dependency ownership. Credential presence, delegated OAuth state and Nexus
execution permissions still use the existing lifecycle checks; no second identity
or account store is introduced.

## Review changes before apply

An activation plan records fingerprints of package versions, worker settings and
selected model file metadata. If these change, apply requires a fresh plan before
starting any newly configured worker. Apply also repeats software checks so an
image removed after review cannot activate successfully. Checks remain local setup
facts: they do not certify account entitlement, quota, remote connectivity or
arbitrary model quality. Existing **Check connection** probes remain separate.

Older pending activation plans need fresh review after this upgrade. Completed
transition receipts replay their saved result without repeating worker checks.
Saved definitions, conversations, releases and existing deactivation/rollback
remain readable. No bulk state migration or frontend build is required. The public
shell cache advances to version 37; private responses are never cached there.

This slice covers Quick Build's reviewed activation path. Other direct resource
configuration paths retain existing permission/validation checks; complete
per-entry activation/readiness acceptance remains a separate M7 task.

## Installation and repeat checks

The catalog now lists exact selected SQL Server (`pymssql==2.4.5`) and Oracle
(`oracledb==26.0.1`) installation instructions, verified against their official
PyPI release metadata. Installing a driver does not establish a working database
account or actual query compatibility. Use the explicitly selected driver and
validate its service separately.

[Worker installation](STUDIO-DATA-WORKERS.md) and
[catalog setup](STUDIO-CATALOG-SETUP.md) describe optional dependencies and billing.
The framework example works without Docker to demonstrate a blocked plan:

```sh
PYTHONPATH=. python examples/nexus-bot/src/studio_activation_setup.py
```

To exercise successful real preflight, select an installed image/context:

```sh
PYTHONPATH=. python examples/nexus-bot/src/studio_activation_setup.py \
  --image sha256:YOUR_INSTALLED_ANALYSIS_IMAGE --docker-context YOUR_CONTEXT
```

The example uses a deterministic local proposal provider and temporary workspace.
Its workflow action deliberately raises if executed. Successful activation proves
that only the fixed software probe ran; it never starts that workflow action.

`tests/studio_activation_worker_acceptance.py` is an explicit optional-infrastructure
suite. Set `NEXUS_ACCEPTANCE_WORKER_IMAGE`, `NEXUS_ACCEPTANCE_VISION_IMAGE`,
`NEXUS_ACCEPTANCE_VISION_MODEL`, `NEXUS_ACCEPTANCE_DOCKER_CONTEXT` and
`NEXUS_ACCEPTANCE_BARE_IMAGE` before invoking it. The bare image must be installed
and contain Python without Plotly/Kaleido/Chromium. The suite fails on absent
prerequisites rather than silently skipping them. It checks all seven worker tool
profiles, missing-image/software rejection and the actual example consumer.
