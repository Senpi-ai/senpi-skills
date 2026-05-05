"""senpi_runtime_helpers — Python wrapper for senpi-skills v2 producers.

Goal: turn good practices into the path of least resistance.

Replaces, for the producer author:
  - `subprocess.run(["mcporter", "call", "senpi", tool, ...])`
    → `client.mcp_call(tool, **args)`  — direct HTTPS, kills the 6-process tree
  - hand-rolled fcntl locks
    → `with scanner_lock("name"): ...`  — liveness-aware, auto-recovers stale locks
  - serial MCP fan-out
    → `parallel([lambda: client.mcp_call(...), ...])`  — concurrency-bounded, queue-not-reject
  - repeated identical MCP calls in one tick
    → `mcp = tick_cache(client); mcp(tool, ...)`
  - `subprocess.run(["openclaw", "senpi", "external-scanner", "ingest", ...])`
    → `client.push_signal(address, scanner, data, ...)`  — POST to runtime API
    → `client.push_signals(items)` for batches

Wall-clock timeouts are enforced by default on every MCP and signal call;
producers can override per-call.

All helpers emit structured `[senpi_helpers]` JSON events to stderr to prove
the fix works (Railway log search).
"""
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
# Source: https://github.com/Senpi-ai/senpi-skills

from .cache import cached_mcp_call, clear_cache, tick_cache
from .client import SenpiClient, SenpiClientError
from .daemon import producer_daemon
from .lock import scanner_lock
from ._logging import enable_logging, log_event
from .parallel import parallel

__all__ = [
    "SenpiClient",
    "SenpiClientError",
    "scanner_lock",
    "parallel",
    "tick_cache",
    "cached_mcp_call",
    "clear_cache",
    "producer_daemon",
    "enable_logging",
    "log_event",
]

__version__ = "0.1.0"
