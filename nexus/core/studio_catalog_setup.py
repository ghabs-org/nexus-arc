"""Passive catalog setup facts; never resolve credentials or execute plugins."""

import hashlib
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from nexus.plugins.catalog import PluginSetup

SOURCE_REVISION = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
SOURCE = (
    "https://github.com/ghabs-org/nexus-arc/blob/main/nexus/core/studio_catalog_setup.py"
    "#sha256=" + SOURCE_REVISION
)
PINS = {
    "mcp": "2.3.0",
    "boto3": "1.43.116",
    "pymongo": "4.19.0",
    "SQLAlchemy": "2.1.5",
    "jsonschema": "4.26.0",
    "slack-sdk": "3.45.0",
    "yfinance": "0.2.65",
}


def setup_metadata(row):
    identity, mode = row["id"], row["mode"]
    packages, requirements, installation = [], [], []
    billing = "Local Nexus service; selected AI provider billing remains separate."
    template = {"open": row.get("target", "tools")}
    alias = None
    if mode == "mcp":
        packages = ["mcp"]
        requirements = [
            "Provider account and user-scoped token reference.",
            "Discover tools, select permissions and explicitly enable the connection.",
        ]
        billing = (
            "Remote provider account usage; not covered by Codex or Claude Code subscriptions."
        )
        auth = {"type": row.get("auth_type", "bearer"), "token_env": row["token_env"]}
        if auth["type"] == "query":
            auth["parameter"] = "token"
        template = {
            "resource": "connectors",
            "data": {
                "type": "mcp",
                "transport": row["transport"],
                "url": row["url"],
                "auth": auth,
                "tools": [],
                "read_only_tools": [],
                "actions": ["discover"],
                "enabled": False,
            },
        }
    elif mode == "filesystem":
        packages = ["mcp"]
        requirements = [
            "Installed filesystem MCP executable, restricted to this workspace.",
            "Node 24 or later for the pinned server; select its absolute executable and installed server script when Node is outside the system PATH.",
            "Discover/select tools; writes require existing reviewed-action permissions.",
        ]
        installation = [
            "npm install --prefix /YOUR_PRIVATE_MCP_DIRECTORY @modelcontextprotocol/server-filesystem@2.0.0",
            "Choose the absolute Node executable and installed node_modules/@modelcontextprotocol/server-filesystem/dist/index.js script, or a standalone filesystem executable. No npx download occurs at invocation.",
        ]
        template = {
            "configure": {
                "command": "/YOUR_NODE_DIRECTORY/node",
                "server_script": "/YOUR_PRIVATE_MCP_DIRECTORY/node_modules/@modelcontextprotocol/server-filesystem/dist/index.js",
            }
        }
        billing = "Local filesystem service; selected AI provider billing remains separate."
    elif mode == "openapi":
        packages = ["jsonschema"]
        requirements = [
            "SendGrid account with reviewed mail-send permissions and SENDGRID_API_KEY credential reference.",
            "Review the disabled OpenAPI connection; sending requires explicit approval.",
            "Verified sender identity; plain text/HTML content or an existing provider template. Acceptance is not delivery confirmation.",
        ]
        billing = "SendGrid account usage; not covered by coding subscriptions."
        template = {"configure": {"token_env": "SENDGRID_API_KEY"}}
    elif mode == "connection":
        target = row["target"]
        data = {"type": target, "enabled": False}
        billing = "Selected service/account usage; AI provider billing remains separate."
        if target == "bedrock":
            packages = ["boto3"]
            requirements = [
                "AWS region, Bedrock resource/alias IDs and scoped AWS credential references.",
                "AWS account access to the selected Agent, Flow or knowledge base; invocation requires review.",
            ]
            data.update(
                region="YOUR_REGION",
                resource_id="YOUR_RESOURCE_ID",
                alias_id="YOUR_ALIAS_ID",
                access_key_env="AWS_ACCESS_KEY_ID",
                secret_key_env="AWS_SECRET_ACCESS_KEY",
                actions=["invoke"],
            )
        elif target == "mongodb":
            packages = ["pymongo"]
            requirements = [
                "MONGODB_DSN reference, database and selected collections.",
                "Read-only account by default; explicitly enabled writes require approval.",
            ]
            data.update(
                dsn_env="MONGODB_DSN",
                database="YOUR_DATABASE",
                collections=["YOUR_COLLECTION"],
                allow_writes=False,
                actions=["query"],
            )
        elif target == "sql":
            packages = ["SQLAlchemy"]
            requirements = [
                "Database URL environment reference and read-only database account.",
                "Install the selected driver: psycopg2-binary (PostgreSQL), PyMySQL (MySQL/MariaDB), pymssql (SQL Server) or oracledb (Oracle).",
                "Select and validate the database dialect before enabling; no driver is chosen automatically.",
            ]
            installation = [
                "Validated PostgreSQL driver: python -m pip install 'psycopg2-binary==2.9.13'",
                "Validated MySQL/MariaDB driver: python -m pip install 'PyMySQL==1.2.3'",
                "Selected SQL Server driver: python -m pip install 'pymssql==2.4.5'",
                "Selected Oracle driver: python -m pip install 'oracledb==26.0.1'",
            ]
            data.update(
                dsn_env="DATABASE_URL",
                dialect="SELECT_DIALECT",
                actions=["query"],
                allow_writes=False,
            )
            if identity.endswith("sql-database"):
                alias = "community-sql-database-tool"
        elif target == "slack":
            packages = ["slack-sdk"]
            requirements = [
                "Slack bot account token reference and selected channel.",
                "Explicitly selected actions/scopes; outgoing messages require review.",
            ]
            data.update(token_env="SLACK_BOT_TOKEN", channel="YOUR_CHANNEL_ID", actions=["send"])
        elif target == "nuclia":
            requirements = [
                "Nuclia knowledge-box URL/ID and scoped token environment reference.",
                "Search uses query. Explicitly add ask to enable provider-billed generative answers, bounded filters and private cited paragraph snapshots.",
                "Coding subscriptions do not cover Nuclia generation. Provider permissions and document access remain account-scoped.",
            ]
            data.update(
                url="https://YOUR_NUCLIA_ORIGIN",
                knowledge_box="YOUR_KNOWLEDGE_BOX",
                token_env="NUCLIA_TOKEN",
                actions=["query"],
            )
        elif target == "powerbi":
            requirements = [
                "Power BI dataset/workspace IDs, account access and POWERBI_TOKEN reference.",
                "Enable the tenant Execute Queries setting; grant dataset read/build access and Dataset.Read.All or Dataset.ReadWrite.All scope.",
                "One DAX query/table per call; Nexus limits responses to 256 KiB and rejects provider errors or partial tables.",
            ]
            data.update(
                token_env="POWERBI_TOKEN",
                dataset_id="YOUR_DATASET_UUID",
                workspace_id="YOUR_WORKSPACE_UUID",
                actions=["read", "query"],
            )
        template = {"resource": "connectors", "data": data}
    elif mode == "tool":
        action = row.get("action", row.get("target"))
        if action in {"imagemagick", "detect_objects"}:
            profile = "vision" if action == "detect_objects" else "media"
            requirements = [
                "Docker with an explicitly selected daemon context and installed immutable worker image.",
                "Only explicitly selected private input artifacts enter the worker.",
            ]
            requirements += (
                ["Installed trusted YOLO model, mounted read-only; CPU inference only."]
                if profile == "vision"
                else ["ImageMagick installed inside the selected worker image."]
            )
            installation = [
                "Build the pinned worker recipe and use its immutable image ID; see docs/STUDIO-DATA-WORKERS.md.",
                "Vision: torch==2.9.1, torchvision==0.24.1, ultralytics==8.3.235."
                if profile == "vision"
                else "Analysis: PyYAML==6.0.3, plotly==6.3.1, kaleido==1.1.0; ImageMagick is frozen by the selected final image ID.",
            ]
            worker = {"image": "sha256:SELECT_INSTALLED_IMAGE_ID", "docker_context": "YOUR_CONTEXT"}
            if profile == "vision":
                worker.update(
                    model_path="/YOUR_INSTALLED_MODEL.pt",
                    memory_mb=2048,
                    pids_limit=256,
                    open_files=256,
                )
            template = {"private_runtime": {"analysis_workers": {profile: worker}}}
            billing = "Local container compute; no media API or coding-subscription charge for the worker itself."
        elif action == "finance":
            packages = ["yfinance"]
            requirements = [
                "Explicit research.finance opt-in and access to the selected market-data service.",
                "Choose history (default), price or fundamentals; only history accepts period. Quotes may be delayed and metrics retain provider units.",
                "Market-data availability and terms remain provider-dependent. No trading operations.",
            ]
            template = {"private_runtime": {"research": {"finance": True}}}
            billing = (
                "External market-data service terms; selected AI provider billing remains separate."
            )
        elif action == "ip_location":
            requirements = [
                "Explicit research.ip_location opt-in; the lookup sends the supplied/public server IP to ipapi.co."
            ]
            template = {"private_runtime": {"research": {"ip_location": True}}}
            billing = "External IP/location service terms; not a coding-subscription feature."
        elif action == "web_search":
            requirements = [
                "Explicitly selected Tavily, Brave or SearxNG search configuration.",
                "Provider-specific token reference for authenticated search; public fetches never inherit account credentials.",
            ]
            template = {
                "private_runtime": {
                    "research": {"search": {"provider": "tavily", "token_env": "TAVILY_API_KEY"}}
                }
            }
            billing = "Selected search provider/API usage; not covered by coding subscriptions."
        elif action == "city_to_coordinates":
            requirements = [
                "Explicit research.geographic opt-in; city/weather queries go to Open-Meteo."
            ]
            template = {"private_runtime": {"research": {"geographic": True}}}
            billing = (
                "External geographic/weather service terms; selected AI billing remains separate."
            )
        elif action == "ruleset_lookup":
            requirements = [
                "An owned versioned JSON/YAML ruleset artifact and explicit lookup parameters."
            ]
            template = {
                "tool_arguments": {
                    "artifact_id": "YOUR_RULESET_ARTIFACT",
                    "revision": 1,
                    "parameters": {},
                }
            }
    elif mode == "feature":
        requirements = ["Use the existing workspace feature with Nexus permissions."]
        if identity.endswith("mermaid"):
            requirements = ["Owned .mmd artifact; Mermaid 12.2.0 is packaged locally."]
        elif identity.endswith("rag"):
            requirements = [
                "Owned knowledge project/documents; lexical retrieval needs no external model.",
                "Semantic/hybrid retrieval requires an explicitly configured embedding model/endpoint; external API billing requires opt-in.",
            ]
        else:
            requirements = [
                "Selected agent/workflow and explicitly enabled authenticated entrypoint.",
                "Review recipient/tool permissions and credentials before enabling.",
            ]
            if "mcp" in identity:
                packages = ["mcp"]
            if "slack" in identity:
                requirements += [
                    "Slack app signing-secret reference, HTTPS event URL and existing Nexus identity mapping."
                ]
                billing = (
                    "Slack account/platform terms; selected AI provider billing remains separate."
                )
    elif mode == "sharing":
        requirements = [
            "Owned conversation and explicitly selected artifacts/recipients.",
            "Private by default; public snapshots require owner opt-in, expiry and revocation.",
        ]
        template = {"open": "sharing"}
        billing = "Local artifact storage and selected hosting costs."
    elif mode == "cli":
        requirements = [
            "Installed Nexus CLI, Studio origin and Nexus session environment reference in team mode.",
            "Select an existing Codex/Claude Code subscription or configured API provider; billing follows that selection.",
        ]
        installation = [
            "Use the Nexus CLI from this reviewed Nexus ARC checkout/release; no separate plugin install is required."
        ]
        template = {"command": "nexus chat --url YOUR_STUDIO_ORIGIN --session-env NEXUS_SESSION"}
        billing = "Selected coding subscription or API account; no automatic provider switch."
    installation = [
        f"python -m pip install '{name}=={PINS[name]}'" for name in packages
    ] + installation
    if not installation:
        installation = [
            "Included in the reviewed Nexus ARC checkout/release; no plugin installation hook is required."
        ]
    return PluginSetup(
        billing,
        tuple(requirements),
        tuple(installation),
        template,
        SOURCE,
        SOURCE_REVISION,
        alias,
    ), packages


def describe_setup(row):
    metadata, packages = setup_metadata(row)
    checks = []
    for name in packages:
        try:
            installed = version(name)
        except PackageNotFoundError:
            installed = None
        checks.append(
            {
                "name": name,
                "status": "installed" if installed else "missing",
                "installed_version": installed,
                "installation_pin": PINS[name],
            }
        )
    missing = any(check["status"] == "missing" for check in checks)
    state = (
        "dependencies-missing"
        if missing
        else "configuration-present"
        if row.get("configured")
        else "setup-required"
    )
    return {
        "setup_metadata": metadata.describe(),
        "readiness": {
            "state": state,
            "checks": checks,
            "live_verified": False,
            "notice": "Local package/configuration facts only. Credentials, remote services, worker images/models and tool permissions require explicit setup and execution checks. Browsing never probes or installs them.",
        },
    }
