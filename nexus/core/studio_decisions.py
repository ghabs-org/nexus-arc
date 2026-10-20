"""Strict typed decisions through existing Nexus decision credentials and clients."""

import asyncio
import hashlib
import inspect
import json
import math
import re

from nexus.adapters.decisions.errors import TRANSIENT, DecisionCallError

FORMAT = "nexus-typed-decision/v1"
NAME = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}")
MODEL = re.compile(r"[A-Za-z0-9~][A-Za-z0-9_./:~-]{0,199}")


def finite(value, low, high):
    return type(value) in {int, float} and math.isfinite(value) and low <= value <= high


def label(value, limit):
    return isinstance(value, str) and bool(value.strip()) and len(value) <= limit


def validate_binding(binding):
    if (
        not isinstance(binding, dict)
        or set(binding) != {"backend", "model"}
        or binding["backend"] not in ("opencode", "openrouter", "openai")
        or not isinstance(binding["model"], str)
        or not MODEL.fullmatch(binding["model"])
    ):
        raise ValueError("Decision alias requires an explicit existing backend and model")
    if binding["backend"] == "openai" and binding["model"] != "gpt-6-luna":
        raise ValueError("The native OpenAI decision profile requires gpt-6-luna")
    return binding


def validate_questions(questions):
    if not isinstance(questions, dict) or not 1 <= len(questions) <= 32:
        raise ValueError("Use 1–32 named decision questions")
    for name, question in questions.items():
        if not isinstance(name, str) or not NAME.fullmatch(name):
            raise ValueError("Use short named decision questions")
        if (
            not isinstance(question, dict)
            or set(question) - {"type", "instructions", "criteria"}
            or question.get("type") not in ("choice", "boolean", "score")
            or not label(question.get("instructions"), 4000)
        ):
            raise ValueError("Decision questions need a supported type and bounded instructions")
        kind, criteria = question["type"], question.get("criteria")
        if kind == "score":
            if not isinstance(criteria, list) or not 2 <= len(criteria) <= 10:
                raise ValueError("Score criteria require 2–10 ordered named levels")
            levels = []
            for entry in criteria:
                if not isinstance(entry, dict) or len(entry) != 1:
                    raise ValueError("Each score level requires one name and description")
                key, description = next(iter(entry.items()))
                if (
                    not isinstance(key, str)
                    or not NAME.fullmatch(key)
                    or not label(description, 1000)
                ):
                    raise ValueError("Invalid score level")
                levels.append(key)
            if len(set(levels)) != len(levels):
                raise ValueError("Score level names must be unique")
        elif kind == "boolean" and "criteria" not in question:
            continue
        elif (
            not isinstance(criteria, dict)
            or kind == "choice"
            and not 2 <= len(criteria) <= 255
            or kind == "boolean"
            and set(criteria) != {"true", "false"}
            or any(
                not isinstance(k, str) or not NAME.fullmatch(k) or not label(v, 1000)
                for k, v in criteria.items()
            )
        ):
            raise ValueError("Invalid named decision criteria")
    if len(json.dumps(questions, ensure_ascii=False).encode()) > 60000:
        raise ValueError("Decision questions exceed 60,000 bytes")


def validate_decision(config):
    if (
        not isinstance(config, dict)
        or set(config)
        - {
            "format",
            "backend",
            "model",
            "questions",
            "route_on",
            "routes",
            "default",
            "min_confidence",
            "escalate",
            "escalation_mode",
            "on_provider_error",
            "timeout_seconds",
            "max_state_bytes",
            "max_images",
            "max_image_bytes",
        }
        or config.get("format") != FORMAT
    ):
        raise ValueError("Invalid typed decision configuration")
    validate_binding({"backend": config.get("backend"), "model": config.get("model")})
    validate_questions(config.get("questions"))
    from nexus.core.studio_decision_images import limits

    limits(config)
    if not finite(config.get("timeout_seconds", 10), 1, 300):
        raise ValueError("Decision call timeout must be 1–300 seconds")
    if (
        type(config.get("max_state_bytes", 131072)) is not int
        or not 1 <= config.get("max_state_bytes", 131072) <= 131072
    ):
        raise ValueError("Decision state limit must be 1–131072 bytes")
    routes = config.get("routes", {})
    if not isinstance(routes, dict) or any(
        not isinstance(k, str) or not isinstance(v, str) or not NAME.fullmatch(v)
        for k, v in routes.items()
    ):
        raise ValueError("Decision routes must name workflow nodes")
    for key in ("default", "escalate"):
        if key in config and (not isinstance(config[key], str) or not NAME.fullmatch(config[key])):
            raise ValueError("Decision targets must name workflow nodes")
    route_on = config.get("route_on")
    if route_on is not None:
        question = config["questions"].get(route_on) if isinstance(route_on, str) else None
        if not question or question["type"] != "choice":
            raise ValueError("route_on must name a choice question")
        options = set(question["criteria"])
        if set(routes) - options or options - set(routes) and not config.get("default"):
            raise ValueError("Every choice requires a route or an explicit default")
    elif routes or any(k in config for k in ("default", "min_confidence")):
        raise ValueError("Routing settings require route_on")
    if "min_confidence" in config and (
        not finite(config["min_confidence"], 0, 1)
        or config["min_confidence"] == 0
        or not config.get("escalate")
    ):
        raise ValueError("Positive confidence threshold requires an escalation target")
    if config.get("escalation_mode", "reroute") not in ("reroute", "handoff") or config.get(
        "on_provider_error", "fail"
    ) not in ("fail", "escalate"):
        raise ValueError("Unsupported decision escalation mode")
    if config.get("on_provider_error") == "escalate" and not config.get("escalate"):
        raise ValueError("Provider-error escalation requires a target")
    if "escalation_mode" in config and not config.get("escalate"):
        raise ValueError("Escalation mode requires a target")
    if config.get("escalate") and config.get("escalation_mode", "reroute") == "reroute":
        if not route_on or config["escalate"] in set(routes.values()) | {config.get("default")}:
            raise ValueError(
                "Reroute escalation needs separate route targets and a choice question"
            )
    return config


def wire_questions(questions):
    result = {}
    for key, q in questions.items():
        result[key] = {**q}
        if q["type"] == "boolean":
            result[key]["type"] = "noul"
        elif q["type"] == "score":
            result[key]["criteria"] = [next(iter(item.values())) for item in q["criteria"]]
    return result


def probabilities(raw, names):
    if (
        not isinstance(raw, dict)
        or set(raw) != set(names)
        or any(not finite(p, 0, 1) for p in raw.values())
        or abs(sum(raw.values()) - 1) > 0.02
    ):
        raise DecisionCallError("invalid_response")
    return dict(raw)


def answers_for(questions, raw):
    if not isinstance(raw, dict) or set(raw) != set(questions):
        raise DecisionCallError("invalid_response")
    result = {}
    for name, question in questions.items():
        answer = raw[name]
        if not isinstance(answer, dict):
            raise DecisionCallError("invalid_response")
        if answer.get("type") in ("refusal", "refused") or "refusal" in answer:
            raise DecisionCallError("refused")
        kind = question["type"]
        if answer.get("type") != ("noul" if kind == "boolean" else kind):
            raise DecisionCallError("invalid_response")
        if kind == "boolean":
            p = answer.get("noul")
            if not finite(p, 0, 1):
                raise DecisionCallError("invalid_response")
            result[name] = {"value": p >= 0.5, "probability": p}
        else:
            if not finite(answer.get("confidence"), 0, 1):
                raise DecisionCallError("invalid_response")
            if kind == "choice":
                distribution = probabilities(answer.get("probabilities"), question["criteria"])
                choice = answer.get("choice")
                if (
                    not isinstance(choice, str)
                    or choice not in distribution
                    or distribution[choice] + 0.01 < max(distribution.values())
                ):
                    raise DecisionCallError("invalid_response")
                result[name] = {
                    "choice": choice,
                    "probabilities": distribution,
                    "confidence": answer["confidence"],
                }
            else:
                levels = [next(iter(item)) for item in question["criteria"]]
                indices = [str(i) for i in range(len(levels))]
                distribution = probabilities(answer.get("probabilities"), indices)
                legend = {
                    str(i): next(iter(item.values())) for i, item in enumerate(question["criteria"])
                }
                if answer.get("legend") != legend or not finite(
                    answer.get("score"), 0, len(levels) - 1
                ):
                    raise DecisionCallError("invalid_response")
                result[name] = {
                    "score": answer["score"],
                    "confidence": answer["confidence"],
                    "probabilities": {
                        level: distribution[str(i)] for i, level in enumerate(levels)
                    },
                    "legend": {str(i): level for i, level in enumerate(levels)},
                }
    return result


def reroute_choice(config, output, target_config=None):
    route_on = config["route_on"]
    value = output.get(route_on) if isinstance(output, dict) else None
    if target_config is not None:
        value = (
            output.get("answers", {}).get(route_on, {}).get("choice")
            if isinstance(output, dict)
            else None
        )
    if not isinstance(value, str) or value.strip() not in config["questions"][route_on]["criteria"]:
        raise ValueError("Escalation answer must be a declared choice string")
    return config.get("routes", {}).get(value.strip(), config.get("default", ""))


class StudioTypedDecisions:
    def __init__(self, jobs):
        self.jobs, self.studio = jobs, jobs.studio

    async def execute(self, config, state, payload, run_id, record_id, *, images=None):
        from nexus.adapters.decisions.jev import ask_strict

        validate_decision(config)
        actor = self.jobs.workbench.actor(payload)
        self.jobs.workbench.visible(self.studio.run(run_id), actor, own=True)
        if self.studio.team:
            self.studio.team.assert_execution(payload.get("requester_nexus_id"))
        owner = self.jobs.workbench.owner(actor)
        digest_input = [config, state, owner]
        if images or payload.get("_decision_image_revisions"):
            digest_input.extend([images, payload.get("_decision_image_revisions", {})])
        digest = hashlib.sha256(
            json.dumps(digest_input, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()
        ).hexdigest()
        record_path = self.studio._path("decisions", record_id)
        if record_path.exists():
            saved = self.studio._read(record_path)
            if saved.get("owner_id") != owner or saved.get("request_digest") != digest:
                raise PermissionError("Decision checkpoint does not match this run")
            return saved["result"]
        questions = wire_questions(config["questions"])
        selected, reason, category = "", "", None
        answers, metadata = {}, {}
        try:
            if not isinstance(state, (str, dict, list)):
                raise DecisionCallError("config")
            if len(json.dumps(state, ensure_ascii=False, allow_nan=False).encode()) > config.get(
                "max_state_bytes", 131072
            ):
                raise DecisionCallError("input_too_large")
            from nexus.core.studio_decision_images import prepare

            image_urls = prepare(self.studio, images or [], config, payload, actor, run_id=run_id)
            if config["backend"] == "openai":
                from nexus.adapters.decisions.openai import ask_strict as ask_native
            else:
                ask_native = ask_strict
            client = self.jobs.decision_client or ask_native
            try:
                kwargs = (
                    self.jobs.decision_credentials(
                        {**config, "requester_nexus_id": payload.get("requester_nexus_id")}
                    )
                    if not self.jobs.decision_client
                    else {}
                )
            except (ValueError, KeyError):
                raise DecisionCallError("config") from None
            model = config["model"]
            if (
                len(
                    json.dumps(
                        {"state": state, "model": model, "questions": questions},
                        ensure_ascii=False,
                        allow_nan=False,
                    ).encode()
                )
                > 65536
            ):
                raise DecisionCallError("input_too_large")
            if image_urls:
                kwargs["image_urls"] = image_urls
            timeout = config.get("timeout_seconds", 10)

            async def invoke():
                if self.studio.team:
                    self.studio.team.assert_execution(payload.get("requester_nexus_id"))
                if self.studio.run(run_id).get("cancel_requested"):
                    raise asyncio.CancelledError
                response = await asyncio.to_thread(
                    client, state, questions, **kwargs, timeout=timeout
                )
                return await response if inspect.isawaitable(response) else response

            try:
                raw = await asyncio.wait_for(invoke(), timeout)
            except TimeoutError:
                raise DecisionCallError("timeout") from None
            except DecisionCallError:
                raise
            except PermissionError:
                raise DecisionCallError("unauthorized") from None
            except OSError:
                raise DecisionCallError("unreachable") from None
            except Exception:
                raise DecisionCallError("config") from None
            reported = getattr(raw, "metadata", {})
            if isinstance(reported, dict):
                model = reported.get("model")
                if isinstance(model, str) and MODEL.fullmatch(model):
                    metadata["model"] = model
                usage = reported.get("usage", {})
                if isinstance(usage, dict):
                    for key in ("input_tokens", "output_tokens"):
                        if type(usage.get(key)) is int and 0 <= usage[key] <= 10**9:
                            metadata.setdefault("usage", {})[key] = usage[key]
            answers = answers_for(config["questions"], raw)
            route_on = config.get("route_on")
            if route_on:
                answer = answers[route_on]
                if answer["confidence"] < config.get("min_confidence", 0):
                    selected, reason = config["escalate"], "low_confidence"
                else:
                    selected = config.get("routes", {}).get(
                        answer["choice"], config.get("default", "")
                    )
        except DecisionCallError as error:
            if config.get("on_provider_error") != "escalate" or error.category not in TRANSIENT:
                raise
            selected, reason, category = config["escalate"], "provider_error", error.category
            answers = {}
        output = {
            "answers": answers,
            "selected_branch": selected,
            "routed_branch": "" if reason else selected,
            "escalated": bool(reason),
            "escalation_reason": reason,
        }
        if category:
            output["provider_error"] = category
        self.studio._update_run(run_id, provider="decision-" + config["backend"])
        from nexus.core.studio_workbench import now

        self.studio._write(
            self.studio._path("decisions", record_id),
            {
                "id": record_id,
                "format": FORMAT,
                "run_id": run_id,
                "updated_at": now(),
                "route_on": config.get("route_on"),
                "owner_id": owner,
                "request_digest": digest,
                "backend": config["backend"],
                "model": config["model"],
                "provider_metadata": metadata,
                "result": output,
            },
        )
        return output


def validate_routes(graph):
    nodes = {n["id"]: n for n in graph["nodes"]}
    edges = graph["edges"]
    for identity, node in nodes.items():
        config = node.get("decision", {})
        if node["kind"] != "decision" or config.get("format") != FORMAT:
            continue
        targets = set(config.get("routes", {}).values()) | {
            config[k] for k in ("default", "escalate") if config.get(k)
        }
        if not targets <= nodes.keys() or identity in targets:
            raise ValueError("Decision routes require distinct existing node targets")
        actual = {
            e["target"] for e in edges if e["source"] == identity and e.get("choice") == e["target"]
        }
        if actual != targets or any(
            e.get("choice") and e["choice"] != e["target"] for e in edges if e["source"] == identity
        ):
            raise ValueError("Decision targets require their exact conditional choice edges")
        escalate = config.get("escalate")
        if not escalate or config.get("escalation_mode", "reroute") != "reroute":
            continue
        target = nodes[escalate]
        if target["kind"] not in {"agent", "workflow", "tool", "decision"}:
            raise ValueError(
                "Reroute review must be an agent, workflow, tool or decision; use handoff otherwise"
            )
        if any(
            e.get("choice") and e["source"] != identity for e in edges if e["target"] == escalate
        ):
            raise ValueError("Reroute review cannot also belong to another router; use handoff")
        route_on = config["route_on"]
        options = set(config["questions"][route_on]["criteria"])
        if target["kind"] == "agent":
            schema = target.get("output_schema", {})
            field = (
                schema.get("properties", {}).get(route_on, {}) if isinstance(schema, dict) else {}
            )
            if (
                not isinstance(field, dict)
                or field.get("type") != "string"
                or set(field.get("enum", [])) != options
                or route_on not in schema.get("required", [])
            ):
                raise ValueError("Reroute agent must declare its required choice enum output field")
        if target["kind"] == "decision":
            review = target.get("decision", {})
            question = review.get("questions", {}).get(route_on, {})
            if (
                review.get("format") != FORMAT
                or review.get("escalate")
                or question.get("type") != "choice"
                or set(question.get("criteria", {})) != options
            ):
                raise ValueError(
                    "Reroute decision review must answer the same choice without further escalation"
                )
        for route in targets - {escalate}:
            if not any(e["source"] == escalate and e["target"] == route for e in edges):
                raise ValueError("Reroute branches must wait for their review target")
