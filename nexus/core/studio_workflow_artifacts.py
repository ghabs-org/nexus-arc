"""Expose proven single-file tool outputs through portable workflow metadata."""


def tool_output(studio, result, run_id, owner):
    if (
        not isinstance(result, dict)
        or not isinstance(result.get("artifacts"), list)
        or len(result["artifacts"]) != 1
    ):
        return result
    reference = result["artifacts"][0]
    if (
        not isinstance(reference, dict)
        or not isinstance(reference.get("id"), str)
        or type(reference.get("revision")) is not int
    ):
        return result
    if studio.team:
        studio.team.assert_execution(owner)
    current = studio.catalog.get("artifacts", reference["id"], archived=True)
    artifact = studio.artifacts.revision(reference["id"], reference["revision"])
    if (
        current.get("owner_id") != owner
        or artifact.get("owner_id") != owner
        or artifact.get("generated_by_run") != run_id
    ):
        raise PermissionError("Tool artifact metadata does not belong to this run and user")
    return {
        **result,
        "created_artifact": artifact["id"] + ":" + str(artifact["revision"]),
        "created_artifact_name": artifact["data"]["filename"],
        "created_artifact_version": artifact["revision"],
    }


def skipped_reason(graph, node_id, edges, statuses, outputs, context, scope, state):
    """Keep intentional pruning distinct from an unresolved routing decision."""
    from nexus.core.studio_interop_conditions import evaluate_condition

    known = {node["id"]: node for node in graph["nodes"]}
    for edge in edges:
        source = edge["source"]
        if source == "START":
            continue
        if (
            statuses.get(source) == "skipped"
            and state.get("skip_reasons", {}).get(scope + "/" + source) != "pruned"
        ):
            return "unresolved"
        origin = known[source]
        value = outputs.get(source, {})
        if (
            edge.get("choice")
            and origin["kind"] == "switch"
            and origin.get("result_mode") == "interop"
            and isinstance(value, dict)
        ):
            cutoff = value.get("selected_case_index", -1)
            cases = origin.get("cases", [])
            considered = cases if cutoff < 0 else cases[:cutoff]
            for case in considered:
                if (
                    case["value"] == edge["choice"]
                    and evaluate_condition(
                        case["condition"]["interop_condition"], {**context, "nodes": outputs}
                    ).unresolved
                ):
                    return "unresolved"
    return "pruned"


def image_references(entries, context, states, resolve):
    """Nil/pruned image entries vanish as a whole; stale/failed evidence fails."""
    from nexus.core.studio_interop_values import MissingSAMValueError, reference_value

    def references(value):
        if isinstance(value, dict):
            if set(value) == {"interop_ref"}:
                yield value["interop_ref"]
            elif set(value) == {"ref"}:
                path = value["ref"]
                yield {
                    "path": {"ref": path},
                    "source": path,
                    "category": "input"
                    if path.startswith(("input.", "interop_input."))
                    else "node",
                }
            else:
                for child in value.values():
                    yield from references(child)
        elif isinstance(value, list):
            for child in value:
                yield from references(child)

    result = []
    for entry in entries:
        omit = False
        for reference in references(entry):
            parts = reference["path"]["ref"].split(".")
            if parts[0] == "previous" and context.get("previous") is None:
                omit = True
                break
            if parts[0] in {"nodes", "interop_nodes"} and len(parts) > 1:
                known = states.get(parts[1])
                if known is None:
                    raise ValueError("Image reference has no acknowledged upstream node")
                if known["status"] == "skipped":
                    if known.get("reason") != "pruned":
                        raise ValueError("Image reference depends on unresolved routing")
                    omit = True
                    break
                if known["status"] != "completed":
                    raise ValueError("Image reference depends on a failed or unfinished node")
                if known.get("output") is None:
                    omit = True
                    break
            try:
                value = reference_value(reference, context)
            except MissingSAMValueError as error:
                if reference["category"] != "input" or error.stale:
                    raise ValueError(
                        "Image reference names a missing upstream output field"
                    ) from None
                omit = True
                break
            if value is None or value == "" or value == []:
                omit = True
                break
        if not omit:
            result.append(resolve(entry, context))
    return result
