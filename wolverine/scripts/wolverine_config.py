"""WOLVERINE v6.0.0 — Shared config + MCP shim + helpers wrapper.

v5.0.0 (2026-05-08): senpi_runtime_helpers migration. mcporter_call
now routes through SenpiClient.mcp_call() (direct HTTPS) instead of
mcporter subprocess.

v6.0.0 (2026-05-18): Patient-Conviction pattern adoption (Bison-port).
HYPE single-asset thesis preserved; behavioral profile shifts from
"4 entries/day on score ≥9" to "2 entries/day on score ≥10 with
wide-DSL multi-day holds." Producer adds the same recent-signals
race-window dedup that Bison v3.0.1 used to eliminate
ENGINE_FAILURE retries on already-emitted assets. See SKILL.md
v6.0.0 changelog for the full rationale + Bison-pattern attribution.
"""
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT

import functools
import json
import os
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

WORKSPACE = os.environ.get("OPENCLAW_WORKSPACE", "/data/workspace")
SKILL_DIR = Path(WORKSPACE) / "skills" / "wolverine-strategy"
CONFIG_PATH = SKILL_DIR / "config" / "wolverine-config.json"
STATE_DIR = SKILL_DIR / "state"
RECENT_SIGNALS_PATH = STATE_DIR / "recent-signals.json"

STATE_DIR.mkdir(parents=True, exist_ok=True)

# v6.0.0: race-window dedup TTL. Wolverine ticks every 180s and HYPE
# ALO fills typically complete within 30-60s. 240s covers the full
# fill window plus the next-tick clearinghouse-state refresh, so the
# on-chain held-asset check picks up where this cache leaves off.
RECENT_SIGNAL_TTL_SEC = 240


# ─── senpi_runtime_helpers (lazy + auth-validated) ───
# Pattern ported verbatim from cheetah/polar/kodiak_config.py: import
# the SDK at module load, defer SenpiClient construction
# until first attribute access. SENPI_AUTH_TOKEN validated on first use.

# senpi_runtime_helpers ships inside the senpi-trading-runtime skill.
# Global skills install under ~/.openclaw/skills/ on standard hosts
# (e.g. /data/.openclaw/skills/ on Railway). Some setups install user
# skills under ${OPENCLAW_WORKSPACE}/skills/. Probe both in order.
_sdk_candidates = [
    str(Path.home() / ".openclaw" / "skills" / "senpi-trading-runtime"),
    str(Path(os.environ.get("OPENCLAW_WORKSPACE", "/data/workspace")) / "skills" / "senpi-trading-runtime"),
]
_sdk_path = next(
    (p for p in _sdk_candidates if (Path(p) / "senpi_runtime_helpers").is_dir()),
    _sdk_candidates[0],
)
if _sdk_path not in sys.path:
    sys.path.insert(0, _sdk_path)
from senpi_runtime_helpers import SenpiClient, log_event  # type: ignore  # noqa: E402


@functools.lru_cache(maxsize=1)
def _get_wrapper_client() -> SenpiClient:
    if not os.environ.get("SENPI_AUTH_TOKEN", "").strip():
        raise RuntimeError(
            "SENPI_AUTH_TOKEN is not set. Wolverine's MCP calls and signal "
            "POST both require it. Set it on the runtime host before "
            "starting the producer daemon."
        )
    client = SenpiClient()
    log_event("wolverine_wrapper_enabled", sdk_path=_sdk_path)
    return client


class _WrapperClientProxy:
    def __getattr__(self, name: str):
        return getattr(_get_wrapper_client(), name)


_wrapper_client = _WrapperClientProxy()


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
    """Call a Senpi MCP tool via the senpi_runtime_helpers wrapper.

    v5.0.0: routes through SenpiClient.mcp_call() — direct HTTPS, no
    mcporter subprocess. Returns the unwrapped JSON document on
    success, or None if the wrapper raised. `retries` parameter
    preserved for call-site compat but not implemented — daemon
    recovers transient failures on the next tick.
    """
    try:
        return _wrapper_client.mcp_call(tool, timeout=timeout, **params)
    except Exception as e:  # noqa: BLE001 — transport / protocol surface
        sys.stderr.write(
            f"[senpi_helpers] wolverine_mcp_call_failed tool={tool} "
            f"err={type(e).__name__}: {e}\n"
        )
        return None


# ─── v6.0.0 atomic-write + recent-signals cache ─────────────
#
# Mirrors the bison v3.0.1 race-window dedup. After push_signal(coin)
# returns OK, record_signal(coin) writes {coin: epoch_seconds} to
# state/recent-signals.json. main() calls was_recently_signaled(coin)
# BEFORE fetching held-assets and aborts the tick if the cache says
# "in flight." Covers the ~30-90s gap between push_signal returning
# OK and the resulting position appearing in the next-tick
# clearinghouseState pull. Stale entries (older than 4× TTL) are
# pruned on read to keep the file bounded.

def atomic_write(path, data):
    """Write JSON atomically via tmp file + os.replace."""
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


def _read_recent_signals():
    if not RECENT_SIGNALS_PATH.exists():
        return {}
    try:
        with open(RECENT_SIGNALS_PATH) as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {}
        return {k: float(v) for k, v in data.items() if isinstance(v, (int, float))}
    except (json.JSONDecodeError, OSError, ValueError):
        return {}


def _prune_recent_signals(signals, now):
    cutoff = now - (RECENT_SIGNAL_TTL_SEC * 4)
    return {k: v for k, v in signals.items() if v >= cutoff}


def record_signal(coin):
    if not coin:
        return
    now = time.time()
    signals = _prune_recent_signals(_read_recent_signals(), now)
    signals[coin.upper()] = now
    try:
        atomic_write(RECENT_SIGNALS_PATH, signals)
    except OSError:
        # Best-effort; on-chain held-asset check is the safety floor.
        pass


def was_recently_signaled(coin, ttl_sec=RECENT_SIGNAL_TTL_SEC):
    if not coin:
        return False
    signals = _read_recent_signals()
    last = signals.get(coin.upper())
    if last is None:
        return False
    return (time.time() - last) < ttl_sec


def output(data):
    print(json.dumps(data, default=str))
    sys.stdout.flush()


def log(msg):
    print(f"[WOLVERINE-v4] {msg}", file=sys.stderr)


def now_ts():
    return time.time()


def now_iso():
    return datetime.now(timezone.utc).isoformat()
