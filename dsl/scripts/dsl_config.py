#!/usr/bin/env python3
"""
dsl_config.py — Layered configuration for DSL.

Resolution order (lowest → highest):
  1. skill.json defaultConfig (packaged defaults)
  2. /data/workspace/config/dsl.json (user install config)
  3. state/dsl/{strategyKey}/strategy.json "config" block (per-strategy, when strategy_key given)
  4. Environment variables DSL_*
  5. CLI arguments (when passed to resolve_config)

Invalid config keys are ignored; missing keys use defaults. No schema validation in this module
(schema/ used for documentation and optional validation elsewhere).
"""
from __future__ import annotations

import json
import os


def _data_dir() -> str:
    return os.environ.get(
        "DSL_STATE_DIR",
        os.environ.get("WOLF_WORKSPACE", os.environ.get("OPENCLAW_WORKSPACE", "/data/workspace")),
    )


def _skill_dir() -> str:
    return os.path.normpath(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    )


def _load_json(path: str) -> dict | None:
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return None


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge override into base. base is copied."""
    out = dict(base)
    for k, v in override.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _env_overrides() -> dict:
    """Build config overrides from DSL_* environment variables."""
    overrides: dict = {}
    v = os.environ.get("DSL_MODEL")
    if v is not None:
        overrides.setdefault("model", {})["primary"] = v
    v = os.environ.get("DSL_CRON_INTERVAL")
    if v is not None:
        try:
            overrides.setdefault("cron", {})["intervalSeconds"] = int(v)
        except ValueError:
            pass
    v = os.environ.get("DSL_CRON_MODE")
    if v is not None:
        overrides.setdefault("cron", {})["mode"] = v
    v = os.environ.get("DSL_OUTPUT_LEVEL")
    if v is not None:
        overrides.setdefault("execution", {})["outputLevel"] = v
    v = os.environ.get("DSL_NAMESPACE")
    if v is not None:
        overrides.setdefault("state", {})["namespace"] = v
    v = os.environ.get("DSL_RETENTION_DAYS")
    if v is not None:
        try:
            overrides.setdefault("state", {})["retentionDays"] = int(v)
        except ValueError:
            pass
    v = os.environ.get("DSL_MAX_HISTORY")
    if v is not None:
        try:
            overrides.setdefault("state", {})["maxHistoryPerPosition"] = int(v)
        except ValueError:
            pass
    return overrides


def resolve_config(
    strategy_key: str | None = None,
    cli_overrides: dict | None = None,
) -> dict:
    """
    Resolve DSL config from all layers.

    Args:
        strategy_key: If set, load strategy.json from state/dsl/{strategy_key}/ and merge its config.
        cli_overrides: Optional dict of overrides (e.g. from --config-* CLI args).

    Returns:
        Full config dict with model, cron, state, execution keys.
    """
    data = _data_dir()
    skill_dir = _skill_dir()

    # 1. skill.json defaultConfig
    manifest_path = os.path.join(skill_dir, "skill.json")
    defaults = {}
    if os.path.isfile(manifest_path):
        with open(manifest_path) as f:
            manifest = json.load(f)
        defaults = dict(manifest.get("defaultConfig", {}))

    # 2. config/dsl.json
    user_config_path = os.path.join(data, "config", "dsl.json")
    user_config = _load_json(user_config_path) or {}
    cfg = _deep_merge(defaults, user_config)

    # 3. strategy.json config block
    if strategy_key:
        strategy_path = os.path.join(data, "state", "dsl", strategy_key, "strategy.json")
        strategy_data = _load_json(strategy_path)
        if strategy_data and isinstance(strategy_data.get("config"), dict):
            cfg = _deep_merge(cfg, strategy_data["config"])

    # 4. Environment
    cfg = _deep_merge(cfg, _env_overrides())

    # 5. CLI
    if cli_overrides:
        cfg = _deep_merge(cfg, cli_overrides)

    return cfg


def get_state_file_path() -> str:
    """Single-position state file from DSL_STATE_FILE or default."""
    return os.environ.get(
        "DSL_STATE_FILE",
        os.path.join(_data_dir(), "trailing-stop-state.json"),
    )


def get_registry_path() -> str:
    """Registry JSON path for multi mode (DSL_REGISTRY or default)."""
    return os.environ.get(
        "DSL_REGISTRY",
        os.path.join(_data_dir(), "wolf-strategies.json"),
    )
