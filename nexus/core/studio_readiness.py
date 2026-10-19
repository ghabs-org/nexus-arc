"""Deployment facts, separated from checks that require live infrastructure."""

from __future__ import annotations

from datetime import UTC, datetime


def deployment_readiness(studio, actor):
    if studio.team and (not actor or actor["role"] != "owner"):
        raise PermissionError("Deployment checks require the owner role")
    items = []

    def check(name, status, detail):
        items.append({"name": name, "status": status, "detail": detail})

    check(
        "Workspace mode",
        "configured",
        "Authenticated team workspace" if studio.team else "Local, single-user workspace",
    )
    check(
        "Mobile access",
        "configured" if studio.team else "needs_setup",
        "HTTPS origin configured. Verify the published address from your phone."
        if studio.team
        else "Phone access needs an authenticated team HTTPS deployment. Desktop localhost is local to this computer.",
    )
    if studio.sql_state:
        try:
            from sqlalchemy import text

            with studio.sql_state.engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            check(
                "Shared run state",
                "checked",
                "Database responded to a read-only query. Cross-host failover still needs deployment validation.",
            )
        except Exception:
            check("Shared run state", "failed", "Database read failed. Check server configuration.")
    else:
        check(
            "Shared run state",
            "needs_setup" if studio.team else "configured",
            "File state requires one server process. Multiple controllers need SQL state and shared file storage.",
        )
    transport = studio.mesh_status()
    check("Event transport", "configured", str(transport.get("transport", "Configured transport")))
    image = studio.team.runner_image if studio.team else None
    check(
        "Subscription workers",
        "configured" if image else "needs_setup" if studio.team else "configured",
        "Pinned worker image configured; verify it on each host."
        if image
        else "Configure a pinned CLI worker image for team subscriptions."
        if studio.team
        else "Uses local provider setup. Account availability is verified when executing.",
    )
    for name, detail in [
        (
            "Published HTTPS and login",
            "Run the deployment verifier against the published origin using a real Nexus session.",
        ),
        (
            "Live integrations",
            "Check each real account and permitted action. No live messages or posts are sent by these checks.",
        ),
        (
            "Phone verification",
            "Test Safari and Chrome on real devices, including keyboards, installation, reconnecting and approvals.",
        ),
        (
            "Recovery and backups",
            "Verify shared volumes, expired workers, database/object backups and restoration in your deployed environment.",
        ),
    ]:
        check(name, "not_verified", detail)
    return {"checked_at": datetime.now(UTC).isoformat(), "items": items}
