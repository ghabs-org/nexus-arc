"""Non-executable, resource-bounded values used by Studio dynamic embeds."""

import ast
import csv
import html
import io
import json
import math
import operator
import re

LIMIT = 32768
ROWS = 1000
PATH = re.compile(r"(?:\.([A-Za-z_][\w-]*)|\[(\d+|\*)\])")


def bounded(value, limit=LIMIT):
    if not isinstance(value, str) or len(value.encode("utf-8")) > limit:
        raise ValueError("Value exceeds embed size limit")
    return value


def encode(value, pretty=False):
    return bounded(
        json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2 if pretty else None)
    )


def select(value, path, *, wildcard=False):
    """JSONPath subset: root, object keys, integer indices, and optional wildcards."""
    if not isinstance(path, str) or len(path) > 512:
        raise ValueError("Invalid data path")
    if not path.startswith("$"):
        path = "$." + path
    position, values, multi, depth = 1, [value], False, 0
    while position < len(path):
        match = PATH.match(path, position)
        if not match or depth >= 16:
            raise ValueError("Unsupported data path")
        key, index = match.groups()
        if key and key.startswith("_"):
            raise ValueError("Private state keys are unavailable")
        selected = []
        for item in values:
            if key is not None:
                if not isinstance(item, dict) or key not in item:
                    raise ValueError("Data path is unavailable")
                selected.append(item[key])
            elif index == "*" and wildcard:
                if not isinstance(item, list):
                    raise ValueError("Wildcard requires an array")
                selected.extend(item)
                multi = True
            elif index != "*" and isinstance(item, list) and int(index) < len(item):
                selected.append(item[int(index)])
            else:
                raise ValueError("Data path is unavailable")
        if len(selected) > ROWS:
            raise ValueError("Data row limit exceeded")
        values, position, depth = selected, match.end(), depth + 1
    return values if multi else values[0]


def calculation(expression):
    if len(expression) > 1024:
        raise ValueError("Calculation exceeds limit")
    try:
        tree = ast.parse(expression, mode="eval")
    except (SyntaxError, RecursionError):
        raise ValueError("Invalid calculation") from None
    if sum(1 for _ in ast.walk(tree)) > 128:
        raise ValueError("Calculation exceeds limit")
    binary = {
        ast.Add: operator.add,
        ast.Sub: operator.sub,
        ast.Mult: operator.mul,
        ast.Div: operator.truediv,
        ast.FloorDiv: operator.floordiv,
        ast.Mod: operator.mod,
        ast.Pow: operator.pow,
    }

    def visit(node, depth=0):
        if depth > 16:
            raise ValueError("Calculation exceeds depth limit")
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            value = node.value
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            value = visit(node.operand, depth + 1) * (-1 if isinstance(node.op, ast.USub) else 1)
        elif isinstance(node, ast.BinOp) and type(node.op) in binary:
            left, right = visit(node.left, depth + 1), visit(node.right, depth + 1)
            if isinstance(node.op, ast.Pow) and (int(right) != right or not -12 <= right <= 12):
                raise ValueError("Power exponent exceeds limit")
            try:
                value = binary[type(node.op)](left, right)
            except (ArithmeticError, ValueError):
                raise ValueError("Invalid calculation") from None
        else:
            raise ValueError("Only numeric arithmetic is supported")
        if type(value) not in (int, float) or abs(value) > 1e15 or not math.isfinite(value):
            raise ValueError("Numeric magnitude exceeds limit")
        return value

    return visit(tree.body)


def number_format(value, spec):
    if not re.fullmatch(r"(?:[<^>])?\d{0,2}(?:\.\d{1,2})?[fge%]?", spec):
        raise ValueError("Unsupported number format")
    precision = re.search(r"\.(\d+)", spec)
    if precision and int(precision[1]) > 12:
        raise ValueError("Number precision exceeds limit")
    return bounded(format(value, spec))


def rows(value):
    if (
        not isinstance(value, list)
        or len(value) > ROWS
        or not all(isinstance(r, dict) for r in value)
    ):
        raise ValueError("Expected at most 1000 object rows")
    return value


def decode(content, filename):
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError("Binary content requires an artifact attachment") from None
    if filename.lower().endswith(".json"):
        try:
            return json.loads(text)
        except (ValueError, RecursionError):
            raise ValueError("Invalid JSON artifact") from None
    if filename.lower().endswith(".csv"):
        try:
            reader = csv.DictReader(io.StringIO(text))
            if len(reader.fieldnames or []) > 100:
                raise ValueError("CSV column limit exceeded")
            result = []
            for row in reader:
                result.append(row)
                if len(result) > ROWS:
                    raise ValueError("Data row limit exceeded")
            return result
        except csv.Error:
            raise ValueError("Invalid CSV artifact") from None
    return text


def transform(value, step):
    name, separator, argument = step.partition(":")
    if not separator:
        raise ValueError("Modifier requires an argument")
    if name == "jsonpath":
        return select(value, argument, wildcard=True)
    if name in {"select_cols", "select_fields"}:
        keys = argument.split(",")
        if not 1 <= len(keys) <= 100 or not all(keys):
            raise ValueError("Choose valid data fields")
        return [{k: row[k] for k in keys if k in row} for row in rows(value)]
    if name == "filter_rows_eq":
        key, sep, expected = argument.partition(":")
        if not sep:
            raise ValueError("Row filter requires field and value")
        return [row for row in rows(value) if str(row.get(key, "")) == expected]
    if name in {"slice_rows", "slice_lines"}:
        if not re.fullmatch(r"\d{0,4}:\d{0,4}", argument):
            raise ValueError("Slice requires nonnegative start:end")
        start, end = (int(x) if x else None for x in argument.split(":"))
        if name == "slice_rows":
            return rows(value)[start:end]
        if not isinstance(value, str):
            raise ValueError("Line modifiers require text")
        return "\n".join(value.splitlines()[start:end])
    if name in {"head", "tail", "grep"}:
        if not isinstance(value, str):
            raise ValueError("Line modifiers require text")
        lines = value.splitlines()
        if name == "grep":
            if not argument or len(argument) > 256 or re.search(r"[\\\[\]().*+?{}^$|]", argument):
                raise ValueError("Grep supports literal text only")
            return "\n".join(line for line in lines if argument in line)
        if not re.fullmatch(r"\d{1,4}", argument) or int(argument) > ROWS:
            raise ValueError("Line count exceeds limit")
        count = int(argument)
        return "\n".join(lines[:count] if name == "head" else lines[-count:] if count else [])
    raise ValueError("Unsupported data modifier")


def format_data(value, kind):
    if kind in {"json", "json_pretty"}:
        return encode(value, kind == "json_pretty")
    if kind == "text":
        return bounded(value if isinstance(value, str) else encode(value))
    if kind == "csv":
        records = rows(value)
        keys = list(dict.fromkeys(k for row in records for k in row))
        if len(keys) > 100:
            raise ValueError("CSV column limit exceeded")
        target = io.StringIO()
        writer = csv.DictWriter(target, fieldnames=keys)
        writer.writeheader()
        writer.writerows(records)
        return bounded(target.getvalue())
    raise ValueError("Unsupported output format")


TAG = re.compile(r"{{\s*([#^/!]?)([^{}]*?)\s*}}")


def template(text, value):
    """Small escaped Mustache subset; no partials, raw HTML or callbacks."""
    bounded(text)
    if "{{{" in text or re.search(r"{{\s*[>&=]", text):
        raise ValueError("Raw tags, partials and delimiter changes are unsupported")
    remainder = TAG.sub("", text)
    if "{{" in remainder or "}}" in remainder:
        raise ValueError("Invalid template tags")
    tokens, stack, position = [], [], 0
    active = tokens
    for match in TAG.finditer(text):
        active.append(text[position : match.start()])
        kind, key = match.groups()
        if not key or len(key) > 512:
            raise ValueError("Invalid template tag")
        if kind in {"#", "^"}:
            if len(stack) >= 16:
                raise ValueError("Template nesting limit exceeded")
            child = []
            active.append((kind, key, child))
            stack.append((key, active))
            active = child
        elif kind == "/":
            if not stack or stack[-1][0] != key:
                raise ValueError("Unbalanced template sections")
            _, active = stack.pop()
        elif kind != "!":
            active.append(("", key, []))
        position = match.end()
    active.append(text[position:])
    if stack or "{{" in "".join(t for t in active if isinstance(t, str)):
        raise ValueError("Unbalanced template tags")
    budget = [0]

    def render(items, context):
        output = []
        for item in items:
            budget[0] += 1
            if budget[0] > 10000:
                raise ValueError("Template work limit exceeded")
            if isinstance(item, str):
                output.append(item)
            else:
                kind, key, child = item
                try:
                    selected = context if key == "." else select(context, key)
                except ValueError:
                    selected = None
                if not kind:
                    output.append(
                        html.escape("" if selected is None else str(selected), quote=True)
                    )
                elif kind == "^" and not selected:
                    output.append(render(child, context))
                elif kind == "#" and selected:
                    values = selected if isinstance(selected, list) else [selected]
                    if len(values) > ROWS:
                        raise ValueError("Template row limit exceeded")
                    for entry in values:
                        output.append(
                            render(
                                child,
                                entry
                                if isinstance(entry, dict)
                                else context
                                if entry is True
                                else entry,
                            )
                        )
            bounded("".join(output))
        return "".join(output)

    return render(tokens, {"items": value} if isinstance(value, list) else value)
