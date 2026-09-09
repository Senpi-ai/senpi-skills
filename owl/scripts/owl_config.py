"""OWL Strategy — Shared config loader and utilities."""

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

WORKSPACE = os.environ.get("OPENCLAW_WORKSPACE", "/data/workspace")
OWL_DIR = Path(WORKSPACE) / "skills" / "owl-strategy"
CONFIG_PATH = OWL_DIR / "config" / "owl-config.json"
STATE_DIR_BASE = OWL_DIR / "state"


def load_global_config() -> dict:
    """Load owl-config.json from the config directory."""
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f:
            return json.load(f)
    # Fallback: check workspace root
    alt = Path(WORKSPACE) / "config" / "owl-config.json"
    if alt.exists():
        with open(alt) as f:
            return json.load(f)
    return {}


def get_wallet_and_strategy() -> tuple:
    """Get wallet and strategy ID from env vars or config."""
    wallet = os.environ.get("OWL_WALLET", "")
    strategy_id = os.environ.get("OWL_STRATEGY_ID", "")
    if not wallet or not strategy_id:
        config = load_global_config()
        wallet = wallet or config.get("wallet", "")
        strategy_id = strategy_id or config.get("strategyId", "")
    return wallet, strategy_id


def get_state_dir() -> Path:
    """Get the strategy state directory."""
    _, strategy_id = get_wallet_and_strategy()
    if strategy_id:
        return STATE_DIR_BASE / strategy_id
    # Fallback: find first state dir
    dirs = get_strategy_dirs()
    return dirs[0] if dirs else STATE_DIR_BASE / "default"


def get_strategy_dirs():
    """Find all OWL strategy state directories."""
    dirs = []
    if STATE_DIR_BASE.exists():
        for d in STATE_DIR_BASE.iterdir():
            if d.is_dir() and (d / "owl-state.json").exists():
                dirs.append(d)
    return dirs


def load_state(state_dir: Path) -> dict:
    """Load owl-state.json from a strategy state directory."""
    p = state_dir / "owl-state.json"
    if not p.exists():
        return {}
    with open(p) as f:
        return json.load(f)


def load_config(state_dir: Path) -> dict:
    """Load owl-config.json from a strategy state directory."""
    p = state_dir / "owl-config.json"
    if not p.exists():
        return {}
    with open(p) as f:
        return json.load(f)


def load_crowding_history(state_dir: Path) -> dict:
    """Load crowding-history.json."""
    p = state_dir / "crowding-history.json"
    if not p.exists():
        return {"version": 1, "snapshots": {}, "persistenceCount": {}, "oiBaselines": {}}
    with open(p) as f:
        return json.load(f)


def atomic_write(path, data: dict):
    """Write JSON atomically via tmpfile + os.replace. Safe across filesystems."""
    path = str(path)
    dir_name = os.path.dirname(path) or "."
    os.makedirs(dir_name, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def save_state(state_dir: Path, state: dict):
    """Save owl-state.json atomically."""
    from datetime import datetime, timezone
    state["updatedAt"] = datetime.now(timezone.utc).isoformat()
    atomic_write(state_dir / "owl-state.json", state)


def save_crowding_history(state_dir: Path, history: dict):
    """Save crowding-history.json atomically."""
    from datetime import datetime, timezone
    history["updatedAt"] = datetime.now(timezone.utc).isoformat()
    atomic_write(state_dir / "crowding-history.json", history)


def mcporter_call(tool: str, args: dict = None, timeout: int = 30) -> dict:
    """Call a Senpi MCP tool via mcporter CLI. Returns parsed JSON."""
    if args:
        args_json = json.dumps(args)
        cmd = ["mcporter", "call", f"senpi.{tool}", "--args", args_json]
    else:
        cmd = ["mcporter", "call", f"senpi.{tool}"]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode != 0:
            return {"success": False, "error": result.stderr.strip()}
        return json.loads(result.stdout)
    except subprocess.TimeoutExpired:
        return {"success": False, "error": f"mcporter timeout ({timeout}s)"}
    except json.JSONDecodeError:
        return {"success": False, "error": "Invalid JSON from mcporter"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def output(data: dict):
    """Print JSON output for cron consumption."""
    print(json.dumps(data))
    sys.stdout.flush()


# ── Technical Indicators ──

def calc_rsi(closes: list, period: int = 14) -> float | None:
    """Calculate RSI from a list of close prices."""
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        delta = closes[i] - closes[i - 1]
        gains.append(max(0, delta))
        losses.append(max(0, -delta))
    # Use last `period` values
    gains = gains[-period:]
    losses = losses[-period:]
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def calc_sma(values: list, period: int) -> float | None:
    """Simple moving average of last `period` values."""
    if len(values) < period:
        return None
    return sum(values[-period:]) / period


def calc_bb_width(closes: list, period: int = 20, std_mult: float = 2.0) -> float | None:
    """Bollinger Band width as percentage of middle band."""
    if len(closes) < period:
        return None
    window = closes[-period:]
    sma = sum(window) / period
    if sma == 0:
        return None
    variance = sum((x - sma) ** 2 for x in window) / period
    std = variance ** 0.5
    width = (2 * std_mult * std) / sma
    return width


def extract_closes(candles: list) -> list:
    """Extract close prices from candle data (Senpi format)."""
    closes = []
    for c in candles:
        close = c.get("close") or c.get("c")
        if close is not None:
            closes.append(float(close))
    return closes


def extract_volumes(candles: list) -> list:
    """Extract volumes from candle data."""
    vols = []
    for c in candles:
        vol = c.get("volume") or c.get("v") or c.get("vlm")
        if vol is not None:
            vols.append(float(vol))
    return vols
