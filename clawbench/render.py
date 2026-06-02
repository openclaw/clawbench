"""Small templating helpers used by task assets, prompts, and checks."""

from __future__ import annotations

import json
import re
import shlex
from typing import Any, Mapping


PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z0-9_]+)\}")
SHELL_PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z0-9_]+)(?::(raw))?\}")


def _stringify_template_value(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value)
    return str(value)


def render_template(text: str, values: Mapping[str, Any]) -> str:
    """Replace `{name}` placeholders while leaving unrelated braces alone."""

    def repl(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in values:
            return match.group(0)
        return _stringify_template_value(values[key])

    return PLACEHOLDER_RE.sub(repl, text)


def _shell_quote_context(text: str, end: int) -> str | None:
    quote: str | None = None
    escaped = False

    for char in text[:end]:
        if quote == "single":
            if char == "'":
                quote = None
            continue

        if escaped:
            escaped = False
            continue

        if char == "\\":
            escaped = True
            continue

        if char == "'" and quote is None:
            quote = "single"
        elif char == '"' and quote is None:
            quote = "double"
        elif char == '"' and quote == "double":
            quote = None

    return quote


def _escape_shell_single_quoted(value: str) -> str:
    return value.replace("'", "'\\''")


def _escape_shell_double_quoted(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("$", "\\$")
        .replace("`", "\\`")
    )


def render_shell_template(text: str, values: Mapping[str, Any]) -> str:
    """Render shell placeholders as literal data, with `{name:raw}` as an escape hatch."""

    parts: list[str] = []
    last = 0
    for match in SHELL_PLACEHOLDER_RE.finditer(text):
        parts.append(text[last : match.start()])
        key = match.group(1)
        if key not in values:
            parts.append(match.group(0))
        else:
            value = _stringify_template_value(values[key])
            if match.group(2) == "raw":
                parts.append(value)
            else:
                quote_context = _shell_quote_context(text, match.start())
                if quote_context == "single":
                    parts.append(_escape_shell_single_quoted(value))
                elif quote_context == "double":
                    parts.append(_escape_shell_double_quoted(value))
                else:
                    parts.append(shlex.quote(value))
        last = match.end()
    parts.append(text[last:])
    return "".join(parts)


def render_argv_template(text: str, values: Mapping[str, Any]) -> list[str]:
    """Render shell-style argv templates without splitting substituted values."""

    return [render_template(part, values) for part in shlex.split(text)]


def render_value(value: Any, values: Mapping[str, Any]) -> Any:
    if isinstance(value, str):
        return render_template(value, values)
    if isinstance(value, list):
        return [render_value(item, values) for item in value]
    if isinstance(value, dict):
        return {key: render_value(item, values) for key, item in value.items()}
    return value
