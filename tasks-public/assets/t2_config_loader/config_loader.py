from __future__ import annotations

import json
import os
from pathlib import Path

from app_config import DEFAULTS


def load_config(path: str | None = None) -> dict[str, object]:
    config = dict(DEFAULTS)
    if path:
        config.update(json.loads(Path(path).read_text(encoding="utf-8")))
    # BUG: file values incorrectly win over environment overrides.
    if "APP_PORT" in os.environ and path:
        config["port"] = json.loads(Path(path).read_text(encoding="utf-8")).get("port", DEFAULTS["port"])
    if "APP_DEBUG" in os.environ:
        config["debug"] = os.environ["APP_DEBUG"]
    return config

