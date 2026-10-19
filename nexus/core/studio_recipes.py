"""Community catalog equivalents configure ordinary Nexus services and tools."""

from copy import deepcopy

OFFICIAL = [
    (
        "community-bedrock-agent",
        "Amazon Bedrock",
        "connection",
        "bedrock",
        "Knowledge retrieval, Agents and Flows through your AWS account",
    ),
    (
        "community-geo-information",
        "Geographic information",
        "planned",
        "",
        "Geographic search adapter remains to be implemented",
    ),
    (
        "community-mcp-server-gateway-adapter",
        "MCP task gateway",
        "feature",
        "entrypoints",
        "Expose one selected agent or workflow through an authenticated MCP entrypoint",
    ),
    (
        "community-mermaid",
        "Mermaid diagrams",
        "feature",
        "library",
        "Preview uploaded .mmd artifacts using locally packaged Mermaid",
    ),
    (
        "community-mongodb",
        "MongoDB",
        "connection",
        "mongodb",
        "Bounded queries against selected database collections; reviewed single-document writes",
    ),
    (
        "community-nuclia-tool",
        "Nuclia",
        "connection",
        "nuclia",
        "Search your configured knowledge box with its account token",
    ),
    (
        "community-powerbi",
        "Power BI",
        "connection",
        "powerbi",
        "Read a selected dataset and run DAX queries",
    ),
    (
        "community-rag",
        "Knowledge retrieval",
        "feature",
        "projects",
        "Local document projects with explicit lexical, semantic or hybrid retrieval",
    ),
    (
        "community-rest-gateway",
        "REST entrypoint",
        "feature",
        "entrypoints",
        "Authenticated task entrypoints and Studio REST APIs",
    ),
    (
        "community-ruleset-lookup-tool",
        "Ruleset lookup",
        "planned",
        "",
        "Artifact-backed ruleset lookup remains to be implemented",
    ),
    (
        "community-slack-gateway-adapter",
        "Slack incoming events",
        "feature",
        "entrypoints",
        "Verified incoming Slack events using Nexus identity mappings",
    ),
    (
        "community-slack",
        "Slack connection",
        "connection",
        "slack",
        "Explicitly selected and reviewed Slack actions",
    ),
    (
        "community-sql-database-tool",
        "SQL database tool",
        "connection",
        "sql",
        "Read-only SQL across PostgreSQL, MySQL, MariaDB, SQL Server and Oracle",
    ),
    (
        "community-sql-database",
        "SQL database agent",
        "connection",
        "sql",
        "The same SQL connector can be explicitly granted to an agent",
    ),
    (
        "community-webhook-gateway",
        "Webhook entrypoint",
        "feature",
        "entrypoints",
        "Bearer-authenticated tasks with explicit target selection",
    ),
]

CATALOG = [
    {
        "id": "tavily",
        "name": "Tavily",
        "mode": "mcp",
        "description": "Web search and extraction through Tavily MCP",
        "url": "https://mcp.tavily.com/mcp/",
        "transport": "streamable-http",
        "token_env": "TAVILY_TOKEN",
    },
    {
        "id": "bright-data",
        "name": "Bright Data",
        "mode": "mcp",
        "description": "Web search and scraping through Bright Data MCP",
        "url": "https://mcp.brightdata.com/sse",
        "transport": "sse",
        "token_env": "BRIGHTDATA_TOKEN",
        "auth_type": "query",
    },
    {
        "id": "zapier",
        "name": "Zapier",
        "mode": "mcp",
        "description": "Selected Zapier app actions through MCP",
        "url": "https://mcp.zapier.com/api/mcp/s/{token}/sse",
        "transport": "sse",
        "token_env": "ZAPIER_TOKEN",
        "auth_type": "url-token",
    },
    {
        "id": "filesystem",
        "name": "Filesystem MCP",
        "mode": "filesystem",
        "description": "Read/write explicitly allowed workspace files through an installed MCP filesystem server",
    },
    {
        "id": "send-grid",
        "name": "SendGrid",
        "mode": "openapi",
        "description": "Reviewed email sending using your SendGrid account",
    },
    {
        "id": "find-my-ip",
        "name": "IP and location",
        "mode": "tool",
        "action": "ip_location",
        "description": "Look up a supplied IP or the server's public network location",
    },
    {
        "id": "web-agent",
        "name": "Web search and fetch",
        "mode": "tool",
        "action": "web_search",
        "description": "Search the web and fetch bounded public pages",
    },
    {
        "id": "finance",
        "name": "Finance data",
        "mode": "tool",
        "action": "finance",
        "description": "Market history and company information through yfinance",
    },
    {
        "id": "imagemagick",
        "name": "ImageMagick",
        "mode": "tool",
        "action": "imagemagick",
        "description": "Image resizing, rotation and grayscale in an isolated local worker",
    },
    {
        "id": "object-detection",
        "name": "Object detection",
        "mode": "tool",
        "action": "detect_objects",
        "description": "YOLO detections and annotated images using an installed local model",
    },
    {
        "id": "artifact-host-agent",
        "name": "Artifact hosting",
        "mode": "sharing",
        "description": "Revocable read-only snapshots with explicitly selected file downloads",
    },
    {
        "id": "cli-entrypoint",
        "name": "Terminal chat",
        "mode": "cli",
        "description": "Authenticated terminal conversations through nexus chat",
    },
]

SENDGRID_SPEC = {
    "openapi": "3.0.3",
    "info": {"title": "SendGrid mail", "version": "1"},
    "paths": {
        "/v3/mail/send": {
            "post": {
                "operationId": "sendMail",
                "summary": "Send an email through the configured account",
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["personalizations", "from", "subject", "content"],
                                "properties": {
                                    "personalizations": {"type": "array", "minItems": 1},
                                    "from": {"type": "object"},
                                    "subject": {"type": "string"},
                                    "content": {"type": "array", "minItems": 1},
                                },
                            }
                        }
                    },
                },
                "responses": {"202": {"description": "Accepted"}},
            }
        }
    },
}


class StudioRecipes:
    def __init__(self, studio):
        self.studio = studio

    def list(self):
        data_names = self.studio.data_tools.names
        library_names = getattr(getattr(self.studio, "research", None), "names", {})
        rows = deepcopy(CATALOG)
        tools = {
            r["id"]: r
            for service in (self.studio.data_tools, self.studio.research)
            for r in service.inventory()["items"]
        }
        for row in rows:
            action = row.get("action")
            row["tool_id"] = next(
                (
                    name
                    for name in [*data_names, *library_names]
                    if name.endswith(":" + str(action))
                ),
                None,
            )
            row["configured"] = False
            if row["mode"] == "tool" and row["tool_id"]:
                row["configured"] = tools[row["tool_id"]]["configured"]
                row["setup"] = tools[row["tool_id"]]["setup"]
            row["live_verified"] = False
            row["installation"] = (
                "Configuration only; no installation hooks or package downloads run while browsing"
            )
        return {
            "items": rows,
            "official_items": [
                {
                    "id": identity,
                    "name": name,
                    "mode": mode,
                    "target": target,
                    "description": description,
                    "configured": False,
                    "live_verified": False,
                    "catalog_commit": "7599710a38318a336c47cf605e4774d4add24d8e",
                }
                for identity, name, mode, target, description in OFFICIAL
            ],
            "excluded": [
                {
                    "id": "community-event-mesh" + suffix,
                    "reason": "Vendor product excluded by the agreed scope",
                }
                for suffix in ("-agent", "-gateway", "-identity-provider", "-tool")
            ],
            "note": "Available integrations require their own account or installed local dependencies. Listing does not install or enable anything.",
        }

    def configure(self, identity, data, actor):
        recipe = next((r for r in CATALOG if r["id"] == identity), None)
        if recipe is None:
            raise FileNotFoundError("Catalog integration not found")
        if self.studio.team:
            self.studio.team.authorize(actor, "/api/catalog/connectors")
        mode = recipe["mode"]
        if mode == "mcp":
            kind = recipe.get("auth_type", "bearer")
            config = {
                "type": "mcp",
                "transport": recipe["transport"],
                "url": recipe["url"],
                "auth": {"type": kind, "token_env": data.get("token_env") or recipe["token_env"]},
                "actions": ["discover"],
                "tools": [],
                "read_only_tools": [],
                "enabled": False,
            }
            if kind == "query":
                config["auth"]["parameter"] = "token"
        elif mode == "filesystem":
            command = data.get("command")
            if not command:
                raise ValueError("Select an already installed MCP filesystem server executable")
            config = {
                "type": "mcp",
                "command": command,
                "args": [str(self.studio.root)],
                "actions": ["discover"],
                "tools": [],
                "read_only_tools": [],
                "enabled": False,
            }
        elif mode == "openapi":
            config = {
                "type": "openapi",
                "url": "https://api.sendgrid.com",
                "auth": {
                    "type": "bearer",
                    "token_env": data.get("token_env") or "SENDGRID_API_KEY",
                },
                "spec": deepcopy(SENDGRID_SPEC),
                "operations": ["sendMail"],
                "actions": ["list_operations", "invoke"],
                "enabled": False,
            }
        elif mode == "sharing":
            return self.studio.collaboration.share(data, actor)
        elif mode == "cli":
            return {
                "command": "nexus chat --url YOUR_STUDIO_ORIGIN",
                "authentication": "Existing Nexus session via --session-env",
                "mutation_replay": False,
            }
        else:
            row = next(r for r in self.list()["items"] if r["id"] == identity)
            return {
                "tool_id": row["tool_id"],
                "configured": row["configured"],
                "setup": "Select this tool from Tools after configuring its optional worker or provider dependencies",
            }
        return self.studio.catalog.save(
            "connectors",
            {
                "name": data.get("name") or recipe["name"],
                "description": recipe["description"],
                "data": config,
                "requester_nexus_id": actor["nexus_id"] if actor else None,
            },
        )
