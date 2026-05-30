"""Small templating helpers used by task assets, prompts, and checks."""

from __future__ import annotations

import json
import re
import shlex
from typing import Any, Mapping


PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z0-9_]+)\}")


def render_template(text: str, values: Mapping[str, Any]) -> str:
    """Replace `{name}` placeholders while leaving unrelated braces alone."""

    def repl(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in values:
            return match.group(0)
        value = values[key]
        if isinstance(value, (dict, list)):
            return json.dumps(value)
        return str(value)

    return PLACEHOLDER_RE.sub(repl, text)


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
