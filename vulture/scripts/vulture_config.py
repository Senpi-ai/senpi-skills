"""VULTURE v3.0.0 — Shared MCP helpers + config loader.

v3.0 producer responsibilities are narrower than v2.x:
  - Fetch market universe via MCP (leaderboard_get_markets)
  - Apply scoring + hard gates
  - Push signals via `openclaw senpi external-scanner ingest`
    (runtime owns execution + DSL + risk + counters)

This module is just the MCP call helper + config loader. All state
(position tracking, trade counters, cooldowns, dynamic slots) lives
in the runtime, not here. v2.x's set_cooldown / load_trade_counter /
save_state functions are GONE — they were the source of the
cfg.set_cooldown silent crash that blew out 27 trades' telemetry.
"""
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

WORKSPACE = os.environ.get("OPENCLAW_WORKSPACE", "/data/workspace")
SKILL_DIR = Path(WORKSPACE) / "skills" / "vulture-strategy"
CONFIG_PATH = SKILL_DIR / "config" / "vulture-config.json"


# ─── Config ──────────────────────────────────────────────────

def load_config():
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {}


# ─── MCP Helper ──────────────────────────────────────────────

def mcporter_call(tool, retries=2, timeout=30, **params):
    """Call a Senpi MCP tool via mcporter. Returns parsed JSON or None on failure."""
    args = json.dumps(params) if params else "{}"
    cmd = ["mcporter", "call", "senpi", tool, "--args", args]
    for attempt in range(retries):
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            if r.returncode != 0:
                if attempt < retries - 1:
                    time.sleep(2)
                    continue
                return None
            raw = json.loads(r.stdout)
            if isinstance(raw, dict) and "content" in raw:
                content = raw["content"]
                if isinstance(content, list) and content:
                    first = content[0]
                    if isinstance(first, dict) and "text" in first:
                        try:
                            return json.loads(first["text"])
                        except (json.JSONDecodeError, TypeError):
                            pass
            return raw
        except subprocess.TimeoutExpired:
            if attempt < retries - 1:
                time.sleep(2)
                continue
            return None
        except (json.JSONDecodeError, Exception):
            return None
    return None


def output(data):
    print(json.dumps(data, default=str))
    sys.stdout.flush()


def log(msg):
    print(f"[VULTURE-v3] {msg}", file=sys.stderr)


def now_ts():
    return time.time()


def now_iso():
    return datetime.now(timezone.utc).isoformat()
