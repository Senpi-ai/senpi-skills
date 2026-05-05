"""Wrapper-wide config — read once at import, all env-driven, documented defaults."""
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
# Source: https://github.com/Senpi-ai/senpi-skills

import os


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_str(name: str, default: str) -> str:
    raw = os.environ.get(name)
    return raw if raw else default


# MCP endpoint and auth — direct HTTPS, no openclaw gateway, no mcporter.
MCP_URL = _env_str("SENPI_MCP_URL", "https://mcp.prod.senpi.ai/mcp")
SENPI_AUTH_TOKEN = os.environ.get("SENPI_AUTH_TOKEN", "")

# Runtime API for signal ingest.
RUNTIME_API_HOST = _env_str("SENPI_RUNTIME_API_HOST", "127.0.0.1")
RUNTIME_API_PORT = _env_int("SENPI_RUNTIME_API_PORT", 8787)

# Wall-clock timeouts. Enforced default; producer can override per-call.
MCP_TIMEOUT_SECONDS = _env_float("SENPI_HELPERS_MCP_TIMEOUT", 30.0)
SIGNAL_TIMEOUT_SECONDS = _env_float("SENPI_HELPERS_SIGNAL_TIMEOUT", 5.0)

# Concurrency cap on parallel MCP fan-out. Calls beyond this queue, never reject.
MAX_CONCURRENT = _env_int("SENPI_HELPERS_MAX_CONCURRENT", 8)

# Threshold for "queue is getting long" warning. Soft signal, no rejection.
QUEUE_WARN_DEPTH = _env_int("SENPI_HELPERS_QUEUE_WARN_DEPTH", 50)

# Per-tick cache TTL in seconds — the same MCP call within this window returns cached.
TICK_CACHE_TTL = _env_float("SENPI_HELPERS_TICK_CACHE_TTL", 120.0)

# Lock heartbeat freshness. If a held lock's mtime exceeds this, treat as stale.
LOCK_HEARTBEAT_TIMEOUT = _env_float("SENPI_HELPERS_LOCK_HEARTBEAT_TIMEOUT", 300.0)
