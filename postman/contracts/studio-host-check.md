# Read-only container layout preflight

`nexus studio-host-check --studio-container NAME --proxy-container NAME
--docker-context CONTEXT` inspects one explicitly selected Studio/proxy pair.
Run it on the Docker daemon host, with permission to inspect both containers and
resolve their host mount sources. It adds no HTTP route or stored record.

The framework pins the selected Docker connection with the existing route
snapshot. It fetches only container IDs, running/privilege flags and mount
metadata; it never fetches environment, command arguments or logs. A fixed Python
probe inside the selected proxy reads UID/GID and private-directory/ledger stat
metadata only. It neither opens the ledger nor writes files or changes services.
No shell expansion, agent invocation, resource creation or action replay occurs.

The v1 JSON report contains check IDs, `passed`/`failed`/`unavailable` status,
constant human-readable details and hashed container identities. It omits names,
paths, environment values, credentials, exception text and daemon response bodies.
Exit 0 requires every scoped check to pass; exit 1 covers failed/unavailable checks;
argument validation uses exit 2. Missing runtime metadata never means success.

Checks cover distinct running containers, non-privileged Studio, a dedicated
writable proxy-state mount, canonical host-source separation from every Studio
bind/volume mount, raw-socket exclusion from Studio, and private storage ownership,
mode, link/type and symlink rules under the actual proxy UID. Read-only Studio
mounts count as exposure too. Missing receipts are reported explicitly; a valid
empty store may be configured but is not live ownership/recovery proof.
Nested mounts beneath the private store are rejected: a separate root mount
alone cannot prove that another mount did not replace the ledger file.

Host sources must resolve on the inspection machine. A remote context or Docker
Desktop's inaccessible Linux volume paths therefore yield `unavailable` for
source identity. String comparison alone never certifies alias isolation. This
does not certify actual host co-location, host administrators, socket forwarding,
network policy, TLS/authentication, service readiness, backup/restore or desktop
installation. The operator must run it on the selected daemon host. A successful
report is scoped configuration/stat evidence, not whole-deployment acceptance.

Each target is assessed independently. An unconfigured private-store mount keeps
its isolation unavailable but cannot hide a verified raw-socket isolation result.
A missing/unreadable private directory returns `ledger: unavailable` and a
specific private-ledger result, rather than inventing a generic inspection error.
Overall success still requires all checks to pass.
