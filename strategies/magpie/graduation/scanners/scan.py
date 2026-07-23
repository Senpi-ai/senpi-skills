"""MAGPIE · GRADUATION book — supervised scanner (Runtime 3.0 port of the v2
Magpie GRADUATION leg).

DYNAMIC UNIVERSE + STATE. Each tick it classifies every LIVE xyz instrument
IPOP-vs-STANDARD off market_list_instruments by the funding signature, compares
against the PRIOR-tick class cache (held in ctx.state) to detect IPOP->STANDARD
CONVERSION flips, stamps each flip into a conversionWindowHours eligibility
window, and within that window scores the post-conversion momentum (+ SM +
rising-volume bonuses) and emits a marginPct intent + per-signal leverage clamped
to the now-lifted venue cap. Read-only, single-pass.

State (v2 used on-disk class-state-*.json + conversions-*.json; 3.0 keeps both in
the per-instance ctx.state record): {class_state: {name: 'IPOP'|'STANDARD'},
conversions: {name: detected_epoch}, recent: {COIN: ts}}. The FIRST tick only
seeds class_state (no known prior -> no flips); flips are only detected against a
known prior, exactly like v2.

EVERY ctx.senpi_mcp.call_tool is read-guarded. The instrument-discovery read
failing degrades to {} (no scan, class_state preserved, window untouched) — never
a crash. NO name is ever emitted that is not in the live universe read AND inside
the conversion window."""

import sys
import time

import scoring

def _sm_row_matches(row, token, target):
    """True if leaderboard row `row` is the market for `target`.

    `leaderboard_get_markets` returns BARE tickers (`NVDA`) plus a separate `dex`
    field, while our universe carries the qualified name (`xyz:NVDA`). A raw
    `token != target` compare therefore NEVER matches an xyz name, so every xyz
    instrument reads as "no smart-money data" and a hard SM gate blocks it
    permanently. Compare bare tickers, and require the dex to agree so a main-DEX
    name cannot cross-match its xyz twin (e.g. main `GOLD` vs `xyz:GOLD`)."""
    tok = str(token or "").upper()
    want = str(target or "").upper()
    if tok.split(":", 1)[-1] != want.split(":", 1)[-1]:
        return False
    row_xyz = (str((row or {}).get("dex", "")).strip().lower() == "xyz"
               or tok.startswith("XYZ:"))
    return row_xyz == want.startswith("XYZ:")


_DEFAULT_TTL = 720   # 12m signal-dedup: don't re-fire a converted name while a signal is in flight


def _read(ctx, name, args):
    try:
        return ctx.senpi_mcp.call_tool(name, args)
    except Exception as exc:  # noqa: BLE001
        print(f"[magpie.graduation.scan] {name} read failed: {exc!r}", file=sys.stderr)
        return None


def _scan_instruments(ctx, config):
    """Classify every LIVE xyz instrument IPOP-vs-STANDARD off the live read.
    Returns {name: {class, max_leverage, vol_usd}}; only instruments that
    actually exist (live, non-delisted, xyz: prefix) are included. Degrades to {}."""
    raw = _read(ctx, "market_list_instruments", {"dex": "xyz"})
    if not raw or (isinstance(raw, dict) and not raw.get("success", True)):
        return {}
    data = raw.get("data", raw) if isinstance(raw, dict) else raw
    instruments = data.get("instruments", data) if isinstance(data, dict) else data
    if not isinstance(instruments, list):
        return {}
    ipop_funding_max = float(config.get("ipopFundingMaxAbs", 1e-7))
    ipop_lev_cap = int(config.get("ipopMaxLeverageCap", 5))
    out = {}
    for inst in instruments:
        if not isinstance(inst, dict):
            continue
        name = inst.get("name", "")
        if not isinstance(name, str) or not name.startswith("xyz:") or inst.get("is_delisted", False):
            continue
        ctx_block = inst.get("context", {}) if isinstance(inst.get("context"), dict) else {}
        funding_abs = abs(float(scoring._f(ctx_block, "funding")))
        try:
            max_lev = int(inst.get("max_leverage", 5))
        except (TypeError, ValueError):
            max_lev = 5
        out[name] = {
            "class": scoring.classify_instrument(funding_abs, max_lev, ipop_funding_max, ipop_lev_cap),
            "max_leverage": max_lev,
            "vol_usd": float(scoring._f(ctx_block, "dayNtlVlm")),
        }
    return out


def _reconcile(scan, prev_class_state, conversions, config, now):
    """Detect IPOP->STANDARD flips vs the prior class cache, stamp each into the
    conversion-window dict, prune stamps older than conversionWindowHours, and
    return (new_class_state, live_conversions, names_in_window)."""
    window_hours = float(config.get("conversionWindowHours", 72))
    new_class_state = {}
    conv = dict(conversions)
    for name, info in scan.items():
        curr_class = info["class"]
        prev_class = prev_class_state.get(name)
        if scoring.detect_conversion(prev_class, curr_class):
            conv[name] = now
            print(f"[magpie.graduation.scan] CONVERSION detected: {name} IPOP->STANDARD",
                  file=sys.stderr)
        new_class_state[name] = curr_class
    cutoff = now - (window_hours * 3600.0)
    conv = {k: v for k, v in conv.items() if v >= cutoff}
    return new_class_state, conv, set(conv.keys())


def _fetch_candles(ctx, asset):
    md = _read(ctx, "market_get_asset_data", {
        "asset": asset,
        "candle_intervals": ["1h"],
        "dex": "xyz",
        "include_funding": False,
        "include_order_book": False,
    })
    if not md or (isinstance(md, dict) and not md.get("success", True)):
        return None
    d = md.get("data", md) if isinstance(md, dict) else md
    return (d.get("candles", {}) or {}).get("1h", []) if isinstance(d, dict) else []


def _fetch_sm_direction(ctx, asset):
    raw = _read(ctx, "leaderboard_get_markets", {})
    if not raw or (isinstance(raw, dict) and not raw.get("success", True)):
        return None, 0.0
    markets = raw.get("data", raw) if isinstance(raw, dict) else raw
    if isinstance(markets, dict):
        markets = markets.get("markets", markets.get("leaderboard", markets))
    if isinstance(markets, dict):
        markets = markets.get("markets", [])
    if not isinstance(markets, list):
        return None, 0.0
    long_pct, short_pct, found = 0.0, 0.0, False
    for m in markets:
        if not isinstance(m, dict):
            continue
        token = str(m.get("token", m.get("coin", m.get("asset", "")))).upper()
        if not _sm_row_matches(m, token, asset):
            continue
        found = True
        d = str(m.get("direction", "")).upper()
        pct = scoring._f(m, "pct_of_top_traders_gain", "longPct")
        if d == "LONG":
            long_pct = pct
        elif d == "SHORT":
            short_pct = pct
    if not found:
        return None, 0.0
    total = long_pct + short_pct
    if total <= 0:
        return "NEUTRAL", 50.0
    long_ratio = (long_pct / total) * 100
    return ("LONG", long_ratio) if long_ratio >= 50 else ("SHORT", 100 - long_ratio)


def scan(inputs, ctx):
    min_score = int(inputs.get("minScore", 5))
    margin_pct = float(inputs.get("marginPct", 15))   # PERCENT of withdrawable (0,100], not a fraction
    max_lev = int(inputs.get("maxLeverage", 5))
    ttl = float(inputs.get("recentSignalTtlSeconds", _DEFAULT_TTL))
    now = time.time()

    last = (ctx.state.last() or {}) if ctx.state else {}
    prev_class_state = last.get("class_state", {}) or {}
    conversions = {k: v for k, v in (last.get("conversions") or {}).items()
                   if isinstance(v, (int, float))}
    recent = (last.get("recent") or {})

    scan_now = _scan_instruments(ctx, inputs)

    out = []

    def _persist(class_state, conv):
        if ctx.state is None:
            return
        try:
            ctx.state.append({
                "class_state": class_state, "conversions": conv, "recent": recent,
                "result": {"ts": now, "tracked": len(class_state),
                           "conversions_in_window": sorted(conv.keys()),
                           "emitted": [c["asset"] for c in out]}})
        except Exception as exc:  # noqa: BLE001
            print(f"[magpie.graduation.scan] WARNING: state append failed: {exc!r}", file=sys.stderr)

    if not scan_now:
        # instrument read failed/empty — preserve the prior class cache + window untouched
        print("[magpie.graduation.scan] instrument read empty — preserving class cache",
              file=sys.stderr)
        _persist(prev_class_state, conversions)
        return out

    class_state, conversions, in_window = _reconcile(scan_now, prev_class_state, conversions, inputs, now)

    if not in_window:
        print("[magpie.graduation.scan] WAITING — no IPOP->equity conversion inside the "
              "eligibility window", file=sys.stderr)
        _persist(class_state, conversions)
        return out

    for name in sorted(in_window):
        cu = name.upper()
        info = scan_now.get(name)
        if not info:                       # converted name dropped from the live board — skip
            continue
        last_sig = recent.get(cu)
        if last_sig is not None and (now - last_sig) < ttl:   # signal-dedup
            continue
        c1h = _fetch_candles(ctx, name)
        if not c1h:
            continue
        sm_dir, sm_tilt = _fetch_sm_direction(ctx, name)
        th = scoring.build_thesis_graduation(name, c1h, info.get("max_leverage", 10),
                                             sm_dir, sm_tilt, inputs)
        if not th or th["score"] < min_score:
            continue
        leverage = scoring.clamp_leverage(max_lev, th.get("max_leverage_cap", max_lev))
        if leverage <= 0:
            continue
        out.append({
            "asset": name,                    # a live, in-window xyz name
            "direction": th["direction"],
            "marginPct": margin_pct,          # SIZING INTENT — runtime sizes the dollars
            "leverage": leverage,             # per-signal, clamped to the lifted venue cap
            "data": {
                "score": th["score"], "direction": th["direction"], "leverage": leverage,
                "conversionEvent": True, "momentumPct": th.get("momentum_pct", 0.0),
                "reasons": th["reasons"],
            },
        })
        recent[cu] = now
        print(f"[magpie.graduation.scan] EMIT {name} {th['direction']} {leverage}x "
              f"score={th['score']} mom={th.get('momentum_pct')}% | {th['reasons']}", file=sys.stderr)

    _persist(class_state, conversions)
    return out
