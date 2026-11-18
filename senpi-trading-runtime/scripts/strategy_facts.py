"""StrategyFacts v1 in Python: a line-for-line port of senpi-trading-runtime src/strategy/facts.ts.

What one runtime instance does, read from its own files, in the wire shape the runtime records on
every launch. One addition, `exit.lock_tiers` (every phase-2 tier as [trigger_pct, lock_hw_pct]),
which the strategy page's profit-lock chart draws; `lock_steps` stays its length.

The exit block follows the engine's own reader (map-exit-config.ts `buildPreset` -> parse-preset.ts
`parsePreset` -> parse-phase.ts / parse-timeouts.ts / parse-tiers.ts): max_loss_pct resolution,
phase 2 on unless `enabled: false`, time cuts only with `enabled: true` and clamped to the exit poll
interval. Where the engine would refuse the preset, exit is null. Not ported: the engine's
unknown-key check (a package with unknown exit keys does not deploy at all).

Pure: no I/O, never raises. Every field is None when the files do not say. When the runtime ships a
facts command, the generator should call it instead; strategies/tests pins this port to known values.
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import math
import re

FACTS_VERSION = 1

ASSET_KEYS = ["universe", "allowedAssets", "assets", "whitelist", "trackedAssets", "asset", "coin",
              "hedgeUniverse"]
LIST_ONLY_ASSET_KEYS = ["equities", "trackedCrypto", "riskAssets"]
DYNAMIC_UNIVERSE_KEYS = [
    "universeVolFloorUsd", "universeMaxNames", "universeTopN", "topN", "maxUniverse", "universeSize",
    "maxUniverseNames", "maxMainNames", "volFloorUsd", "leaderboardLimit", "shortlistByVol", "minOiUsd",
    "xyzVolFloorUsd", "trackedXyz",
]
TICKER = re.compile(r"^(xyz:)?[A-Za-z0-9]{2,10}$")
DEFAULT_EXIT_INTERVAL_S = 30


class _Refused(Exception):
    """The engine would refuse this preset."""


def _num(v):
    """A finite number (TS `num`): any sign, never a bool."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return v if math.isfinite(v) else None


def _is_map(v):
    return isinstance(v, dict)


def _tickers(v):
    if not isinstance(v, list) or not v:
        return None
    return v if all(isinstance(x, str) and TICKER.match(x) for x in v) else None


def _tier_numbers(v):
    if _num(v) is not None:
        return [v]
    if isinstance(v, list):
        return [n for x in v for n in _tier_numbers(x)]
    if _is_map(v):
        return [n for x in v.values() for n in _tier_numbers(x)]
    return []


def _first_num(inputs, keys):
    for k in keys:
        n = _num(inputs.get(k))
        if n is not None:
            return n
    return None


def _normalize_direction(v):
    if not isinstance(v, str):
        return None
    return {"long": "long", "long_only": "long", "short": "short", "short_only": "short",
            "long_short": "long_short"}.get(v.strip().lower())


def _universe_facts(inputs, catalog):
    for k in ASSET_KEYS:
        v = inputs.get(k)
        lst = _tickers([v] if isinstance(v, str) else v)
        if lst:
            return {"assets": lst, "universe": "static"}
    for k in LIST_ONLY_ASSET_KEYS:
        lst = _tickers(inputs.get(k))
        if lst:
            return {"assets": lst, "universe": "static"}
    dex = inputs["dex"].lower() if isinstance(inputs.get("dex"), str) else ""
    if dex == "xyz" or any(k in inputs for k in DYNAMIC_UNIVERSE_KEYS):
        return {"assets": None, "universe": "dynamic"}
    return {"assets": _tickers(catalog.get("assets")), "universe": None}


def _direction(inputs, catalog):
    return (_normalize_direction(inputs.get("direction")) or _normalize_direction(inputs.get("leg"))
            or _normalize_direction(catalog.get("direction")))


def _tier_leverages(v):
    rows = v if isinstance(v, list) else list(v.values()) if _is_map(v) else []
    out = []
    for row in rows:
        lev = _num(row[1]) if isinstance(row, list) and len(row) > 1 else (
            None if isinstance(row, list) else _num(row))
        if lev is not None:
            out.append(lev)
    return out


def _leverage_max(inputs):
    explicit = _first_num(inputs, ["maxLeverage", "leverageMax", "max_leverage"])
    if explicit is not None:
        return explicit
    tiers = _tier_leverages(inputs.get("leverageTiers"))
    if tiers:
        return max(tiers)
    return _num(inputs.get("leverage"))


def _margin_facts(strategy, inputs):
    margin = _num(strategy.get("margin_pct"))
    margin_max = None
    if margin is None:
        margin = _first_num(inputs, ["marginPct", "marginPctBase", "margin_pct", "marginPctMax"])
        tiers = _tier_numbers(inputs.get("marginPctTiers"))
        if margin is None and tiers:
            margin, margin_max = min(tiers), max(tiers)
    if margin_max is None:
        cap = _first_num(inputs, ["maxMarginPct", "marginPctMax", "marginPctCap"])
        if cap is not None and cap != margin:
            margin_max = cap
    return {"margin_pct": margin, "margin_pct_max": margin_max}


# ---- the engine's preset reader (only what the facts need) ----

def _pos(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0


def _derive_max_loss(preset):  # map-exit-config.ts deriveMaxLossPct
    if _pos(preset.get("max_loss_pct")):
        return preset["max_loss_pct"]
    p1 = preset.get("phase1")
    if _is_map(p1) and _pos(p1.get("max_loss_pct")):
        return p1["max_loss_pct"]
    return None


def _number(v):  # JS Number(): None/missing -> NaN
    if isinstance(v, bool):
        return float(v)
    if isinstance(v, (int, float)):
        return v
    if isinstance(v, str) and not v.strip():
        return 0  # JS Number("") is 0
    try:
        return float(v)
    except (TypeError, ValueError):
        return math.nan


def _read_phase1_max_loss(phase1, preset_max_loss):  # parse-phase.ts readPhase1
    if not _is_map(phase1):
        raise _Refused("phase1 is required")
    if phase1.get("enabled") is False:
        v = phase1.get("max_loss_pct")
        m = _number(v if v is not None else (preset_max_loss if preset_max_loss is not None else 1))
        if not (m > 0) or m > 100:
            raise _Refused("max_loss_pct")
        return m
    v = phase1.get("max_loss_pct")
    m = _number(v if v is not None else preset_max_loss)
    if not (m > 0) or m > 100:
        raise _Refused("max_loss_pct")
    rt = _number(_coalesce(phase1, "retrace_threshold", "retraceThreshold"))
    if not (rt > 0):
        raise _Refused("retrace_threshold")
    br = _number(_coalesce(phase1, "consecutive_breaches_required", "consecutiveBreachesRequired"))
    if not math.isfinite(br) or br < 1 or br != int(br):
        raise _Refused("consecutive_breaches_required")
    return m


def _coalesce(m, *keys):
    """JS `m.a ?? m.b`: the first key whose value is not null/missing."""
    for k in keys:
        if m.get(k) is not None:
            return m[k]
    return None


def _parse_tier(t):  # parse-tiers.ts parseTier
    if not _is_map(t) or t.get("breaches") is not None or t.get("retrace") is not None \
            or t.get("retraceFromHW") is not None:
        raise _Refused("tier")
    trig = _number(_coalesce(t, "trigger_pct", "triggerPct"))
    lock = _number(_coalesce(t, "lock_hw_pct", "lockHwPct"))
    if not math.isfinite(trig) or trig <= 0 or not math.isfinite(lock) or lock < 0 or lock > 100:
        raise _Refused("tier values")
    return [trig, lock]  # Number()-coerced, as the engine stores them


def _parse_cut(raw, min_interval, needs_min_value=False):  # parse-timeouts.ts
    if not _is_map(raw) or not raw.get("enabled"):
        return None
    iv = raw.get("interval_in_minutes")
    if not _pos(iv) or not math.isfinite(iv):
        raise _Refused("cut interval")
    after = max(iv, min_interval)
    if math.isnan(min_interval) or not after > 0:  # JS Math.max with NaN is NaN, then refused
        raise _Refused("cut interval after clamp")
    cut = {"after_min": after}
    if needs_min_value:
        mv = raw.get("min_value")
        if not _pos(mv) or not math.isfinite(mv):
            raise _Refused("weak_peak_cut min_value")
        cut["under_pct"] = mv
    return cut


def _engine_preset(exit_block):
    """(max_loss, phase2_enabled, tiers, hard_timeout, weak_cut, dead_cut) or None if refused."""
    try:
        if not _is_map(exit_block):
            return None
        raw = exit_block.get("dsl_preset")
        if not _is_map(raw):
            return None
        # buildPreset: resolve max_loss_pct the way the runtime does before the parser sees it
        max_loss = _derive_max_loss(raw)
        p1raw = raw.get("phase1")
        if not _pos(max_loss) and _is_map(p1raw) and p1raw.get("enabled") is False:
            max_loss = 1
        if not _pos(max_loss):
            return None
        if raw.get("phase2_trigger_tier") is not None:
            return None
        if _is_map(p1raw) and any(p1raw.get(k) is not None
                                  for k in ("hard_timeout", "weak_peak_cut", "dead_weight_cut")):
            return None
        interval_s = exit_block.get("interval_seconds")
        interval_s = interval_s if interval_s is not None else DEFAULT_EXIT_INTERVAL_S
        min_interval = _number(interval_s) / 60  # JS coerces `"60" / 60`

        phase2 = raw.get("phase2")
        p2_tiers = phase2.get("tiers") if _is_map(phase2) else None
        if isinstance(p2_tiers, list) and p2_tiers:
            tiers_raw, path = p2_tiers, "phase2"
        else:
            tiers_raw, path = raw.get("tiers"), "legacy"
            if not isinstance(tiers_raw, list) or not tiers_raw:
                return None
        if path == "legacy":
            tiers = [_parse_tier(t) for t in tiers_raw]
        p1_enabled = not (_is_map(p1raw) and p1raw.get("enabled") is False)
        loss_cap = _read_phase1_max_loss(p1raw, max_loss)
        if not _is_map(phase2):
            return None
        if any(phase2.get(k) is not None for k in ("retrace_threshold", "retraceThreshold",
               "retrace_from_hw", "consecutive_breaches_required", "consecutiveBreachesRequired")):
            return None
        p2_enabled = phase2.get("enabled") is not False
        if not p1_enabled and not p2_enabled:
            return None
        if path == "phase2":
            tiers = [_parse_tier(t) for t in tiers_raw]
        if any(tiers[i][0] <= tiers[i - 1][0] for i in range(1, len(tiers))):
            return None
        hard = _parse_cut(raw.get("hard_timeout"), min_interval)
        weak = _parse_cut(raw.get("weak_peak_cut"), min_interval, needs_min_value=True)
        dead = _parse_cut(raw.get("dead_weight_cut"), min_interval)
        return loss_cap, p2_enabled, tiers, hard, weak, dead
    except Exception:  # noqa: BLE001 — facts.ts enginePreset: any refusal or surprise -> null
        return None


def _exit_facts(exit_block):
    preset = _engine_preset(exit_block)
    if preset is None:
        return None
    loss_cap, p2_enabled, tiers, hard, weak, dead = preset
    tiers = tiers if p2_enabled else []
    return {
        "loss_cap_pct": loss_cap,
        "time_cap_min": hard["after_min"] if hard else None,
        "first_lock": tiers[0] if tiers else None,
        "lock_steps": len(tiers),
        "lock_tiers": tiers,
        "weak_peak_cut": weak,
        "dead_weight_cut": dead,
    }


def _limit_facts(config):
    gr = (config.get("risk") or {}).get("guard_rails") if _is_map(config.get("risk")) else None
    if not gr or not _is_map(gr):
        return None
    count, pause = _num(gr.get("max_consecutive_losses")), _num(gr.get("cooldown_seconds"))
    return {
        "max_entries_per_day": _num(gr.get("max_entries_per_day")),
        "daily_loss_pct": _num(gr.get("daily_loss_limit_pct")),
        "loss_streak": {"count": count, "pause_s": pause} if count is not None and pause is not None else None,
        "drawdown_halt_pct": _num(gr.get("drawdown_halt_pct")),
        "same_market_wait_s": _num(gr.get("per_asset_cooldown_seconds")),
    }


def _entry_scanner(config):
    for s in config.get("scanners") or []:
        if _is_map(s) and s.get("type") == "external_scanner":
            return s
    return None


def _js_numbers(v):
    """JSON as the runtime writes it: JS has one number type, so YAML `10.0` is `10` there."""
    if isinstance(v, float) and v.is_integer():
        return int(v)
    if isinstance(v, list):
        return [_js_numbers(x) for x in v]
    if isinstance(v, dict):
        return {k: _js_numbers(x) for k, x in v.items()}
    return v


def strategy_facts(config, manifest=None):
    return _js_numbers(_strategy_facts(config, manifest))


def _strategy_facts(config, manifest):
    config = config if _is_map(config) else {}
    strategy = config.get("strategy") if _is_map(config.get("strategy")) else {}
    scanner = _entry_scanner(config)
    inputs = scanner.get("inputs") if scanner and _is_map(scanner.get("inputs")) else {}
    catalog = manifest.get("catalog") if _is_map(manifest) and _is_map(manifest.get("catalog")) else {}
    return {
        "v": FACTS_VERSION,
        **_universe_facts(inputs, catalog),
        "direction": _direction(inputs, catalog),
        "leverage_default": _num(strategy.get("default_leverage")),
        "leverage_max": _leverage_max(inputs),
        **_margin_facts(strategy, inputs),
        "slots": _num(strategy.get("slots")),
        "scan_interval_s": _num(scanner.get("interval_seconds")) if scanner else None,
        "exit": _exit_facts(config.get("exit")),
        "limits": _limit_facts(config),
    }
