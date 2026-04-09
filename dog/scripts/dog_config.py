"""DOG Strategy — Shared config, MCP helpers, state I/O."""
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT

import json, os, subprocess, sys, tempfile, time
from datetime import datetime, timezone
from pathlib import Path

WORKSPACE = os.environ.get("OPENCLAW_WORKSPACE", "/data/workspace")
SKILL_DIR = Path(WORKSPACE) / "skills" / "dog-strategy"
CONFIG_PATH = SKILL_DIR / "config" / "dog-config.json"
STATE_DIR = SKILL_DIR / "state"

STATE_DIR.mkdir(parents=True, exist_ok=True)


def atomic_write(path, data):
    path = str(path)
    dir_name = os.path.dirname(path) or "."
    os.makedirs(dir_name, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_path, path)
    except BaseException:
        try: os.unlink(tmp_path)
        except OSError: pass
        raise


def load_config():
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f: return json.load(f)
    return {}


def get_wallet_and_strategy():
    wallet = os.environ.get("DOG_WALLET", "")
    strategy_id = os.environ.get("DOG_STRATEGY_ID", "")
    if not wallet or not strategy_id:
        config = load_config()
        wallet = wallet or config.get("wallet", "")
        strategy_id = strategy_id or config.get("strategyId", "")
    return wallet, strategy_id


def mcporter_call(tool, retries=2, timeout=25, **params):
    args = json.dumps(params) if params else "{}"
    cmd = ["mcporter", "call", "senpi", tool, "--args", args]
    for attempt in range(retries):
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            if r.returncode != 0:
                if attempt < retries - 1: time.sleep(2); continue
                return None
            raw = json.loads(r.stdout)
            if isinstance(raw, dict) and "content" in raw:
                content = raw["content"]
                if isinstance(content, list) and content:
                    first = content[0]
                    if isinstance(first, dict) and "text" in first:
                        try: return json.loads(first["text"])
                        except: pass
            return raw
        except subprocess.TimeoutExpired:
            if attempt < retries - 1: time.sleep(2); continue
            return None
        except: return None
    return None


def get_positions(wallet):
    ch = mcporter_call("strategy_get_clearinghouse_state", strategy_wallet=wallet)
    if not ch: return 0, []
    data = ch.get("data", ch)
    positions, account_value = [], 0
    for section in ("main", "xyz"):
        s = data.get(section, {})
        if not isinstance(s, dict): continue
        ms = s.get("marginSummary", {})
        # CRITICAL: only count accountValue from "main" section.
        # Both main and xyz report the SAME total wallet value.
        # Summing both doubles it (the bug that caused $574 margin instead of $287).
        if section == "main":
            account_value = float(ms.get("accountValue", 0))
        for ap in s.get("assetPositions", []):
            pos = ap.get("position", ap)
            szi = float(pos.get("szi", 0))
            if szi == 0: continue
            positions.append({
                "coin": pos.get("coin", ""),
                "direction": "LONG" if szi > 0 else "SHORT",
                "upnl": float(pos.get("unrealizedPnl", 0)),
                "margin": float(pos.get("marginUsed", 0)),
                "entryPrice": float(pos.get("entryPx", 0)),
                "size": abs(szi),
            })
    return account_value, positions


def output(data):
    print(json.dumps(data))
    sys.stdout.flush()


def log(msg):
    print(f"[DOG] {msg}", file=sys.stderr)
    sys.stderr.flush()
