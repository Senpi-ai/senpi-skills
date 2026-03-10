import json
import os
from typing import Any, Dict


WORKSPACE = os.environ.get("OPENCLAW_WORKSPACE", os.path.expanduser("~/.openclaw/workspace"))
CONFIG_FILE = os.path.join(WORKSPACE, "fortress-config.json")
STATE_DIR = os.path.join(WORKSPACE, "state", "fortress-default")


def deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def defaults() -> Dict[str, Any]:
    return {
        "version": 1,
        "instanceKey": "fortress-default",
        "market": {
            "asset": "HYPE",
            "direction": "SHORT"
        },
        "consensus": {
            "requiredUnanimous": True,
            "minAverageConviction": 3.0,
            "enabledPillars": ["oracle", "ta", "vol", "risk"]
        },
        "risk": {
            "maxLossUsd": 75,
            "maxLossPct": 5
        }
    }


def ensure_dirs() -> None:
    os.makedirs(STATE_DIR, exist_ok=True)


def load_config() -> Dict[str, Any]:
    cfg = defaults()
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as handle:
            user_cfg = json.load(handle)
        return deep_merge(cfg, user_cfg)
    except FileNotFoundError:
        return cfg
    except json.JSONDecodeError:
        return cfg


def state_path(name: str) -> str:
    ensure_dirs()
    return os.path.join(STATE_DIR, f"{name}.json")