"""Non-executing adaptation of Nuclia Python descriptors to native connections."""

import json
import re

from nexus.core.studio_nuclia_interop import POLICY, convert_template_settings

DESCRIPTOR_ADAPTATIONS = (
    "Python module/class names are descriptive data; no source code is loaded.",
    "The selected native connection owns credentials and exposes only ask.",
    "Configured ask settings are fixed; conflicting caller overrides fail.",
    "Sources use private revision-pinned artifacts instead of provider ephemeral links.",
    "Answer artifacts use Nexus filenames and metadata, with structured source citations.",
    "Account identifiers are not used to mint ephemeral tokens; provider evaluation topics are unsupported.",
    "Native provider defaults replace the source tool's implicit neighbour/hierarchy retrieval strategies.",
)


def validate_ask_settings(value):
    """Validate fixed settings separately from caller-supplied query/parameters."""
    from nexus.core.studio_nuclia import request_body

    if not isinstance(value, dict) or set(value) - {
        "top_k",
        "rephrase",
        "prefer_markdown",
        "prompt",
    }:
        raise ValueError("Nuclia ask settings support top_k, rephrase, prefer_markdown and prompt")
    # Use the invocation validator, so saved and executed settings have one contract.
    request_body("ask", {"query": "Validate configured settings", **value})
    return json.loads(json.dumps(value, allow_nan=False))


def convert_tool_descriptor(source, *, policy):
    """Return disabled native connection data for explicit review and binding.

    The strict profile preserves supported request settings. Presentation uses
    Nexus artifacts and citations. Unknown fields or unsupported semantics fail
    rather than being dropped. No environment value is resolved.
    """
    if policy != POLICY:
        raise ValueError("Select the explicit Nuclia descriptor policy nexus-strict-v1")
    if not isinstance(source, dict) or set(source) != {
        "tool_type",
        "component_module",
        "class_name",
        "tool_config",
    }:
        raise ValueError("Supply a Python descriptor with module, class and tool_config only")
    module = source["component_module"]
    if (
        source["tool_type"] != "python"
        or source["class_name"] != "NucliaRagTool"
        or not isinstance(module, str)
        or len(module) > 200
        or not re.fullmatch(r"(?:[A-Za-z_][A-Za-z0-9_]*\.)+nuclia_rag_tool", module)
    ):
        raise ValueError("Only a NucliaRagTool Python descriptor maps to this profile")
    config = source["tool_config"]
    allowed = {
        "tool_name",
        "base_url",
        "kb_id",
        "token",
        "account_id",
        "top_k",
        "output_response_as_artifact",
        "output_filename_base",
        "artifact_description_query_max_length",
        "inline_citation_links",
        "include_citations_in_tool_response",
        "citations",
        "prefer_markdown",
        "user_prompt",
        "rephrase",
        "template_parameters",
        "context_parameters",
        "prompt_rephrasing",
        "filter_expression_template",
        "audit_metadata",
        "remi_publish_topic",
    }
    if not isinstance(config, dict) or set(config) - allowed:
        raise ValueError("Unsupported Nuclia descriptor settings require manual adaptation")
    try:
        encoded = json.dumps(config, allow_nan=False).encode()
    except (TypeError, ValueError, RecursionError):
        raise ValueError("Nuclia descriptor must contain finite JSON values") from None
    if len(encoded) > 32768:
        raise ValueError("Nuclia descriptor exceeds 32 KiB")
    name = config.get("tool_name", "NucliaRagTool")
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,99}", name):
        raise ValueError("Nuclia tool name must be a bounded identifier")
    token = config.get("token")
    if not isinstance(token, str) or not re.fullmatch(r"\$\{[A-Za-z_][A-Za-z0-9_]*\}", token):
        raise ValueError("Nuclia token requires an environment reference, never a credential value")
    endpoint = config.get("base_url")
    box = config.get("kb_id")
    if (
        not isinstance(endpoint, str)
        or not endpoint.rstrip("/").endswith("/api/v1")
        or "${" in endpoint
        or not isinstance(box, str)
        or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", box)
    ):
        raise ValueError(
            "Use a concrete credential-free API base ending /api/v1 and knowledge box ID"
        )
    if config.get("account_id") is not None:
        account = config["account_id"]
        if not isinstance(account, str) or not re.fullmatch(
            r"(?:[A-Za-z0-9_-]{1,128}|\$\{[A-Za-z_][A-Za-z0-9_]*\})", account
        ):
            raise ValueError("Nuclia account ID must be an identifier or environment reference")
    if config.get("remi_publish_topic") is not None:
        raise ValueError(
            "Provider evaluation topics require manual adaptation to Nexus evaluations"
        )
    audit = config.get("audit_metadata")
    if audit is not None and (
        not isinstance(audit, dict)
        or set(audit) - {"enabled", "fields"}
        or audit.get("enabled") is not False
        or audit.get("fields", {}) != {}
    ):
        raise ValueError("Enabled or populated provider audit metadata requires manual adaptation")
    for field, default in {
        "output_response_as_artifact": True,
        "inline_citation_links": True,
        "include_citations_in_tool_response": False,
    }.items():
        if type(config.get(field, default)) is not bool or config.get(field, default) != default:
            raise ValueError("Non-default Nuclia presentation flags require manual adaptation")
    if config.get("citations", "default") != "default":
        raise ValueError("Only default Nuclia citations map to private Nexus sources")
    filename = config.get("output_filename_base", "nuclia_answer")
    length = config.get("artifact_description_query_max_length", 150)
    if filename != "nuclia_answer" or type(length) is not int or length != 150:
        raise ValueError("Custom Nuclia artifact presentation requires manual adaptation")
    if "context_parameters" in config and "template_parameters" in config:
        raise ValueError("Use template_parameters or legacy context_parameters, not both")
    templates = {
        "template_parameters": config.get(
            "template_parameters", config.get("context_parameters", [])
        ),
        "prompt_rephrasing": config.get("prompt_rephrasing"),
        "filter_expression_template": config.get("filter_expression_template"),
    }
    converted = convert_template_settings(templates, policy=policy)
    settings = {
        "top_k": config.get("top_k", 5),
        "rephrase": config.get("rephrase", False),
        "prefer_markdown": config.get("prefer_markdown", True),
    }
    if config.get("user_prompt") is not None:
        settings["prompt"] = {"user": config["user_prompt"]}
    from nexus.core.studio_remote import validate_remote

    endpoint = validate_remote({"url": endpoint.rstrip("/")[:-7], "allow_private": True})["url"]
    return {
        "name": name,
        "policy": policy,
        "adaptations": [*converted["adaptations"], *DESCRIPTOR_ADAPTATIONS],
        "connector": {
            "type": "nuclia",
            "enabled": False,
            "url": endpoint,
            "knowledge_box": box,
            "token_env": token[2:-1],
            "actions": ["ask"],
            "template": converted["template"],
            "ask_settings": validate_ask_settings(settings),
        },
    }


def bind_tool_descriptor(bridge, source, path, refs):
    """Bind a descriptor to matching reviewed native settings without writing them."""
    if source.get("tool_type") != "python" or source.get("class_name") != "NucliaRagTool":
        return False
    config = source.get("tool_config")
    alias = config.get("tool_name", "NucliaRagTool") if isinstance(config, dict) else None
    binding = bridge.bindings.get("nuclia", {}).get(alias) if isinstance(alias, str) else None
    if not isinstance(binding, dict):
        bridge.issue(
            path,
            "binding_required",
            "Bind this Nuclia tool name to an existing connection with explicit nexus-strict-v1 policy",
        )
        return True
    converted = convert_tool_descriptor(source, policy=binding.get("policy"))
    target = binding.get("connector_id")
    selected = bridge.studio.catalog.get("connectors", target)["data"]
    expected = converted["connector"]
    actual = {key: selected.get(key) for key in expected if key != "enabled"}
    actual["template"] = selected.get("template", {"version": 1, "parameters": []})
    if actual != {key: value for key, value in expected.items() if key != "enabled"}:
        bridge.issue(
            path + "/tool_config",
            "connection_mismatch",
            "Selected Nuclia connection must match endpoint, box, token reference, template and fixed ask settings; expose ask only",
        )
        return True
    if target not in refs.setdefault("connector_ids", []):
        refs["connector_ids"].append(target)
    bridge.adaptations.append(
        {"path": path, "policy": converted["policy"], "changes": converted["adaptations"]}
    )
    return True
