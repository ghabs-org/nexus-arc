"""Side-effect-free inventory of shipped ARC integrations and Studio coverage.

Discovery never loads optional adapters, reads credentials, starts bots, or probes
external services. A catalog entry is not an authenticated connection.
"""

from __future__ import annotations

from copy import deepcopy


def _integration(identity, name, category, summary, capabilities, sources, setup, mode="framework"):
    return {
        "id": identity,
        "name": name,
        "category": category,
        "summary": summary,
        "capabilities": capabilities,
        "sources": sources,
        "setup": setup,
        "studio_support": mode,
    }


_INTEGRATIONS = (
    _integration(
        "teams",
        "Microsoft Teams",
        "Messaging",
        "Read one selected channel, send reviewed messages, and receive mapped task commands.",
        [
            "Channel metadata and paginated messages",
            "Reviewed messages and replies",
            "Delegated work/school Graph account",
            "Verified incoming commands with durable deduplication",
        ],
        ["nexus/core/studio_teams.py", "nexus/core/studio_teams_events.py"],
        "Choose Microsoft Teams in the connector editor, set team/channel IDs and a delegated token reference. Select read/write actions and enable explicitly. Entrypoints also accept selected basic Graph notifications with mapped Microsoft users and a command prefix.",
        "studio",
    ),
    _integration(
        "github",
        "GitHub",
        "Code",
        "Repositories, issues and pull requests.",
        [
            "Read and update issues",
            "Comments and labels",
            "Branches and pull requests",
            "Merge pull requests",
            "API or official CLI transport",
        ],
        ["nexus/adapters/git/github.py", "nexus/adapters/git/github_cli.py"],
        "Create a GitHub connection with a repository and allowed actions. Team runs reuse each user’s Nexus GitHub account. Issue and pull-request actions are available; writes require confirmation or a workflow approval gate.",
    ),
    _integration(
        "gitlab",
        "GitLab",
        "Code",
        "Projects, issues and merge requests.",
        [
            "Read and update issues",
            "Comments and labels",
            "Branches and merge requests",
            "Merge requests",
            "API or official CLI transport",
        ],
        ["nexus/adapters/git/gitlab.py", "nexus/adapters/git/gitlab_cli.py"],
        "Create a GitLab connection with a repository, sign-in host and allowed actions. Team runs reuse refreshed personal Nexus credentials. Issue and merge-request actions require explicit permissions; writes require approval.",
    ),
    _integration(
        "telegram",
        "Telegram",
        "Messaging",
        "Bot conversations and interactive notifications.",
        [
            "Incoming messages and commands",
            "Normalize messages into tasks",
            "Interactive buttons",
            "Send and update notifications",
        ],
        [
            "nexus/adapters/telegram_gateway.py",
            "nexus/plugins/builtin/telegram_interactive_plugin.py",
            "nexus/plugins/builtin/telegram_notification_plugin.py",
        ],
        "Configure a fixed chat connection and a Telegram entrypoint. Register its URL and secret token in Telegram, map sender IDs to Nexus users, and optionally enable results and approval buttons. Saving never changes your bot account.",
    ),
    _integration(
        "discord",
        "Discord",
        "Messaging",
        "Bot commands, interactive replies and publishing.",
        [
            "Bot commands and messages",
            "Interactive replies",
            "Notifications and message updates",
            "Webhook publishing",
        ],
        [
            "nexus/plugins/builtin/discord_interactive_plugin.py",
            "nexus/adapters/notifications/discord.py",
            "nexus/adapters/social/discord_publisher.py",
        ],
        "Configure a fixed bot channel and signed Discord interaction entrypoint for commands and approval buttons. Campaigns also support explicit webhook publishing. Ordinary Gateway messages use the existing ARC bot runtime.",
    ),
    _integration(
        "linkedin",
        "LinkedIn",
        "Social",
        "Account profiles and professional publishing.",
        [
            "Stored Nexus OAuth credentials",
            "Account and expiry status",
            "Read your profile",
            "Validate and publish posts with link metadata",
        ],
        ["nexus/connectors/linkedin.py", "nexus/adapters/social/linkedin_publisher.py"],
        "Reuse the existing Nexus LinkedIn OAuth setup and stored account credentials. Campaign controls now support validation, revision approval and explicit publishing. OAuth account setup and profile management remain external.",
    ),
    _integration(
        "slack",
        "Slack",
        "Messaging",
        "Workspace notifications and interactive messages.",
        [
            "Notifications and alerts",
            "Update messages",
            "Input requests",
            "Interactive plugin integration",
        ],
        [
            "nexus/adapters/notifications/slack.py",
            "nexus/plugins/builtin/slack_interactive_plugin.py",
        ],
        "Configure a fixed Slack channel connection and signed event/interaction entrypoint. Register the incoming URL in Slack and map platform users before enabling replies or approval buttons.",
    ),
    _integration(
        "x",
        "X",
        "Social",
        "Social post validation and publishing.",
        [
            "Validate post content",
            "Publish posts",
            "Thread support",
            "Shared publishing result handling",
        ],
        ["nexus/adapters/social/x_publisher.py"],
        "Configure the existing X publishing adapter with account credentials. Campaign controls now support validation, revision approval and explicit publishing. Account setup remains external.",
    ),
    _integration(
        "meta",
        "Facebook & Instagram",
        "Social",
        "Page and media publishing through Meta.",
        ["Facebook page posts", "Instagram media publishing", "Content validation"],
        ["nexus/adapters/social/meta_publisher.py"],
        "Use existing Nexus Meta OAuth and the publishing adapter with the appropriate page or Instagram account. Campaign controls now support validation, revision approval and explicit publishing. OAuth account setup remains external.",
    ),
    _integration(
        "openclaw",
        "OpenClaw",
        "Messaging",
        "Gateway events and workflow notifications.",
        ["Gateway event handling", "Notifications", "Workflow notification metadata"],
        [
            "nexus/adapters/notifications/openclaw.py",
            "nexus/plugins/builtin/openclaw_event_handler_plugin.py",
        ],
        "Configure a fixed notification destination and an explicitly signed OpenClaw bridge entrypoint. Bridge envelopes map senders to Nexus users. Approval codes are delivered as text; your bridge returns their callback values.",
    ),
    _integration(
        "mcp",
        "MCP servers",
        "Tools",
        "Discover server tools and register them for workflows.",
        ["List tools from a stdio server", "Call server tools", "Register named workflow tools"],
        ["nexus/tools/mcp.py"],
        "Install the mcp extra and configure a trusted local executable in Connections. Discover tools, enable selected names, and mark read-only calls explicitly before giving a saved agent access. Server installation remains an administrator task.",
    ),
    _integration(
        "postgres",
        "PostgreSQL",
        "Storage",
        "Persistent workflows, approvals and audit history.",
        ["Workflow state", "Audit events", "Completion metadata", "Pending approvals"],
        ["nexus/adapters/storage/postgres.py"],
        "Install the postgres extra and use a private runtime configuration with a database environment reference, project namespace and worker count. State, queue leases, approvals and checkpoints persist in SQL. Definitions and local bytes require a shared volume.",
    ),
    _integration(
        "filesystem",
        "Filesystem artifacts",
        "Storage",
        "Reusable artifact storage on local disk.",
        ["Store and retrieve artifacts", "List artifacts", "Delete artifacts"],
        ["nexus/adapters/artifacts/__init__.py"],
        "Private filesystem storage is the default for versioned artifact bytes. Inspect or select storage under Workspace tools → File storage.",
    ),
    _integration(
        "s3",
        "Amazon S3",
        "Storage",
        "Object storage for framework artifacts.",
        ["Store and retrieve artifacts", "List artifacts", "Delete artifacts"],
        ["nexus/adapters/artifacts/__init__.py"],
        "Install the s3 extra, add an S3 profile under File storage, test a temporary object, then activate it for new uploads. Credentials stay in environment references.",
    ),
    _integration(
        "gcs",
        "Google Cloud Storage",
        "Storage",
        "Object storage for framework artifacts.",
        ["Store and retrieve artifacts", "List artifacts", "Delete artifacts"],
        ["nexus/adapters/artifacts/__init__.py"],
        "Install the gcs extra, add and test a Google Cloud Storage profile under File storage, then activate it for new uploads. Configure the service account on the server.",
    ),
    _integration(
        "azure",
        "Azure Blob Storage",
        "Storage",
        "Object storage for framework artifacts.",
        ["Store and retrieve artifacts", "List artifacts", "Delete artifacts"],
        ["nexus/adapters/artifacts/__init__.py"],
        "Install the azure extra, add an Azure profile with a connection-string environment reference, test it, then activate it under File storage.",
    ),
    _integration(
        "mqtt",
        "MQTT",
        "Events",
        "Publish and subscribe to execution metadata.",
        ["Status event publishing", "Topic subscriptions", "Verified TLS in shared Studio"],
        ["nexus/adapters/transport.py"],
        "Choose mqtt in the administrator-owned Studio team configuration, with verified TLS and environment references for secrets. Events carry status metadata. Distributed jobs use the separate SQL-backed queue.",
        "deployment",
    ),
    _integration(
        "kafka",
        "Kafka",
        "Events",
        "Broker-backed execution event transport.",
        ["Status event publishing", "Topic subscriptions", "SSL or SASL_SSL in shared Studio"],
        ["nexus/adapters/transport.py"],
        "Choose kafka in the administrator-owned Studio team configuration, with verified SSL/SASL_SSL and environment references for secrets. Events carry status metadata. Distributed jobs use the separate SQL-backed queue.",
        "deployment",
    ),
    _integration(
        "http",
        "HTTP GET",
        "General",
        "Read a configured HTTP endpoint.",
        [
            "GET requests",
            "Environment-reference bearer tokens",
            "Response limits and redirect protection",
        ],
        ["nexus/core/studio_operations.py"],
        "Create a Studio connection with an endpoint URL and optional token environment-variable name. Enable it explicitly, then run a connection test.",
        "studio",
    ),
    _integration(
        "sqlite",
        "SQLite",
        "General",
        "Read a database inside this workspace.",
        ["Read-only queries", "100-row result limit", "Query execution time limit"],
        ["nexus/core/studio_operations.py"],
        "Create a Studio connection with a database path relative to this workspace. Enable it explicitly, then test a read-only query.",
        "studio",
    ),
)

_CAPABILITIES = (
    {
        "id": "memory",
        "name": "Conversation memory",
        "summary": "Browse, rename and manage existing user conversations and their history.",
        "sources": ["nexus/core/memory.py"],
        "scope": "Browse, rename, archive, restore, export and import conversations. Legacy Redis/filesystem sources require administrator-owned source and identity mappings.",
        "studio_area": "memory",
    },
    {
        "id": "checkpoints",
        "name": "Workflow checkpoints",
        "summary": "Inspect saved step outputs and durable workflow resume state.",
        "sources": ["nexus/workflows/checkpoints.py"],
        "scope": "Inspect canvas and YAML checkpoints, approve gates and resume unfinished steps. Local mode uses SQLite snapshots; SQL deployments keep snapshots in their state database.",
        "studio_area": "checkpoints",
    },
    {
        "id": "agents",
        "name": "Agent coordination & discovery",
        "summary": "Inspect peers, capabilities, heartbeats and task delegation.",
        "sources": ["nexus/agents/peer_registry.py", "nexus/agents/coordinator.py"],
        "scope": "Delegate to 1–4 saved agents sequentially, in parallel or through an autonomous coordinator with an explicit fallback. Optional model routing uses server configuration. External agents support A2A JSON-RPC discovery, tasks, input continuation and cancellation.",
        "studio_area": "agents",
    },
    {
        "id": "campaigns",
        "name": "Publishing campaigns",
        "summary": "Review per-channel drafts, approvals and publishing outcomes.",
        "sources": ["nexus/core/campaign.py", "nexus/plugins/builtin/social_publisher_plugin.py"],
        "scope": "Save per-channel drafts, check content and required account fields, approve an exact revision and explicitly publish once. Uncertain outcomes require manual review. Account setup remains external.",
        "studio_area": "campaigns",
    },
    {
        "id": "analytics",
        "name": "Audit queries & analytics",
        "summary": "Explore execution events, system metrics and agent performance.",
        "sources": [
            "nexus/adapters/analytics/file_audit_query.py",
            "nexus/adapters/analytics/loki.py",
        ],
        "scope": "Explore run counts, statuses, durations and metadata-only audits from local files, SQL state or configured project-scoped Loki queries. Inspect provider, node and tool traces from run details.",
        "studio_area": "analytics",
    },
    {
        "id": "transcription",
        "name": "Audio transcription",
        "summary": "Turn uploaded audio into text for conversations and workflows.",
        "sources": ["nexus/adapters/transcription/whisper_provider.py"],
        "scope": "Upload up to 4 MiB of audio, transcribe using the existing Whisper API provider and reuse text in chat. Requires an OpenAI API key; CLI subscriptions do not cover this API.",
        "studio_area": "transcription",
    },
    {
        "id": "decisions",
        "name": "Typed decisions & routing",
        "summary": "Inspect classification choices, confidence and fallback routes.",
        "sources": ["nexus/adapters/decisions/jev.py"],
        "scope": "Inspect Jev choices, reported confidence and explicit fallback, or use typed decisions and named choice edges in the workflow canvas. Decision APIs require their own configured account.",
        "studio_area": "decisions",
    },
    {
        "id": "budgets",
        "name": "Prompt budgets & launch guards",
        "summary": "Show context truncation and repeated-launch limits.",
        "sources": ["nexus/core/prompt_budget.py", "nexus/core/guards.py"],
        "scope": "Set personal character budgets, preview history compaction and persist matching-task cooldowns. These are not token, subscription-quota or monetary measurements.",
        "studio_area": "controls",
    },
)


def integration_catalog(studio=None):
    """Describe shipped capabilities without claiming account connectivity."""
    connections = studio.catalog.list("connectors") if studio else []
    transport = type(studio.transport).__name__ if studio else ""
    items = deepcopy(list(_INTEGRATIONS))
    for item in items:
        saved = [record for record in connections if record["data"]["type"] == item["id"]]
        item["connection_count"] = len(saved)
        item["enabled_count"] = sum(bool(record["data"]["enabled"]) for record in saved)
        item["active_transport"] = transport == {
            "mqtt": "MqttTransport",
            "kafka": "KafkaTransport",
        }.get(item["id"])
        if item["id"] in {"github", "gitlab", "telegram", "discord", "slack", "openclaw", "mcp"}:
            item["studio_support"] = "studio"
            item["status"] = "Saved connections" if saved else "Ready to configure"
        elif item["id"] in {"linkedin", "x", "meta"}:
            item["studio_area"] = "campaigns"
            item["studio_support"] = "workbench"
            item["status"] = "Campaign controls available"
        elif item["id"] in {"filesystem", "s3", "gcs", "azure"}:
            item.update(
                studio_support="workbench",
                studio_area="storage",
                status="File storage controls available",
            )
        elif item["id"] == "postgres":
            item.update(studio_support="deployment", status="Runtime configuration available")
        elif item["studio_support"] == "studio":
            item["status"] = "Saved connections" if saved else "Ready to configure"
        elif item["active_transport"]:
            item["status"] = "Active event transport"
        elif item["studio_support"] == "deployment":
            item["status"] = "Deployment setup"
        else:
            item["status"] = "Available in ARC"
    return items


def arc_capabilities():
    """Return available Studio controls, with their current limits stated."""
    return deepcopy(list(_CAPABILITIES))
