"""Non-executing adaptation of Nuclia Python descriptors to native connections."""

import json
import re
import string

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
        "rag_strategies",
        "citations",
    }:
        raise ValueError(
            "Nuclia ask settings support top_k, rephrase, prefer_markdown, prompt, rag_strategies and citations"
        )
    # Use the invocation validator, so saved and executed settings have one contract.
    body = request_body("ask", {"query": "Validate configured settings", **value})
    normalized = json.loads(json.dumps(value, allow_nan=False))
    if "rag_strategies" in value:
        normalized["rag_strategies"] = body["rag_strategies"]
    return normalized


def convert_tool_descriptor(
    source,
    *,
    policy,
    preserve_retrieval=False,
    preserve_presentation=False,
    preserve_evaluation=False,
):
    """Return disabled native connection data for explicit review and binding.

    The strict profile preserves supported request settings. Presentation uses
    Nexus artifacts and citations. Unknown fields or unsupported semantics fail
    rather than being dropped. No environment value is resolved.
    """
    if policy != POLICY:
        raise ValueError("Select the explicit Nuclia descriptor policy nexus-strict-v1")
    if type(preserve_retrieval) is not bool:
        raise ValueError("Nuclia preserve_retrieval must be an explicit boolean")
    if type(preserve_presentation) is not bool:
        raise ValueError("Nuclia preserve_presentation must be an explicit boolean")
    if type(preserve_evaluation) is not bool:
        raise ValueError("Nuclia preserve_evaluation must be an explicit boolean")
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
    topic = config.get("remi_publish_topic")
    if topic is not None and not preserve_evaluation:
        raise ValueError(
            "Provider evaluation topics require manual adaptation to Nexus evaluations"
        )
    if topic is not None:
        if (
            not isinstance(topic, str)
            or len(topic.encode()) > 1000
            or any(ord(char) < 32 or ord(char) == 127 for char in topic)
        ):
            raise ValueError("Evaluation topic requires bounded static text/plain placeholders")
        try:
            for _, name, specification, conversion in string.Formatter().parse(topic):
                if name is not None and (
                    name not in {"interaction_id", "tool_name", "learning_id"}
                    or specification
                    or conversion
                ):
                    raise ValueError
        except ValueError:
            raise ValueError(
                "Evaluation topic supports plain interaction_id, tool_name and learning_id placeholders only"
            ) from None
    presentation_defaults = {
        "output_response_as_artifact": True,
        "inline_citation_links": True,
        "include_citations_in_tool_response": False,
        "output_filename_base": "nuclia_answer",
        "artifact_description_query_max_length": 150,
    }
    answer_options = None
    if preserve_presentation:
        from nexus.core.studio_nuclia_presentation import (
            validate_answer_options,
            validate_citation_mode,
        )

        answer_options = validate_answer_options(
            {key: config.get(key, default) for key, default in presentation_defaults.items()}
        )
        validate_citation_mode(config.get("citations", "default"))
    else:
        for field in (
            "output_response_as_artifact",
            "inline_citation_links",
            "include_citations_in_tool_response",
        ):
            default = presentation_defaults[field]
            if (
                type(config.get(field, default)) is not bool
                or config.get(field, default) != default
            ):
                raise ValueError("Non-default Nuclia presentation flags require manual adaptation")
        if config.get("citations", "default") != "default":
            raise ValueError("Only default Nuclia citations map to private Nexus sources")
        length = config.get("artifact_description_query_max_length", 150)
        if (
            config.get("output_filename_base", "nuclia_answer") != "nuclia_answer"
            or type(length) is not int
            or length != 150
        ):
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
    if config.get("audit_metadata") is not None:
        templates["audit_metadata"] = config["audit_metadata"]
    converted = convert_template_settings(templates, policy=policy)
    # Earlier bindings accepted disabled, empty audit settings without a native field.
    if converted["template"].get("audit_metadata") == {"enabled": False, "fields": {}}:
        converted["template"].pop("audit_metadata")
    settings = {
        "top_k": config.get("top_k", 5),
        "rephrase": config.get("rephrase", False),
        "prefer_markdown": config.get("prefer_markdown", True),
    }
    adaptations = list(DESCRIPTOR_ADAPTATIONS)
    if preserve_evaluation:
        adaptations[5] = (
            "Configured evaluation topic adapts to private full-retrieval evidence, including uncited paragraphs. No broker publication occurs; judge selection and execution remain explicit."
            if topic
            else "No evaluation topic is configured; no additional capture is enabled."
        )
    if preserve_presentation:
        settings["citations"] = config.get("citations", "default")
        adaptations[4] = (
            "Answer-file output, filename, query-prefix description, inline marker style and compact citations follow reviewed settings. Private native source snapshots remain available, with authenticated revision links instead of broker URLs."
        )
    if preserve_retrieval:
        from nexus.core.studio_nuclia_retrieval import EXPANDED_CONTEXT

        settings["rag_strategies"] = list(EXPANDED_CONTEXT)
        adaptations[-1] = (
            "Neighbouring paragraphs (two before/two after) and title/summary hierarchy context are fixed explicitly; expanded provider context may increase usage."
        )
    if config.get("user_prompt") is not None:
        settings["prompt"] = {"user": config["user_prompt"]}
    from nexus.core.studio_remote import validate_remote

    endpoint = validate_remote({"url": endpoint.rstrip("/")[:-7], "allow_private": True})["url"]
    return {
        "name": name,
        "policy": policy,
        "adaptations": [*converted["adaptations"], *adaptations],
        "connector": {
            "type": "nuclia",
            "enabled": False,
            "url": endpoint,
            "knowledge_box": box,
            "token_env": token[2:-1],
            "actions": ["ask"],
            "template": converted["template"],
            "ask_settings": validate_ask_settings(settings),
            **({"answer_options": answer_options} if answer_options is not None else {}),
            **(
                {"capture_evaluation": True, "evaluation_context": "retrieved"}
                if preserve_evaluation and topic
                else {}
            ),
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
    converted = convert_tool_descriptor(
        source,
        policy=binding.get("policy"),
        preserve_retrieval=binding.get("preserve_retrieval", False),
        preserve_presentation=binding.get("preserve_presentation", False),
        preserve_evaluation=binding.get("preserve_evaluation", False),
    )
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
