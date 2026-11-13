"""Read-only operational summaries built from existing scoped records and services."""

from nexus.core.studio_configuration import digest


def resource_versions(document):
    """Compare resource identity and content without returning configuration values."""
    result = {}
    for item in document.get("definitions", []):
        kind = item["collection"]
        result[f"{kind}:{item['id']}"] = digest(item["document"])
    for item in document.get("resources", []):
        kind = item["kind"]
        result[f"{kind}:{item['id']}"] = digest(
            {k: item.get(k) for k in ("kind", "id", "name", "description", "data")}
        )
    return result


class StudioOverview:
    recent_limit = 20

    def __init__(self, studio):
        self.studio = studio

    def visible(self, collection, actor):
        records = self.studio.records(collection, limit=None)
        return sorted(
            (r for r in records if not self.studio.team or self.studio.team.visible(r, actor)),
            key=lambda r: (r.get("updated_at") or r.get("created_at") or "", r["id"]),
            reverse=True,
        )

    def comparison(self, releases):
        if len(releases) < 2:
            return None
        after, before = releases[:2]
        old, new = resource_versions(before["document"]), resource_versions(after["document"])
        return {
            "from_release_id": before["id"],
            "to_release_id": after["id"],
            "added": sorted(new.keys() - old.keys()),
            "removed": sorted(old.keys() - new.keys()),
            "changed": sorted(key for key in old.keys() & new.keys() if old[key] != new[key]),
        }

    def summary(self, actor=None):
        studio = self.studio
        feedback = studio.collaboration.list_feedback(actor)
        reports = self.visible("evaluation-reports", actor)
        builds = self.visible("builds", actor)
        releases = self.visible("configuration-releases", actor)
        runs = {run["id"]: run for run in self.visible("runs", actor)}
        evaluations = []
        for report in reports[: self.recent_limit]:
            evaluations.append(
                {
                    key: report.get(key)
                    for key in (
                        "id",
                        "name",
                        "suite_id",
                        "suite_revision",
                        "created_at",
                        "passed",
                        "total",
                        "complete",
                        "acceptance",
                        "experiment_id",
                    )
                }
                | {"status": runs.get(report["id"], {}).get("status", "unknown")}
            )
        items = []
        for build in builds[: self.recent_limit]:
            item = {
                "id": build["id"],
                "name": build.get("proposal", {}).get("name", build["id"]),
                "revision": build.get("revision"),
                "status": build.get("status"),
                "current_release_id": build.get("release_id"),
                "health": None,
                "health_status": "not_checked",
            }
            if build.get("status") == "saved":
                try:
                    lifecycle = studio.resource_lifecycle.inspect_build(build["id"], actor)
                    item["health"] = lifecycle.get("health_summary")
                    item["health_status"] = "available"
                except (ValueError, FileNotFoundError, KeyError):
                    item["health_status"] = "unavailable"
            history = [r for r in releases if r.get("metadata", {}).get("build_id") == build["id"]]
            try:
                item["comparison"] = self.comparison(history)
                item["comparison_status"] = (
                    "available" if item["comparison"] else "insufficient_history"
                )
            except (ValueError, KeyError, TypeError):
                item["comparison"] = None
                item["comparison_status"] = "unavailable"
            items.append(item)
        return {
            "format": "nexus-studio-overview/v1",
            "recent_limit": self.recent_limit,
            "scope": "workspace" if studio.team and actor["role"] == "owner" else "account",
            "feedback": {
                "total": len(feedback),
                "helpful": sum(r["rating"] == 1 for r in feedback),
                "needs_improvement": sum(r["rating"] == -1 for r in feedback),
            },
            "evaluations": {
                "total": len(reports),
                "completed": sum(
                    runs.get(r["id"], {}).get("status") == "completed" for r in reports
                ),
                "items": evaluations,
            },
            "builds": {"total": len(builds), "items": items},
        }
