"""Structured JSON logging — every helper emits `[senpi_helpers]` events that prove the fix works.

Keep stdout clean for skills that print signal JSON to stdout (Phoenix, etc.).
All wrapper logs go to stderr to avoid contaminating stdout-based protocols.
"""
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT

import json
import logging
import os
import sys
import time
from typing import Any


PREFIX = "[senpi_helpers]"

_initialized = False


def _emit(event: str, **fields: Any) -> None:
    """Write one structured line to stderr. Robust to non-serialisable values."""
    now = time.time()
    ms = int((now - int(now)) * 1000)
    payload = {
        "ts": now,
        "iso": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(now)) + f".{ms:03d}Z",
        "pid": os.getpid(),
        "event": event,
    }
    for k, v in fields.items():
        try:
            json.dumps(v)
            payload[k] = v
        except (TypeError, ValueError):
            payload[k] = repr(v)
    try:
        line = f"{PREFIX} {json.dumps(payload, default=str)}"
    except Exception:
        line = f"{PREFIX} event={event}"
    print(line, file=sys.stderr, flush=True)


def log_event(event: str, **fields: Any) -> None:
    """Public entry point — emit one structured `[senpi_helpers]` event."""
    _emit(event, **fields)


def enable_logging(level: int = logging.INFO) -> None:
    """One-call setup. Idempotent. Anchors the standard logging module too."""
    global _initialized
    if _initialized:
        return
    _initialized = True
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter(f"{PREFIX} %(message)s"))
    root = logging.getLogger("senpi_runtime_helpers")
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
    root.propagate = False
    _emit("logger_initialized", level=logging.getLevelName(level))
