from __future__ import annotations

import json

from config_loader import load_config


def test_env_port_overrides_file(tmp_path, monkeypatch):
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({"port": 9000, "debug": False}), encoding="utf-8")
    monkeypatch.setenv("APP_PORT", "9200")
    cfg = load_config(str(config_path))
    assert cfg["port"] == 9200


def test_debug_flag_is_boolean(monkeypatch):
    monkeypatch.setenv("APP_DEBUG", "true")
    cfg = load_config(None)
    assert cfg["debug"] is True

