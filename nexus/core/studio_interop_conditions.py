"""Parse a bounded deterministic external condition subset; never execute source code."""

import ast
import io
import math
import re
import tokenize
from dataclasses import dataclass

from nexus.core.studio_interop_mappings import TOKEN
from nexus.core.studio_interop_values import (
    MissingSAMValueError,
    import_value,
    reference_value,
    text,
    validate_value,
)

MARKER = "INTEROP_REFERENCE_"
OPS = {
    ast.Eq: "eq",
    ast.NotEq: "ne",
    ast.Lt: "lt",
    ast.LtE: "lte",
    ast.Gt: "gt",
    ast.GtE: "gte",
    ast.In: "in",
    ast.NotIn: "not_in",
}
CALL_OPS = {"contains", "startswith", "endswith"}


def parse_condition(source, reference=None):
    if (
        not isinstance(source, str)
        or not 1 <= len(source) <= 4000
        or MARKER in source
        or "${" in source
    ):
        raise ValueError("Use a bounded deterministic external condition")
    references = []

    def substitute(match):
        references.append(import_value(match[0], reference))
        return MARKER + str(len(references) - 1)

    substituted = TOKEN.sub(substitute, source)
    if "{{" in substituted or "}}" in substituted:
        raise ValueError("Malformed external condition reference")
    try:
        for token in tokenize.generate_tokens(io.StringIO(substituted).readline):
            if (
                token.type == tokenize.COMMENT
                or token.type == tokenize.NAME
                and token.string in {"True", "False", "None"}
                or token.type == tokenize.STRING
                and (
                    not token.string.startswith(("'", '"'))
                    or token.string.startswith(('"""', "'" * 3))
                )
            ):
                raise ValueError("Unsupported external condition token")
        expression = ast.parse(substituted, mode="eval")
    except (SyntaxError, RecursionError, tokenize.TokenError, IndentationError):
        raise ValueError("Invalid external condition syntax") from None
    if sum(1 for _ in ast.walk(expression)) > 200:
        raise ValueError("External condition complexity limit exceeded")

    def value(node):
        if isinstance(node, ast.Name):
            if re.fullmatch(MARKER + r"\d+", node.id):
                return {"value": references[int(node.id[len(MARKER) :])]}
            if node.id in {"true", "false"}:
                return {"value": node.id == "true"}
        if isinstance(node, ast.Constant) and type(node.value) in {str, int, float, bool}:
            if isinstance(node.value, float) and not math.isfinite(node.value):
                raise ValueError("Condition numbers must be finite")
            if isinstance(node.value, str) and MARKER in node.value:
                parts, offset = [], 0
                for match in re.finditer(MARKER + r"(\d+)", node.value):
                    if match.start() > offset:
                        parts.append(node.value[offset : match.start()])
                    parts.append(references[int(match[1])])
                    offset = match.end()
                if offset < len(node.value):
                    parts.append(node.value[offset:])
                return {"text": parts}
            return {"value": node.value}
        if isinstance(node, ast.List) and len(node.elts) <= 100:
            return {"list": [value(item) for item in node.elts]}
        if (
            isinstance(node, ast.UnaryOp)
            and isinstance(node.op, ast.USub)
            and isinstance(node.operand, ast.Constant)
            and type(node.operand.value) in {int, float}
        ):
            return {"value": -node.operand.value}
        raise ValueError("Only literals and data references are allowed in condition operands")

    def condition(node):
        if isinstance(node, ast.BoolOp):
            return {
                "all" if isinstance(node.op, ast.And) else "any": [
                    condition(part) for part in node.values
                ]
            }
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            return {"not": condition(node.operand)}
        if isinstance(node, ast.Compare) and len(node.ops) == 1 and type(node.ops[0]) in OPS:
            return {
                "compare": {
                    "left": value(node.left),
                    "op": OPS[type(node.ops[0])],
                    "right": value(node.comparators[0]),
                }
            }
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in CALL_OPS
            and len(node.args) == 2
            and not node.keywords
        ):
            return {
                "compare": {
                    "left": value(node.args[0]),
                    "op": node.func.id,
                    "right": value(node.args[1]),
                }
            }
        return {"boolean": value(node)}

    result = {"interop_condition": condition(expression.body)}
    validate_condition(result["interop_condition"])
    return result


def validate_condition(tree, depth=0, budget=None):
    budget = budget if budget is not None else [200]
    budget[0] -= 1
    if depth > 20 or budget[0] < 0 or not isinstance(tree, dict) or len(tree) != 1:
        raise ValueError("Invalid or oversized external condition")
    key, value = next(iter(tree.items()))
    if key in {"all", "any"}:
        if not isinstance(value, list) or not 1 <= len(value) <= 100:
            raise ValueError("External logical conditions require bounded operands")
        for child in value:
            validate_condition(child, depth + 1, budget)
    elif key == "not":
        validate_condition(value, depth + 1, budget)
    elif key == "compare":
        if (
            not isinstance(value, dict)
            or set(value) != {"left", "op", "right"}
            or value["op"] not in (*OPS.values(), *CALL_OPS)
        ):
            raise ValueError("Invalid external comparison")
        validate_operand(value["left"])
        validate_operand(value["right"])
    elif key == "boolean":
        validate_operand(value)
    else:
        raise ValueError("Unsupported external condition operation")


def validate_operand(value, depth=0):
    if depth > 20 or not isinstance(value, dict) or len(value) != 1:
        raise ValueError("Invalid external condition operand")
    if "value" in value:
        item = value["value"]
        if isinstance(item, dict):
            if set(item) != {"interop_ref"}:
                raise ValueError("Only external references are condition operands")
            validate_value(item)
        elif (
            type(item) not in {str, int, float, bool}
            or isinstance(item, float)
            and not math.isfinite(item)
        ):
            raise ValueError("Invalid condition literal")
    elif "text" in value:
        validate_value({"interop_text": value["text"]})
    elif "list" in value and isinstance(value["list"], list) and len(value["list"]) <= 100:
        for item in value["list"]:
            validate_operand(item, depth + 1)
    else:
        raise ValueError("Invalid external condition operand")


@dataclass(frozen=True)
class Answer:
    value: bool | None
    unresolved: bool = False


def evaluate_condition(tree, context):
    def operand(part):
        if "value" in part:
            item = part["value"]
            if not isinstance(item, dict):
                return item
            value = reference_value(item["interop_ref"], context)
            if value is None:
                raise MissingSAMValueError()
            return value
        if "text" in part:
            values = []
            for item in part["text"]:
                if isinstance(item, str):
                    values.append(item)
                else:
                    value = reference_value(item["interop_ref"], context)
                    if value is None:
                        raise MissingSAMValueError()
                    values.append(text(value))
            return "".join(values)
        return [operand(item) for item in part["list"]]

    def evaluate(node):
        key, value = next(iter(node.items()))
        if key in {"all", "any"}:
            answers, errors = [], []
            for child in value:
                try:
                    answers.append(evaluate(child))
                except ValueError as error:
                    errors.append(error)
            values = [a.value for a in answers]
            settled = False in values if key == "all" else True in values
            if settled:
                result = key == "any"
            elif None in values:
                result = None
            elif errors:
                raise errors[0]
            else:
                result = key == "all"
            return Answer(result, any(a.unresolved for a in answers))
        if key == "not":
            answer = evaluate(value)
            return Answer(None if answer.value is None else not answer.value, answer.unresolved)
        try:
            if key == "boolean":
                result = operand(value)
                if type(result) is not bool:
                    raise ValueError("External condition must evaluate to a boolean")
                return Answer(result)
            left, right = operand(value["left"]), operand(value["right"])
            op = value["op"]
            if (
                op in {"eq", "ne", "lt", "lte", "gt", "gte"}
                and type(left) is not type(right)
                and not (type(left) in {int, float} and type(right) in {int, float})
            ):
                raise ValueError("External comparison values have incompatible types")
            if op in {"startswith", "endswith"} and (
                not isinstance(left, str) or not isinstance(right, str)
            ):
                raise ValueError("External comparison values have incompatible types")
            operations = {
                "eq": lambda: left == right,
                "ne": lambda: left != right,
                "lt": lambda: left < right,
                "lte": lambda: left <= right,
                "gt": lambda: left > right,
                "gte": lambda: left >= right,
                "in": lambda: left in right,
                "not_in": lambda: left not in right,
                "contains": lambda: right in left,
                "startswith": lambda: left.startswith(right),
                "endswith": lambda: left.endswith(right),
            }
            return Answer(bool(operations[op]()))
        except MissingSAMValueError as missing:
            return Answer(None, missing.stale)
        except TypeError:
            raise ValueError("External comparison values have incompatible types") from None

    return evaluate(tree)
