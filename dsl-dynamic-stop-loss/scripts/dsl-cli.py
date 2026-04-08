#!/usr/bin/env python3
"""DSL lifecycle CLI — add/update/pause/resume/delete/status/count DSL for a strategy.
Reads/writes strategy config (strategy-<id>.json) and per-position state files; outputs OpenClaw cron intent (create/remove) for the agent.
See design/dsl-cli-commands.md and references/cli-usage.md.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

DSL_STATE_DIR = os.environ.get("DSL_STATE_DIR", "/data/workspace/dsl")


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _exit_error(msg: str) -> None:
    print(json.dumps({"status": "error", "error": msg}))
    sys.exit(1)


def _safe_float(value: Any, default: float = 0.0) -> float:
    """Convert to float without raising; return default on failure."""
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default

# ---------------------------------------------------------------------------
# Path helpers (aligned with dsl-v5.py)
# ---------------------------------------------------------------------------


def cron_schedule_from_interval_minutes(minutes: int) -> str:
    """Build cron expression for 'every N minutes'. minutes in 1–1440. Default 3 → '*/3 * * * *'."""
    n = max(1, min(1440, int(minutes)))
    if n <= 59:
        return f"*/{n} * * * *"
    if n == 60:
        return "0 * * * *"
    hours = n // 60
    return f"0 */{hours} * * *"


def asset_to_filename(asset: str) -> str:
    """xyz:SILVER → xyz--SILVER (filesystem-safe)."""
    if asset.startswith("xyz:"):
        return asset.replace(":", "--", 1)
    return asset


def filename_to_asset(filename: str) -> str | None:
    """xyz--SILVER.json → xyz:SILVER; ETH.json → ETH. Returns None for strategy config (strategy-*.json)."""
    if not filename.endswith(".json"):
        return None
    if filename.startswith("strategy-"):
        return None
    base = filename[:-5]
    if "--" in base and not base.startswith("xyz--"):
        return None
    if base.startswith("xyz--"):
        return "xyz:" + base[5:]
    return base


def _safe_path_component(s: str) -> bool:
    """Return True if s is safe for use as a path component (no path separators or parent refs)."""
    return bool(s) and os.path.sep not in s and s not in ("", ".", "..")


def strategy_dir(state_dir: str, strategy_id: str) -> str:
    return os.path.join(state_dir, strategy_id)


def strategy_config_filename(strategy_id: str) -> str:
    """Strategy config filename including strategy ID (e.g. strategy-<uuid>.json)."""
    return f"strategy-{strategy_id}.json"


def strategy_json_path(state_dir: str, strategy_id: str) -> str:
    return os.path.join(strategy_dir(state_dir, strategy_id), strategy_config_filename(strategy_id))


def position_state_path(state_dir: str, strategy_id: str, asset: str) -> str:
    return os.path.join(strategy_dir(state_dir, strategy_id), f"{asset_to_filename(asset)}.json")


def list_position_state_files(state_dir: str, strategy_id: str) -> list[tuple[str, str]]:
    """(path, asset) for each position .json; excludes strategy config (strategy-*.json) and *_archived_*."""
    out = []
    sd = strategy_dir(state_dir, strategy_id)
    if not os.path.isdir(sd):
        return out
    for name in os.listdir(sd):
        if name.startswith("strategy-") or "_archived" in name or ".archived" in name:
            continue
        if not name.endswith(".json"):
            continue
        path = os.path.join(sd, name)
        if not os.path.isfile(path):
            continue
        asset = filename_to_asset(name)
        if asset is not None:
            out.append((path, asset))
    return out


# ---------------------------------------------------------------------------
# MCP (mcporter) helpers
# ---------------------------------------------------------------------------

# Retries: 2-tuple (result, error) success when error is None; 3-tuple (success, *rest) success when first is True.
def _mcp_result_ok(result: Any) -> bool:
    if result is None or not isinstance(result, tuple) or len(result) < 2:
        return False
    if len(result) == 2:
        return result[1] is None
    return result[0] is True


def _retry_mcp_call(
    fn: Callable[..., Any],
    *args: Any,
    max_attempts: int = 4,
    delay_seconds: float = 1.0,
    **kwargs: Any,
) -> Any:
    """Run fn(*args, **kwargs); on failure, retry up to max_attempts (1 initial + 3 retries)."""
    last_result: Any = None
    for attempt in range(max_attempts):
        try:
            last_result = fn(*args, **kwargs)
            if _mcp_result_ok(last_result):
                return last_result
        except Exception as e:
            last_result = (None, str(e))  # 2-tuple style so _mcp_result_ok is False
        if attempt + 1 < max_attempts:
            time.sleep(delay_seconds)
    return last_result


def _unwrap_mcporter_response(stdout_str: str) -> dict | None:
    try:
        raw = json.loads(stdout_str)
    except json.JSONDecodeError:
        return None
    if not isinstance(raw, dict):
        return None
    content = raw.get("content")
    if isinstance(content, list) and len(content) > 0:
        first = content[0]
        if isinstance(first, dict):
            text = first.get("text")
            if isinstance(text, str) and text.strip():
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    return None
    return raw


def _mcp_strategy_get_once(strategy_id: str) -> tuple[dict | None, str | None]:
    """Single attempt: (strategy dict, error)."""
    try:
        r = subprocess.run(
            ["mcporter", "call", "senpi", "strategy_get", "--args", json.dumps({"strategy_id": strategy_id})],
            capture_output=True, text=True, timeout=20,
        )
        if r.returncode != 0:
            return None, (r.stderr or r.stdout or "non-zero exit")
        raw = _unwrap_mcporter_response(r.stdout or "")
        if not raw:
            return None, "strategy_get: invalid or empty response"
        if raw.get("success") is False:
            err = raw.get("error", {})
            msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
            return None, msg
        data = raw.get("data") or raw
        strategy = data.get("strategy") if isinstance(data, dict) else None
        if not strategy or not isinstance(strategy, dict):
            return None, "strategy_get: no strategy in response"
        return strategy, None
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        return None, str(e)


def mcp_strategy_get(strategy_id: str) -> tuple[dict | None, str | None]:
    """Returns (strategy dict with strategyWalletAddress, status, ...), error. 4 attempts (1 initial + 3 retries) on failure."""
    return _retry_mcp_call(_mcp_strategy_get_once, strategy_id)


def _mcp_clearinghouse_once(wallet: str) -> tuple[dict | None, str | None]:
    """Single attempt: (data with main/xyz and assetPositions), error."""
    try:
        r = subprocess.run(
            ["mcporter", "call", "senpi", "strategy_get_clearinghouse_state", "--args", json.dumps({"strategy_wallet": wallet})],
            capture_output=True, text=True, timeout=20,
        )
        if r.returncode != 0:
            return None, (r.stderr or r.stdout or "non-zero exit")
        raw = _unwrap_mcporter_response(r.stdout or "")
        if not raw:
            return None, "clearinghouse: invalid or empty response"
        data = raw.get("data") if isinstance(raw.get("data"), dict) else raw
        return data, None
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        return None, str(e)


def mcp_clearinghouse(wallet: str) -> tuple[dict | None, str | None]:
    """Returns (data with main/xyz and assetPositions), error. 4 attempts (1 initial + 3 retries) on failure."""
    return _retry_mcp_call(_mcp_clearinghouse_once, wallet)


def get_positions_from_clearinghouse(wallet: str) -> tuple[list[dict], str | None]:
    """Returns (list of {coin, dex, entryPx, size, leverage, direction}), error.
    coin is e.g. ETH or xyz:SILVER; dex is 'main' or 'xyz'.
    """
    data, err = mcp_clearinghouse(wallet)
    if err is not None:
        return [], err
    if not isinstance(data, dict):
        return [], "clearinghouse: invalid or non-dict response"
    out = []
    for section, dex in (("main", "main"), ("xyz", "xyz")):
        if section not in data:
            continue
        section_data = data.get(section)
        if not isinstance(section_data, dict):
            continue
        for p in section_data.get("assetPositions", []):
            if not isinstance(p, dict):
                continue
            pos = p.get("position", {})
            if not isinstance(pos, dict):
                continue
            coin = pos.get("coin")
            if not coin or not isinstance(coin, str):
                continue
            szi = _safe_float(pos.get("szi"), 0.0)
            if szi == 0:
                continue
            margin_used = _safe_float(pos.get("marginUsed"), 0.0)
            pos_value = _safe_float(pos.get("positionValue"), 0.0)
            leverage = round(pos_value / margin_used, 1) if margin_used > 0 else None
            out.append({
                "coin": coin,
                "dex": dex,
                "entryPx": _safe_float(pos.get("entryPx"), 0.0),
                "size": abs(szi),
                "leverage": leverage,
                "direction": "SHORT" if szi < 0 else "LONG",
            })
    return out, None


# ---------------------------------------------------------------------------
# Config: defaults and resolution (configuration only, no presets)
# ---------------------------------------------------------------------------

# Legacy fixed-ROE tiers (used only when a config explicitly uses lockMode fixed_roe).
DEFAULT_TIERS = [
    {"triggerPct": 10, "lockPct": 5},
    {"triggerPct": 20, "lockPct": 14},
    {"triggerPct": 30, "lockPct": 22, "retrace": 0.012},
    {"triggerPct": 50, "lockPct": 40, "retrace": 0.010},
    {"triggerPct": 75, "lockPct": 60, "retrace": 0.008},
    {"triggerPct": 100, "lockPct": 80, "retrace": 0.006},
]

# Default when no dsl-profile is supplied: high water mode (pct_of_high_water) per dsl-profile.json.
DEFAULT_LOCK_MODE = "pct_of_high_water"
DEFAULT_PHASE2_TRIGGER_ROE = 7
DEFAULT_TIERS_HIGH_WATER = [
    {"triggerPct": 7, "lockHwPct": 40, "consecutiveBreachesRequired": 3},
    {"triggerPct": 12, "lockHwPct": 55, "consecutiveBreachesRequired": 2},
    {"triggerPct": 15, "lockHwPct": 75, "consecutiveBreachesRequired": 2},
    {"triggerPct": 20, "lockHwPct": 85, "consecutiveBreachesRequired": 1},
]


def _default_tiers_for_lock_mode(lock_mode: str | None) -> list:
    """Return default tiers for the given lockMode. fixed_roe uses lockPct tiers; else high-water."""
    if lock_mode == "fixed_roe":
        return list(DEFAULT_TIERS)
    return list(DEFAULT_TIERS_HIGH_WATER)


def normalize_asset_dex(asset: str, dex: str) -> tuple[str, str]:
    """Keep asset and dex in sync: if dex is xyz but asset has no xyz: prefix, prefix asset;
    if asset has xyz: prefix but dex is not xyz, set dex to xyz. Returns (normalized_asset, normalized_dex)."""
    a = (asset or "").strip()
    d = (dex or "").strip().lower()
    if d == "xyz" and a and not a.startswith("xyz:"):
        a = "xyz:" + a
    if a.startswith("xyz:") and d != "xyz":
        d = "xyz"
    return (a, d)


def validate_cli_args(
    strategy_id: str | None = None,
    asset: str | None = None,
    dex: str | None = None,
    require_asset_dex_together: bool = True,
) -> list[str]:
    """Validate common CLI args. Returns list of error messages; empty = valid."""
    errors: list[str] = []
    if strategy_id is not None:
        sid = (strategy_id or "").strip()
        if not sid:
            errors.append("strategy_id is required")
        elif not _safe_path_component(sid):
            errors.append("strategy_id must be path-safe (no path separators or . / ..)")
    if asset is not None or dex is not None:
        a = (asset or "").strip() if asset is not None else ""
        d = (dex or "").strip().lower() if dex is not None else ""
        if a and (os.path.sep in a or a in (".", "..")):
            errors.append("asset must not contain path separators or . / ..")
        if require_asset_dex_together and (bool(a) != bool(d)):
            errors.append("asset and dex must both be set or both omitted")
        if d and d not in ("main", "xyz"):
            errors.append("dex must be 'main' or 'xyz'")
    return errors


def validate_dsl_config(cfg: dict) -> list[str]:
    """Validate DSL config (phase1, phase2, phase2TriggerTier, tiers). Returns list of error messages; empty = valid."""
    errors: list[str] = []
    if not isinstance(cfg, dict):
        return ["configuration must be a JSON object"]

    phase1 = cfg.get("phase1")
    phase2 = cfg.get("phase2")
    if phase1 is not None and not isinstance(phase1, dict):
        errors.append("phase1 must be an object")
    if phase2 is not None and not isinstance(phase2, dict):
        errors.append("phase2 must be an object")

    if isinstance(phase1, dict):
        en = phase1.get("enabled")
        if en is not None and not isinstance(en, bool):
            errors.append("phase1.enabled must be true or false")
        rt = phase1.get("retraceThreshold")
        if rt is not None:
            r = _safe_float(rt, -1)
            if r < 0 or r > 1:
                errors.append("phase1.retraceThreshold must be a number between 0 and 1 (ROE fraction)")
        cb = phase1.get("consecutiveBreachesRequired")
        if cb is not None:
            c = _safe_int(cb, -1)
            if c < 1:
                errors.append("phase1.consecutiveBreachesRequired must be an integer >= 1")

    if isinstance(phase2, dict):
        en = phase2.get("enabled")
        if en is not None and not isinstance(en, bool):
            errors.append("phase2.enabled must be true or false")
        rt = phase2.get("retraceThreshold")
        if rt is not None:
            r = _safe_float(rt, -1)
            if r < 0 or r > 1:
                errors.append("phase2.retraceThreshold must be a number between 0 and 1 (ROE fraction)")
        cb = phase2.get("consecutiveBreachesRequired")
        if cb is not None:
            c = _safe_int(cb, -1)
            if c < 1:
                errors.append("phase2.consecutiveBreachesRequired must be an integer >= 1")

    pt = cfg.get("phase2TriggerTier")
    if pt is not None:
        p = _safe_int(pt, -1)
        if p < 0:
            errors.append("phase2TriggerTier must be a non-negative integer")

    tiers = cfg.get("tiers") or (phase2.get("tiers") if isinstance(phase2, dict) else None)
    if tiers is not None and not isinstance(tiers, list):
        errors.append("tiers must be an array")
    elif isinstance(tiers, list):
        lock_mode = cfg.get("lockMode", "fixed_roe")
        prev_trigger = -1
        prev_lock_hw = -1
        for i, t in enumerate(tiers):
            if not isinstance(t, dict):
                errors.append(f"tiers[{i}] must be an object")
                continue
            tp = t.get("triggerPct")
            if tp is None:
                errors.append(f"tiers[{i}]: triggerPct is required")
            else:
                tv = _safe_float(tp, -1)
                if tv < 0 or tv > 200:
                    errors.append(f"tiers[{i}]: triggerPct must be a number 0–200 (ROE %)")
                elif tv <= prev_trigger:
                    errors.append(f"tiers[{i}]: triggerPct must be strictly greater than previous tier ({prev_trigger})")
                prev_trigger = tv
            if lock_mode == "pct_of_high_water":
                lhw = t.get("lockHwPct")
                if lhw is None:
                    errors.append(f"tiers[{i}]: lockHwPct is required when lockMode is pct_of_high_water")
                else:
                    lhwv = _safe_float(lhw, -1)
                    if lhwv < 0 or lhwv > 100:
                        errors.append(f"tiers[{i}]: lockHwPct must be a number between 0 and 100")
                    elif lhwv <= prev_lock_hw:
                        errors.append(f"tiers[{i}]: lockHwPct must be strictly greater than previous tier ({prev_lock_hw})")
                    prev_lock_hw = lhwv
                cbr = t.get("consecutiveBreachesRequired")
                if cbr is not None:
                    cbrv = _safe_int(cbr, -1)
                    if cbrv < 1 or cbrv > 5:
                        errors.append(f"tiers[{i}]: consecutiveBreachesRequired must be an integer 1–5")
            else:
                lp = t.get("lockPct")
                if lp is None:
                    errors.append(f"tiers[{i}]: lockPct is required")
                else:
                    lv = _safe_float(lp, -1)
                    if lv < 0 or lv > 100:
                        errors.append(f"tiers[{i}]: lockPct must be a number between 0 and 100")
            ret = t.get("retrace")
            if ret is not None:
                rv = _safe_float(ret, -1)
                if rv < 0 or rv > 1:
                    errors.append(f"tiers[{i}]: retrace must be a number between 0 and 1 (ROE fraction)")

    # Optional: cronIntervalMinutes (strategy/position). Minimum for phase1 time-based cuts.
    cron_interval = cfg.get("cronIntervalMinutes")
    cron_val = _safe_int(cron_interval, -1) if cron_interval is not None else 3
    if cron_interval is not None and (cron_val < 1 or cron_val > 1440):
        errors.append("cronIntervalMinutes must be an integer between 1 and 1440 (minutes)")
    min_cron_minutes = cron_val if (cron_interval is not None and cron_val >= 1) else 3

    # Optional: phase1 time-based cut objects (hardTimeout, weakPeakCut, deadWeightCut).
    # Minimum allowed intervalInMinutes when enabled = cron interval.
    if isinstance(phase1, dict):
        cron_interval_minutes = min_cron_minutes
        for block_name, block_schema in (
            ("hardTimeout", ("intervalInMinutes",)),
            ("weakPeakCut", ("intervalInMinutes", "minValue")),
            ("deadWeightCut", ("intervalInMinutes",)),
        ):
            blk = phase1.get(block_name)
            if blk is not None and not isinstance(blk, dict):
                errors.append(f"phase1.{block_name} must be an object")
                continue
            if isinstance(blk, dict):
                en = blk.get("enabled")
                if en is not None and not isinstance(en, bool):
                    errors.append(f"phase1.{block_name}.enabled must be true or false")
                interval = blk.get("intervalInMinutes")
                if interval is not None:
                    vi = _safe_int(interval, -1)
                    if vi < 0:
                        errors.append(f"phase1.{block_name}.intervalInMinutes must be a non-negative integer")
                    elif blk.get("enabled") and vi < cron_interval_minutes:
                        errors.append(
                            f"phase1.{block_name}.intervalInMinutes ({vi}) must be >= cronIntervalMinutes ({cron_interval_minutes}) when enabled"
                        )
                elif blk.get("enabled"):
                    errors.append(
                        f"phase1.{block_name}.intervalInMinutes required when enabled (min: cronIntervalMinutes = {cron_interval_minutes})"
                    )
                if "minValue" in block_schema and block_name == "weakPeakCut":
                    mv = blk.get("minValue")
                    if mv is not None:
                        mvf = _safe_float(mv, -1)
                        if mvf < 0 or mvf > 100:
                            errors.append("phase1.weakPeakCut.minValue must be a number 0–100 (ROE % for weak-peak threshold)")

    # Only require "at least one phase enabled" when the config actually has phase objects
    # (partial patches for update-dsl may omit phase1/phase2 and will be merged with existing config).
    phase1_enabled = isinstance(phase1, dict) and phase1.get("enabled", True)
    phase2_enabled = isinstance(phase2, dict) and phase2.get("enabled", True)
    has_phase_blocks = isinstance(phase1, dict) or isinstance(phase2, dict)
    if has_phase_blocks and not phase1_enabled and not phase2_enabled:
        errors.append("at least one of phase1.enabled or phase2.enabled must be true")
    if phase2_enabled and not phase1_enabled:
        if not tiers or not isinstance(tiers, list) or len(tiers) == 0:
            errors.append("phase2 only mode requires a non-empty tiers array")

    if isinstance(tiers, list) and len(tiers) > 0 and pt is not None:
        p = _safe_int(pt, -1)
        if p >= len(tiers):
            errors.append(f"phase2TriggerTier ({p}) must be less than number of tiers ({len(tiers)})")

    return errors


def load_config_source(source: str) -> tuple[dict | None, str | None]:
    """Parse --configuration value: inline JSON or @path. Returns (config_dict, error)."""
    if source.startswith("@"):
        path = source[1:].strip()
        if not path:
            return None, "empty @ path"
        path = os.path.abspath(path)
        if not os.path.isfile(path):
            return None, f"file not found: {path}"
        try:
            with open(path) as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return None, "configuration must be a JSON object"
            return data, None
        except (OSError, json.JSONDecodeError) as e:
            return None, str(e)
    try:
        data = json.loads(source)
        if not isinstance(data, dict):
            return None, "configuration must be a JSON object"
        return data, None
    except json.JSONDecodeError as e:
        return None, str(e)


def _infer_lock_mode_from_tiers(tiers: list) -> str:
    """Infer lockMode from tier contents (mirrors build_position_state). lockHwPct -> pct_of_high_water, else fixed_roe."""
    if not isinstance(tiers, list):
        return DEFAULT_LOCK_MODE
    return (
        "pct_of_high_water"
        if any(isinstance(t, dict) and "lockHwPct" in t for t in tiers)
        else "fixed_roe"
    )


def _ensure_phase_defaults(base: dict) -> None:
    """Apply defaults when config is missing; use high-water mode as default (no profile supplied)."""
    if "phase1" not in base:
        base["phase1"] = {"enabled": True, "retraceThreshold": 0.03, "consecutiveBreachesRequired": 3}
    elif isinstance(base.get("phase1"), dict) and "enabled" not in base["phase1"]:
        base["phase1"]["enabled"] = True
    if "phase2TriggerTier" not in base:
        base["phase2TriggerTier"] = 0
    # When lockMode is missing: infer from existing tiers if present (preserve legacy/custom tiers); else apply high-water defaults.
    if "lockMode" not in base:
        raw_tiers = base.get("tiers") or (
            base.get("phase2", {}).get("tiers") if isinstance(base.get("phase2"), dict) else None
        )
        existing_tiers = list(raw_tiers) if isinstance(raw_tiers, list) and len(raw_tiers) > 0 else []
        if existing_tiers:
            base["lockMode"] = _infer_lock_mode_from_tiers(existing_tiers)
            if "phase2TriggerRoe" not in base:
                base["phase2TriggerRoe"] = DEFAULT_PHASE2_TRIGGER_ROE
            # Preserve existing tiers: set base and phase2 from canonical list (do not overwrite with DEFAULT_TIERS_HIGH_WATER).
            base["tiers"] = list(existing_tiers)
            if isinstance(base.get("phase2"), dict):
                base["phase2"]["tiers"] = list(existing_tiers)
            else:
                base.setdefault("phase2", {})["tiers"] = list(existing_tiers)
        else:
            base["lockMode"] = DEFAULT_LOCK_MODE
            base["phase2TriggerRoe"] = DEFAULT_PHASE2_TRIGGER_ROE
            default_tiers = _default_tiers_for_lock_mode(DEFAULT_LOCK_MODE)
            base["tiers"] = default_tiers
            if isinstance(base.get("phase2"), dict):
                base["phase2"]["tiers"] = default_tiers
            else:
                base.setdefault("phase2", {})["tiers"] = default_tiers
    elif "phase2TriggerRoe" not in base:
        base["phase2TriggerRoe"] = DEFAULT_PHASE2_TRIGGER_ROE
    lock_mode = base.get("lockMode", DEFAULT_LOCK_MODE)
    default_tiers = _default_tiers_for_lock_mode(lock_mode)
    if "phase2" not in base:
        base["phase2"] = {"enabled": True, "retraceThreshold": 0.015, "consecutiveBreachesRequired": 1, "tiers": default_tiers}
    elif isinstance(base.get("phase2"), dict):
        if "enabled" not in base["phase2"]:
            base["phase2"]["enabled"] = True
        if "tiers" not in base["phase2"] and "tiers" not in base:
            base["phase2"]["tiers"] = default_tiers
    if "tiers" not in base and isinstance(base.get("phase2"), dict):
        base["tiers"] = base["phase2"].get("tiers", default_tiers)
    elif "tiers" not in base:
        base["tiers"] = default_tiers


def _merge_inline_config(base: dict, inline_config: dict | None) -> None:
    if not inline_config:
        return
    inline_has_tiers = False
    for k, v in inline_config.items():
        if k == "tiers":
            base["tiers"] = list(v) if isinstance(v, list) else base.get("tiers", [])
            inline_has_tiers = True
        elif isinstance(v, dict) and isinstance(base.get(k), dict):
            base[k] = {**base[k], **v}
        else:
            base[k] = v
    if (
        not inline_has_tiers
        and isinstance(base.get("phase2"), dict)
        and "tiers" in base["phase2"]
    ):
        base["tiers"] = list(base["phase2"]["tiers"])


def resolve_config(strategy_default: dict | None, inline_config: dict | None) -> dict:
    """Merge: inline (highest) > strategy default > hardcoded defaults. Tiers replaced atomically."""
    base = dict(strategy_default) if strategy_default else {}
    _ensure_phase_defaults(base)
    _merge_inline_config(base, inline_config)
    return base


def calc_absolute_floor(entry_price: float, leverage: float, retrace_roe: float, direction: str) -> float:
    """LONG: entry * (1 - retrace/leverage); SHORT: entry * (1 + retrace/leverage)."""
    lev = max(1, leverage)
    if (direction or "LONG").upper() == "LONG":
        return round(entry_price * (1 - retrace_roe / lev), 4)
    return round(entry_price * (1 + retrace_roe / lev), 4)


def _safe_int(value: Any, default: int = 0) -> int:
    """Convert to int without raising; return default on failure."""
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def config_to_phase1_phase2_tiers(config: dict, entry_price: float, leverage: float, direction: str) -> tuple[dict, int, dict, list]:
    """Extract phase1, phase2TriggerTier, phase2, tiers. At least one phase must be enabled. Tiers only in Phase 2."""
    phase1 = dict(config.get("phase1", {})) if isinstance(config.get("phase1"), dict) else {}
    phase2 = dict(config.get("phase2", {})) if isinstance(config.get("phase2"), dict) else {}
    phase1_enabled = phase1.get("enabled", True)
    phase2_enabled = phase2.get("enabled", True)
    if not phase1_enabled and not phase2_enabled:
        raise ValueError("at least one of phase1.enabled or phase2.enabled must be true")
    default_tiers = _default_tiers_for_lock_mode(config.get("lockMode"))
    raw_tiers = config.get("tiers") or (phase2.get("tiers") if isinstance(phase2.get("tiers"), list) else None) or default_tiers
    tiers = list(raw_tiers) if isinstance(raw_tiers, list) else default_tiers
    if phase2_enabled and not phase1_enabled and not tiers:
        raise ValueError("phase2 only requires non-empty tiers")
    if "absoluteFloor" not in phase1 or phase1["absoluteFloor"] is None:
        phase1["absoluteFloor"] = calc_absolute_floor(
            entry_price, leverage,
            _safe_float(phase1.get("retraceThreshold"), 0.03),
            direction,
        )
    phase1["enabled"] = phase1_enabled
    phase2["enabled"] = phase2_enabled
    phase2_trigger = _safe_int(config.get("phase2TriggerTier"), 0)
    return phase1, phase2_trigger, phase2, tiers


# ---------------------------------------------------------------------------
# strategy config (strategy-<id>.json) read/write
# ---------------------------------------------------------------------------


def load_strategy_json(state_dir: str, strategy_id: str) -> tuple[dict | None, str | None]:
    path = strategy_json_path(state_dir, strategy_id)
    if not os.path.isfile(path):
        return None, None
    try:
        with open(path) as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return None, "strategy config is not a JSON object"
        return data, None
    except (OSError, json.JSONDecodeError) as e:
        return None, str(e)


def _default_strategy_config() -> dict:
    """Default strategy config when no dsl-profile is supplied: high water mode."""
    return {
        "phase1": {"enabled": True, "retraceThreshold": 0.03, "consecutiveBreachesRequired": 3},
        "phase2TriggerTier": 0,
        "phase2TriggerRoe": DEFAULT_PHASE2_TRIGGER_ROE,
        "lockMode": DEFAULT_LOCK_MODE,
        "phase2": {"enabled": True, "retraceThreshold": 0.015, "consecutiveBreachesRequired": 1, "tiers": list(DEFAULT_TIERS_HIGH_WATER)},
        "tiers": list(DEFAULT_TIERS_HIGH_WATER),
        "cronIntervalMinutes": 3,
    }


def _wallet_from_position_file(path: str) -> str:
    state, _ = read_position_state(path)
    return ((state or {}).get("wallet") or "").strip()


def _new_strategy_data(strategy_id: str, wallet: str, now_iso: str) -> dict:
    return {
        "strategyId": strategy_id,
        "wallet": wallet,
        "status": "active",
        "createdAt": now_iso,
        "updatedAt": now_iso,
        "createdBySkill": "dsl-cli",
        "cronJobId": "",
        "defaultConfig": _default_strategy_config(),
        "positions": {},
    }


def load_or_create_strategy_json(state_dir: str, strategy_id: str) -> tuple[dict | None, str | None]:
    """Load strategy config; if missing but strategy has position files (legacy), create with defaults."""
    data, load_err = load_strategy_json(state_dir, strategy_id)
    if load_err or data is not None:
        return data, load_err
    positions_list = list_position_state_files(state_dir, strategy_id)
    if not positions_list:
        return None, None
    now = _now_iso()
    wallet = _wallet_from_position_file(positions_list[0][0])
    data = _new_strategy_data(strategy_id, wallet, now)
    reconcile_strategy_positions_from_disk(state_dir, strategy_id, data)
    err = save_strategy_json(state_dir, strategy_id, data)
    return (None, err) if err else (data, None)


def require_strategy_data(state_dir: str, strategy_id: str, not_found_msg: str = "strategy config not found") -> dict:
    """Load or create strategy config; exit with error if missing or load failed."""
    data, err = load_or_create_strategy_json(state_dir, strategy_id)
    if err:
        _exit_error(err)
    if not data:
        _exit_error(not_found_msg)
    return data


def reconcile_strategy_positions_from_disk(state_dir: str, strategy_id: str, data: dict) -> None:
    """Set data['positions'] to match current position state files on disk. Removes stale
    entries (e.g. after dsl-v5 archived a file). Preserves existing dex/addedAt when present."""
    now_iso = _now_iso()
    existing_positions = data.get("positions") or {}
    data["positions"] = {}
    for _path, asset in list_position_state_files(state_dir, strategy_id):
        data["positions"][asset] = existing_positions.get(
            asset,
            {"dex": "xyz" if asset.startswith("xyz:") else "main", "addedAt": now_iso},
        )


def save_strategy_json(state_dir: str, strategy_id: str, data: dict) -> str | None:
    path = strategy_json_path(state_dir, strategy_id)
    sd = os.path.dirname(path)
    try:
        os.makedirs(sd, exist_ok=True)
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
        return None
    except OSError as e:
        return str(e)


# ---------------------------------------------------------------------------
# Position state file build (for add-dsl)
# ---------------------------------------------------------------------------


def build_position_state(
    asset: str,
    dex: str,
    wallet: str,
    strategy_id: str,
    entry_price: float,
    size: float,
    leverage: float,
    direction: str,
    config: dict,
    now_iso: str,
) -> dict:
    """Build full position state dict for dsl-v5 (matches state-schema). At most two phases; initial phase = 1 if phase1.enabled else 2."""
    phase1, phase2_trigger, phase2, tiers = config_to_phase1_phase2_tiers(
        config, entry_price, leverage, direction,
    )
    # Default lockMode: high water when no profile supplied; infer from tiers when config has tiers but no lockMode (e.g. legacy lockPct).
    if "lockMode" in config:
        lock_mode = config["lockMode"]
    else:
        lock_mode = (
            "pct_of_high_water"
            if any(isinstance(t, dict) and "lockHwPct" in t for t in tiers)
            else "fixed_roe"
        )
    abs_floor = phase1["absoluteFloor"]
    initial_phase = 1 if phase1.get("enabled", True) else 2
    state = {
        "active": True,
        "asset": asset,
        "direction": direction,
        "leverage": leverage,
        "entryPrice": entry_price,
        "size": size,
        "wallet": wallet,
        "strategyId": strategy_id,
        "phase": initial_phase,
        "phase1": phase1,
        "phase2TriggerTier": phase2_trigger,
        "phase2": phase2,
        "tiers": tiers,
        "currentTierIndex": -1,
        "tierFloorPrice": None,
        "highWaterPrice": entry_price,
        "highWaterRoe": 0,
        "floorPrice": abs_floor,
        "currentBreachCount": 0,
        "createdAt": now_iso,
        "cronIntervalMinutes": _safe_int(config.get("cronIntervalMinutes"), 3) or 3,
        "lockMode": lock_mode,
    }
    if config.get("phase2TriggerRoe") is not None:
        state["phase2TriggerRoe"] = _safe_float(config["phase2TriggerRoe"], 0.0)
    return state


def write_position_state(path: str, state: dict) -> str | None:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(state, f, indent=2)
        return None
    except OSError as e:
        return str(e)


def read_position_state(path: str) -> tuple[dict | None, str | None]:
    """Return (state dict, error). Error is None on success."""
    try:
        with open(path) as f:
            data = json.load(f)
        return (data, None) if isinstance(data, dict) else (None, "invalid state")
    except (OSError, json.JSONDecodeError) as e:
        return None, str(e)


# ---------------------------------------------------------------------------
# Update-dsl: patch config into state dict
# ---------------------------------------------------------------------------


def _deep_merge_dict(dest: dict, src: dict) -> None:
    """Merge src into dest in place. For values that are dicts in both, merge recursively; otherwise replace."""
    for k, v in src.items():
        if k in dest and isinstance(dest[k], dict) and isinstance(v, dict):
            _deep_merge_dict(dest[k], v)
        else:
            dest[k] = v


def patch_config_into_state(state: dict, cfg: dict) -> list[str]:
    """Apply cfg (phase1, phase2TriggerTier, phase2, tiers) into state. Returns list of updated keys."""
    updated = []
    if "phase1" in cfg and isinstance(cfg["phase1"], dict):
        state.setdefault("phase1", {})
        if not isinstance(state["phase1"], dict):
            state["phase1"] = {}
        for k, v in cfg["phase1"].items():
            state["phase1"][k] = v
        if "absoluteFloor" not in cfg["phase1"]:
            entry_px, lev = state.get("entryPrice"), _safe_float(state.get("leverage"), 1.0)
            if entry_px is not None and lev >= 1:
                state["phase1"]["absoluteFloor"] = calc_absolute_floor(
                    _safe_float(entry_px, 0.0), lev,
                    _safe_float(state["phase1"].get("retraceThreshold"), 0.03),
                    state.get("direction") or "LONG",
                )
        updated.append("phase1")
    if "phase2TriggerTier" in cfg:
        state["phase2TriggerTier"] = _safe_int(cfg["phase2TriggerTier"], 0)
        updated.append("phase2TriggerTier")
    if "phase2" in cfg and isinstance(cfg["phase2"], dict):
        state.setdefault("phase2", {})
        if not isinstance(state["phase2"], dict):
            state["phase2"] = {}
        for k, v in cfg["phase2"].items():
            state["phase2"][k] = v
        updated.append("phase2")
    if "tiers" in cfg and isinstance(cfg["tiers"], list):
        state["tiers"] = list(cfg["tiers"])
        updated.append("tiers")
    if "cronIntervalMinutes" in cfg and cfg["cronIntervalMinutes"] is not None:
        state["cronIntervalMinutes"] = _safe_int(cfg["cronIntervalMinutes"], 3)
        updated.append("cronIntervalMinutes")
    if "lockMode" in cfg:
        state["lockMode"] = cfg["lockMode"]
        updated.append("lockMode")
    if "phase2TriggerRoe" in cfg:
        state["phase2TriggerRoe"] = _safe_float(cfg["phase2TriggerRoe"], 0.0)
        updated.append("phase2TriggerRoe")
    return updated


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------


def _add_dsl_resolve_wallet(strategy_id: str, wallet_arg: str | None, existing: dict | None) -> str:
    wallet = wallet_arg or (existing.get("wallet") if existing else None)
    if wallet:
        return wallet
    strategy, err = mcp_strategy_get(strategy_id)
    if err:
        _exit_error(f"wallet required; strategy_get failed: {err}")
    wallet = (strategy.get("strategyWalletAddress") or "").strip()
    if not wallet:
        _exit_error("strategy has no strategyWalletAddress")
    return wallet


def cmd_add_dsl(state_dir: str, args: argparse.Namespace) -> None:
    strategy_id = (args.strategy_id or "").strip()
    asset = (args.asset or "").strip() if getattr(args, "asset", None) else None
    dex = (args.dex or "").strip().lower() if getattr(args, "dex", None) else None
    errs = validate_cli_args(strategy_id=strategy_id, asset=asset, dex=dex, require_asset_dex_together=True)
    if errs:
        _exit_error("; ".join(errs))
    if asset is not None and dex is not None:
        asset, dex = normalize_asset_dex(asset, dex)
    skill = (args.skill or "").strip() if getattr(args, "skill", None) else ""
    wallet_arg = (args.wallet or "").strip() if getattr(args, "wallet", None) else None
    entry_override = getattr(args, "entry_price", None)
    entry_override = float(entry_override) if entry_override is not None else None
    inline_config = None
    if getattr(args, "configuration", None):
        cfg, err = load_config_source(args.configuration)
        if err:
            _exit_error(err)
        inline_config = cfg
        val_errors = validate_dsl_config(inline_config)
        if val_errors:
            _exit_error("configuration invalid: " + "; ".join(val_errors))
    existing, load_err = load_strategy_json(state_dir, strategy_id)
    if load_err:
        _exit_error(load_err)
    wallet = _add_dsl_resolve_wallet(strategy_id, wallet_arg, existing)
    positions_list, ch_err = get_positions_from_clearinghouse(wallet)
    if ch_err:
        _exit_error(ch_err)
    if asset:
        positions_list = [p for p in positions_list if p["coin"] == asset and p["dex"] == dex]
        if not positions_list:
            _exit_error(f"position not found in clearinghouse: {asset} ({dex})")
    else:
        existing_paths = {filename_to_asset(os.path.basename(p)) for p, _ in list_position_state_files(state_dir, strategy_id)}
        existing_paths.discard(None)
        positions_list = [p for p in positions_list if p["coin"] not in existing_paths]
    now_iso = _now_iso()
    config = resolve_config(existing.get("defaultConfig") if existing else None, inline_config)
    action = "updated" if existing else "created"
    cron_job_id = existing.get("cronJobId") if existing else None
    if not existing:
        strategy_data = _new_strategy_data(strategy_id, wallet, now_iso)
        strategy_data["createdBySkill"] = skill or "dsl-cli"
        strategy_data["cronJobId"] = cron_job_id or ""
        strategy_data["defaultConfig"] = config
        err = save_strategy_json(state_dir, strategy_id, strategy_data)
        if err:
            _exit_error(err)
        cron_created = not cron_job_id
    else:
        existing["updatedAt"] = now_iso
        if inline_config:
            existing["defaultConfig"] = config
        reconcile_strategy_positions_from_disk(state_dir, strategy_id, existing)
        err = save_strategy_json(state_dir, strategy_id, existing)
        if err:
            _exit_error(err)
        cron_created = not existing.get("cronJobId")
        strategy_data = existing
    positions_added = []
    for p in positions_list:
        entry_price = entry_override if entry_override is not None else p["entryPx"] or 0.0
        state = build_position_state(
            asset=p["coin"], dex=p["dex"], wallet=wallet, strategy_id=strategy_id,
            entry_price=entry_price, size=p["size"], leverage=p["leverage"] or 10.0,
            direction=p["direction"], config=config, now_iso=now_iso,
        )
        path = position_state_path(state_dir, strategy_id, p["coin"])
        err = write_position_state(path, state)
        if err:
            _exit_error(err)
        positions_added.append(p["coin"])
        strategy_data["positions"][p["coin"]] = {"dex": p["dex"], "addedAt": now_iso}
    if positions_added:
        strategy_data["updatedAt"] = now_iso
        reconcile_strategy_positions_from_disk(state_dir, strategy_id, strategy_data)
        err = save_strategy_json(state_dir, strategy_id, strategy_data)
        if err:
            _exit_error(f"failed to save strategy config after adding positions: {err}")
    out = {"status": "ok", "strategy_id": strategy_id, "action": action, "positions_added": positions_added}
    if cron_created:
        cron_job_id = "dsl-" + uuid.uuid4().hex[:12]
        strategy_data["cronJobId"] = cron_job_id
        cron_interval = _safe_int(config.get("cronIntervalMinutes"), 3) or 3
        strategy_data["cronScheduleMinutes"] = cron_interval
        strategy_data["updatedAt"] = _now_iso()
        reconcile_strategy_positions_from_disk(state_dir, strategy_id, strategy_data)
        err = save_strategy_json(state_dir, strategy_id, strategy_data)
        if err:
            _exit_error(f"failed to save strategy config with cron id: {err}")
        out["cron_needed"] = True
        out["cron_job_id"] = cron_job_id
        out["cron_env"] = {"DSL_STATE_DIR": state_dir, "DSL_STRATEGY_ID": strategy_id}
        out["cron_schedule"] = cron_schedule_from_interval_minutes(cron_interval)
        out["cron_interval_minutes"] = cron_interval
    elif strategy_data.get("cronJobId"):
        out["cron_job_id"] = strategy_data["cronJobId"]
    print(json.dumps(out))


def cmd_update_dsl(state_dir: str, args: argparse.Namespace) -> None:
    strategy_id = (args.strategy_id or "").strip()
    if not strategy_id or not _safe_path_component(strategy_id):
        _exit_error("strategy_id required and must be path-safe")
    asset = (args.asset or "").strip() if getattr(args, "asset", None) else None
    if asset and (os.path.sep in asset or asset in (".", "..")):
        _exit_error("invalid asset")
    dex = (args.dex or "").strip().lower() if getattr(args, "dex", None) else None
    if not getattr(args, "configuration", None):
        _exit_error("--configuration required")
    cfg, err = load_config_source(args.configuration)
    if err:
        _exit_error(err)
    val_errors = validate_dsl_config(cfg)
    if val_errors:
        _exit_error("configuration invalid: " + "; ".join(val_errors))
    strategy_data = require_strategy_data(state_dir, strategy_id)
    strategy_data["updatedAt"] = _now_iso()
    if asset and dex:
        asset, dex = normalize_asset_dex(asset, dex)
        path = position_state_path(state_dir, strategy_id, asset)
        if not os.path.isfile(path):
            _exit_error(f"position state file not found: {asset}")
        state, err = read_position_state(path)
        if err or not state:
            _exit_error(f"position state file invalid: {asset}" if not err else err)
        fields_updated = patch_config_into_state(state, cfg)
        err = write_position_state(path, state)
        if err:
            _exit_error(f"failed to write position state: {err}")
        scope, out_asset, patch_failed = "position", asset, []
    else:
        old_cron_interval = strategy_data.get("cronScheduleMinutes", 3)
        existing = strategy_data.get("defaultConfig", {})
        merged = copy.deepcopy(existing) if existing else {}
        _deep_merge_dict(merged, cfg)
        strategy_data["defaultConfig"] = merged
        new_cron_interval = _safe_int(strategy_data["defaultConfig"].get("cronIntervalMinutes"), 3) or 3
        fields_updated = list(cfg.keys())
        patch_failed = []
        for path, a in list_position_state_files(state_dir, strategy_id):
            state, err = read_position_state(path)
            if err or not state:
                patch_failed.append(a)
                continue
            patch_config_into_state(state, cfg)
            if write_position_state(path, state):
                patch_failed.append(a)
        scope, out_asset = "strategy", None

        # If cron interval changed, agent must remove old cron and create new one with new schedule.
        cron_job_id = strategy_data.get("cronJobId")
        if cron_job_id and new_cron_interval != old_cron_interval:
            strategy_data["cronScheduleMinutes"] = new_cron_interval
            # Save strategy with updated cronScheduleMinutes before outputting
            reconcile_strategy_positions_from_disk(state_dir, strategy_id, strategy_data)
            err = save_strategy_json(state_dir, strategy_id, strategy_data)
            if err:
                _exit_error(f"failed to save strategy config: {err}")
            out = {
                "status": "ok",
                "strategy_id": strategy_id,
                "scope": scope,
                "fields_updated": fields_updated,
                "cron_schedule_changed": True,
                "cron_to_remove": {"cron_job_id": cron_job_id},
                "cron_needed": True,
                "cron_job_id": cron_job_id,
                "cron_env": {"DSL_STATE_DIR": state_dir, "DSL_STRATEGY_ID": strategy_id},
                "cron_schedule": cron_schedule_from_interval_minutes(new_cron_interval),
                "cron_interval_minutes": new_cron_interval,
            }
            if patch_failed:
                out["patch_failed"] = patch_failed
            print(json.dumps(out))
            return

    reconcile_strategy_positions_from_disk(state_dir, strategy_id, strategy_data)
    err = save_strategy_json(state_dir, strategy_id, strategy_data)
    if err:
        _exit_error(f"failed to save strategy config: {err}")
    out = {"status": "ok", "strategy_id": strategy_id, "scope": scope, "fields_updated": fields_updated}
    if out_asset:
        out["asset"] = out_asset
    if patch_failed:
        out["patch_failed"] = patch_failed
    print(json.dumps(out))


def _set_position_active(path: str, active: bool, paused_at: str | None = None) -> str | None:
    """Set active and optionally pausedAt; write back. Returns error or None."""
    state, err = read_position_state(path)
    if err or not state:
        return err or "invalid state"
    state["active"] = active
    if paused_at is not None:
        state["pausedAt"] = paused_at
    else:
        state.pop("pausedAt", None)
    return write_position_state(path, state)


def cmd_pause_dsl(state_dir: str, args: argparse.Namespace) -> None:
    strategy_id = (args.strategy_id or "").strip()
    if not strategy_id or not _safe_path_component(strategy_id):
        _exit_error("strategy_id required and must be path-safe")
    asset = (args.asset or "").strip() if getattr(args, "asset", None) else None
    if asset and (os.path.sep in asset or asset in (".", "..")):
        _exit_error("invalid asset")
    dex = (args.dex or "").strip().lower() if getattr(args, "dex", None) else None
    strategy_data = require_strategy_data(state_dir, strategy_id)
    now_iso = _now_iso()
    if asset and dex:
        asset, dex = normalize_asset_dex(asset, dex)
        path = position_state_path(state_dir, strategy_id, asset)
        if not os.path.isfile(path):
            _exit_error(f"position state file not found: {asset}")
        err = _set_position_active(path, False, now_iso)
        if err:
            _exit_error(f"failed to read or write position state: {err}")
        paused_list, scope = [asset], "position"
    else:
        strategy_data["status"] = "paused"
        strategy_data["updatedAt"] = now_iso
        paused_list = []
        for path, a in list_position_state_files(state_dir, strategy_id):
            state, _ = read_position_state(path)
            if state and state.get("active", True) and _set_position_active(path, False, now_iso) is None:
                paused_list.append(a)
        reconcile_strategy_positions_from_disk(state_dir, strategy_id, strategy_data)
        err = save_strategy_json(state_dir, strategy_id, strategy_data)
        if err:
            _exit_error(f"failed to save strategy config: {err}")
        scope = "strategy"
    print(json.dumps({"status": "ok", "strategy_id": strategy_id, "scope": scope, "paused": paused_list}))


def cmd_resume_dsl(state_dir: str, args: argparse.Namespace) -> None:
    strategy_id = (args.strategy_id or "").strip()
    if not strategy_id or not _safe_path_component(strategy_id):
        _exit_error("strategy_id required and must be path-safe")
    asset = (args.asset or "").strip() if getattr(args, "asset", None) else None
    if asset and (os.path.sep in asset or asset in (".", "..")):
        _exit_error("invalid asset")
    dex = (args.dex or "").strip().lower() if getattr(args, "dex", None) else None
    strategy_data = require_strategy_data(state_dir, strategy_id)
    if asset and dex:
        asset, dex = normalize_asset_dex(asset, dex)
        path = position_state_path(state_dir, strategy_id, asset)
        if not os.path.isfile(path):
            _exit_error(f"position state file not found: {asset}")
        err = _set_position_active(path, True, None)
        if err:
            _exit_error(f"failed to read or write position state: {err}")
        resumed_list, scope = [asset], "position"
    else:
        strategy_data["status"] = "active"
        strategy_data["updatedAt"] = _now_iso()
        resumed_list = []
        for path, a in list_position_state_files(state_dir, strategy_id):
            state, _ = read_position_state(path)
            if state and "pausedAt" in state and _set_position_active(path, True, None) is None:
                resumed_list.append(a)
        reconcile_strategy_positions_from_disk(state_dir, strategy_id, strategy_data)
        err = save_strategy_json(state_dir, strategy_id, strategy_data)
        if err:
            _exit_error(f"failed to save strategy config: {err}")
        scope = "strategy"
    print(json.dumps({"status": "ok", "strategy_id": strategy_id, "scope": scope, "resumed": resumed_list}))


def _archive_position_file(path: str, dest_path: str) -> tuple[bool, bool]:
    """Archive position file by moving to dest_path (never delete). Returns (archived_ok, moved_ok).
    If read fails (transient I/O, bad JSON), we still move the file to preserve original content
    in the archive instead of overwriting with minimal state."""
    state, _ = read_position_state(path)
    try:
        os.rename(path, dest_path)
    except OSError:
        return False, False
    if not isinstance(state, dict):
        # Read failed: do not overwrite archived file; leave original content intact.
        return True, True
    state["active"] = False
    try:
        with open(dest_path, "w") as f:
            json.dump(state, f, indent=2)
    except OSError:
        pass  # moved ok; write-back of active=False is best-effort
    return True, True


def _run_dsl_cleanup(state_dir: str, strategy_id: str) -> bool:
    scripts_dir = os.path.dirname(os.path.abspath(__file__))
    script = os.path.join(scripts_dir, "dsl-cleanup.py")
    if not os.path.isfile(script):
        return False
    env = os.environ.copy()
    env["DSL_STATE_DIR"] = state_dir
    env["DSL_STRATEGY_ID"] = strategy_id
    try:
        r = subprocess.run(
            [sys.executable, script],
            env=env, cwd=scripts_dir, capture_output=True, text=True, timeout=15,
        )
        return r.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


def cmd_delete_dsl(state_dir: str, args: argparse.Namespace) -> None:
    strategy_id = (args.strategy_id or "").strip()
    if not strategy_id or not _safe_path_component(strategy_id):
        _exit_error("strategy_id required and must be path-safe")
    asset = (args.asset or "").strip() if getattr(args, "asset", None) else None
    if asset and (os.path.sep in asset or asset in (".", "..")):
        _exit_error("invalid asset")
    dex = (args.dex or "").strip().lower() if getattr(args, "dex", None) else None
    strategy_data = require_strategy_data(state_dir, strategy_id)
    epoch = int(datetime.now(timezone.utc).timestamp())
    sd = strategy_dir(state_dir, strategy_id)
    archived = []
    failed_to_remove = []
    cron_to_remove = None
    cleanup_run = False
    if asset and dex:
        asset, dex = normalize_asset_dex(asset, dex)
        path = position_state_path(state_dir, strategy_id, asset)
        if os.path.isfile(path):
            dest = os.path.join(sd, f"{asset_to_filename(asset)}_archived_deleted_{epoch}.json")
            _, removed = _archive_position_file(path, dest)
            (archived if removed else failed_to_remove).append(asset)
        else:
            archived.append(asset)
        strategy_data["updatedAt"] = _now_iso()
        reconcile_strategy_positions_from_disk(state_dir, strategy_id, strategy_data)
        save_strategy_json(state_dir, strategy_id, strategy_data)
        if not list_position_state_files(state_dir, strategy_id):
            cron_to_remove = strategy_data.get("cronJobId") or ""
            strategy_data["cronJobId"] = ""
            strategy_data["status"] = "completed"
            save_strategy_json(state_dir, strategy_id, strategy_data)
            cleanup_run = _run_dsl_cleanup(state_dir, strategy_id)
    else:
        for path, a in list_position_state_files(state_dir, strategy_id):
            dest = os.path.join(sd, f"{asset_to_filename(a)}_archived_deleted_{epoch}.json")
            _, removed = _archive_position_file(path, dest)
            (archived if removed else failed_to_remove).append(a)
        strat_path = strategy_json_path(state_dir, strategy_id)
        dest_strat = os.path.join(sd, f"strategy_archived_{epoch}.json")
        if os.path.isfile(strat_path):
            try:
                os.rename(strat_path, dest_strat)
            except OSError:
                pass
        cron_to_remove = strategy_data.get("cronJobId") or ""
        cleanup_run = _run_dsl_cleanup(state_dir, strategy_id)
    out = {"status": "ok", "strategy_id": strategy_id, "scope": "position" if asset else "strategy", "archived": archived}
    if failed_to_remove:
        out["failed_to_remove"] = failed_to_remove
    if cron_to_remove:
        out["cron_to_remove"] = {"cron_job_id": cron_to_remove}
    out["cron_removed"] = bool(cron_to_remove)
    out["cleanup_run"] = cleanup_run
    print(json.dumps(out))


def _position_status_summary(state: dict, asset: str) -> dict:
    return {
        "dex": "xyz" if asset.startswith("xyz:") else "main",
        "status": "active" if state.get("active") else "paused",
        "phase": state.get("phase", 1),
        "current_tier": state.get("currentTierIndex", -1),
        "high_water_price": state.get("highWaterPrice"),
        "floor_price": state.get("floorPrice"),
        "last_check": state.get("lastCheck"),
    }


def cmd_status_dsl(state_dir: str, args: argparse.Namespace) -> None:
    strategy_id = (args.strategy_id or "").strip()
    if not strategy_id or not _safe_path_component(strategy_id):
        _exit_error("strategy_id required and must be path-safe")
    asset = (args.asset or "").strip() if getattr(args, "asset", None) else None
    if asset and (os.path.sep in asset or asset in (".", "..")):
        _exit_error("invalid asset")
    dex = (args.dex or "").strip().lower() if getattr(args, "dex", None) else None
    strategy_data = require_strategy_data(state_dir, strategy_id)
    if asset and dex:
        asset, dex = normalize_asset_dex(asset, dex)
        path = position_state_path(state_dir, strategy_id, asset)
        if not os.path.isfile(path):
            _exit_error(f"position state file not found: {asset}")
        state, err = read_position_state(path)
        if err or not state:
            _exit_error(f"position state file invalid: {asset}" if not err else err)
        out = {
            "strategy_id": strategy_id,
            "asset": state.get("asset"),
            "dex": dex,
            "status": "active" if state.get("active") else "paused",
            "phase": state.get("phase", 1),
            "direction": state.get("direction"),
            "leverage": state.get("leverage"),
            "entry_price": state.get("entryPrice"),
            "high_water_price": state.get("highWaterPrice"),
            "floor_price": state.get("floorPrice"),
            "current_tier_index": state.get("currentTierIndex", -1),
            "current_breach_count": state.get("currentBreachCount", 0),
            "last_check": state.get("lastCheck"),
            "last_price": state.get("lastPrice"),
        }
        print(json.dumps(out))
    else:
        positions = {}
        for path, a in list_position_state_files(state_dir, strategy_id):
            state, err = read_position_state(path)
            positions[a] = _position_status_summary(state, a) if state else {"status": "error", "error": err or "read failed"}
        print(json.dumps({
            "strategy_id": strategy_id,
            "status": strategy_data.get("status", "active"),
            "created_by_skill": strategy_data.get("createdBySkill"),
            "cron_job_id": strategy_data.get("cronJobId"),
            "positions": positions,
        }))


def _count_positions_by_state(state_dir: str, strategy_id: str) -> tuple[list[str], list[str], list[str]]:
    """Return (active_list, paused_list, completed_list)."""
    sd = strategy_dir(state_dir, strategy_id)
    active_list, paused_list, completed_list = [], [], []
    if not os.path.isdir(sd):
        return active_list, paused_list, completed_list
    try:
        names = os.listdir(sd)
    except OSError:
        raise
    for name in names:
        if name.startswith("strategy-") or name.startswith("strategy_archived_") or not name.endswith(".json"):
            continue
        path = os.path.join(sd, name)
        if not os.path.isfile(path):
            continue
        if "_archived" in name or ".archived" in name:
            base = name[:-5].split("_archived_")[0]
            asset = filename_to_asset(base + ".json")
            if asset:
                completed_list.append(asset)
            continue
        asset = filename_to_asset(name)
        if not asset:
            continue
        state, _ = read_position_state(path)
        if state and not state.get("active", True):
            paused_list.append(asset)
        else:
            active_list.append(asset)
    return active_list, paused_list, completed_list


def cmd_count_dsl(state_dir: str, args: argparse.Namespace) -> None:
    strategy_id = (args.strategy_id or "").strip()
    if not strategy_id or not _safe_path_component(strategy_id):
        _exit_error("strategy_id required and must be path-safe")
    try:
        active_list, paused_list, completed_list = _count_positions_by_state(state_dir, strategy_id)
    except OSError as e:
        _exit_error(f"cannot list strategy directory: {e}")
    total = len(active_list) + len(paused_list) + len(completed_list)
    print(json.dumps({
        "strategy_id": strategy_id,
        "total": total,
        "active": len(active_list),
        "paused": len(paused_list),
        "completed": len(completed_list),
        "positions": {"active": active_list, "paused": paused_list, "completed": completed_list},
    }))


def cmd_validate(state_dir: str, args: argparse.Namespace) -> None:
    """Validate DSL configuration (e.g. dsl-profile.json or inline JSON)."""
    source = (getattr(args, "configuration", None) or "").strip()
    if not source:
        _exit_error("--configuration required (inline JSON or @path to file, e.g. @dsl-profile.json)")
    cfg, load_err = load_config_source(source)
    if load_err:
        _exit_error(load_err)
    if not cfg:
        _exit_error("configuration must be a JSON object")
    errors = validate_dsl_config(cfg)
    if errors:
        print(json.dumps({"valid": False, "errors": errors}))
        sys.exit(1)
    print(json.dumps({"valid": True, "errors": []}))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="DSL lifecycle CLI")
    parser.add_argument("--state-dir", default=os.environ.get("DSL_STATE_DIR", DSL_STATE_DIR), help="DSL state base directory")
    sub = parser.add_subparsers(dest="command", required=True)

    # add-dsl
    add = sub.add_parser("add-dsl", help="Set up DSL for a strategy")
    add.add_argument("strategy_id", nargs="?", help="Strategy UUID")
    add.add_argument("asset", nargs="?", help="Asset (e.g. ETH, xyz:SILVER); omit for all positions")
    add.add_argument("dex", nargs="?", choices=["main", "xyz"], help="main or xyz (required if asset set)")
    add.add_argument("--skill", help="Calling skill name (stored in strategy config)")
    add.add_argument("--wallet", help="Strategy wallet (skips MCP fetch if set)")
    add.add_argument("--entry-price", type=float, help="Override entry price from clearinghouse")
    add.add_argument("--configuration", help="JSON config or @path (e.g. @/path/to/dsl-profile.json)")
    add.set_defaults(func=cmd_add_dsl)

    # update-dsl
    upd = sub.add_parser("update-dsl", help="Update DSL configuration")
    upd.add_argument("strategy_id", help="Strategy UUID")
    upd.add_argument("asset", nargs="?", help="Asset; omit for strategy-wide")
    upd.add_argument("dex", nargs="?", choices=["main", "xyz"], help="main or xyz (required if asset set)")
    upd.add_argument("--configuration", required=True, help="JSON config or @path")
    upd.set_defaults(func=cmd_update_dsl)

    # pause-dsl
    pause = sub.add_parser("pause-dsl", help="Pause DSL monitoring")
    pause.add_argument("strategy_id", help="Strategy UUID")
    pause.add_argument("asset", nargs="?", help="Asset; omit for all")
    pause.add_argument("dex", nargs="?", choices=["main", "xyz"], help="main or xyz (required if asset set)")
    pause.set_defaults(func=cmd_pause_dsl)

    # resume-dsl
    resume = sub.add_parser("resume-dsl", help="Resume paused DSL")
    resume.add_argument("strategy_id", help="Strategy UUID")
    resume.add_argument("asset", nargs="?", help="Asset; omit for all")
    resume.add_argument("dex", nargs="?", choices=["main", "xyz"], help="main or xyz (required if asset set)")
    resume.set_defaults(func=cmd_resume_dsl)

    # delete-dsl
    delete = sub.add_parser("delete-dsl", help="Tear down DSL (archive state, output cron removal)")
    delete.add_argument("strategy_id", help="Strategy UUID")
    delete.add_argument("asset", nargs="?", help="Asset; omit for entire strategy")
    delete.add_argument("dex", nargs="?", choices=["main", "xyz"], help="main or xyz (required if asset set)")
    delete.set_defaults(func=cmd_delete_dsl)

    # status-dsl
    status = sub.add_parser("status-dsl", help="Report DSL status")
    status.add_argument("strategy_id", help="Strategy UUID")
    status.add_argument("asset", nargs="?", help="Asset; omit for strategy summary")
    status.add_argument("dex", nargs="?", choices=["main", "xyz"], help="main or xyz (required if asset set)")
    status.set_defaults(func=cmd_status_dsl)

    # count-dsl
    count = sub.add_parser("count-dsl", help="Count positions by state")
    count.add_argument("strategy_id", help="Strategy UUID")
    count.set_defaults(func=cmd_count_dsl)

    # validate
    validate = sub.add_parser("validate", help="Validate DSL config (e.g. dsl-profile.json)")
    validate.add_argument("--configuration", required=True, help="JSON config or @path (e.g. @dsl-profile.json)")
    validate.set_defaults(func=cmd_validate)

    args = parser.parse_args()
    state_dir = (args.state_dir or DSL_STATE_DIR).rstrip("/")
    args.func(state_dir, args)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(json.dumps({"status": "error", "error": "interrupted"}))
        sys.exit(130)
    except Exception as e:
        print(json.dumps({"status": "error", "error": str(e)}))
        sys.exit(1)
