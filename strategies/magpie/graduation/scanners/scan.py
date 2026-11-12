"""MAGPIE · GRADUATION book — supervised scanner (Runtime 3.0 port of the v2
Magpie GRADUATION leg).

DYNAMIC UNIVERSE + STATE. Each tick it classifies every LIVE xyz instrument
IPOP-vs-STANDARD off market_list_instruments by the VENUE'S LEVERAGE CAP plus a
volume floor (the v2 funding band is opt-in and off — see scoring), compares each
name's cap against the PRIOR-tick cache (held in ctx.state) to detect a CONVERSION
(the cap lifts above the IPOP cap on a name trading over the floor), stamps each
flip into a conversionWindowHours eligibility
window, and within that window scores the post-conversion momentum (+ SM +
rising-volume bonuses) and emits a marginPct intent + per-signal leverage clamped
to the now-lifted venue cap. Read-only, single-pass.

State (v2 used on-disk class-state-*.json + conversions-*.json; 3.0 keeps both in
the per-instance ctx.state record): {class_state: {name: {class, max_leverage,
funding}} — a pre-1.2.0 state file carries the bare class string and is read as
such}, conversions: {name: detected_epoch}, recent: {COIN: ts}}. The FIRST tick only
seeds class_state (no known prior -> no flips); flips are only detected against a
known prior, exactly like v2. That is a limit, not an oversight: a conversion is a
TRANSITION, and the live board carries no field that dates a listing or a cap
change (market_list_instruments rows: name, max_leverage, is_delisted,
only_isolated, margin_modes, sz_decimals, context.{funding, dayNtlVlm, markPx,
...} — read 2026-10-12), so a name that is already STANDARD when this book first
runs cannot be told from one that converted months ago. The earliest tradable flip
is the one between the first and the second tick; the runtime reads the state file
back on restart, so the first tick happens once per install, not once per restart.
tests/test_magpie.py pins this.

The momentum read uses CLOSED hourly candles only (scoring.closed_candles): the
feed's last row is the bar in progress, and a read off it is the live price against
a six-bar-old close — it changed every tick, so a thesis could appear and vanish
inside one hour.

The wallet is read (strategy_get_clearinghouse_state) on every tick that has a
name inside the window, and a name it already holds is skipped. Before 1.2.0
nothing looked at the positions, so a held name was emitted again every tick the
12-minute dedup allowed (every other 10-minute tick). That read is the one
exception to degrade-and-continue: unreadable positions mean NO opens this tick
(logged; the class cache and the window are still reconciled and kept).

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
    Returns {name: {class, max_leverage, vol_usd, funding}}; only instruments that
    actually exist (live, non-delisted, xyz: prefix) are included. An unreadable cap
    reads as 999 (never a pre-IPO name), as in the pre-listing book. Degrades to {}."""
    raw = _read(ctx, "market_list_instruments", {"dex": "xyz"})
    if not raw or (isinstance(raw, dict) and not raw.get("success", True)):
        return {}
    data = raw.get("data", raw) if isinstance(raw, dict) else raw
    instruments = data.get("instruments", data) if isinstance(data, dict) else data
    if not isinstance(instruments, list):
        return {}
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
            max_lev = int(inst.get("max_leverage", 999))
        except (TypeError, ValueError):
            max_lev = 999
        vol_usd = float(scoring._f(ctx_block, "dayNtlVlm"))
        out[name] = {
            "class": scoring.classify_instrument(max_lev, vol_usd, funding_abs, config),
            "max_leverage": max_lev,
            "vol_usd": vol_usd,
            "funding": funding_abs,
        }
    return out


def _reconcile(scan, prev_class_state, conversions, config, now):
    """Detect conversions (the cap lifting above the IPOP cap on a name trading over
    the floor) vs the prior cache, stamp each into the conversion-window dict, prune
    stamps older than conversionWindowHours, and return (new_class_state,
    live_conversions, names_in_window). The new cache keeps class, cap and funding per
    name so the next tick's flip test reads the CAP, not just the class."""
    window_hours = float(config.get("conversionWindowHours", 72))
    new_class_state = {}
    conv = dict(conversions)
    for name, info in scan.items():
        if scoring.detect_conversion(prev_class_state.get(name), info["max_leverage"], info["vol_usd"], config):
            conv[name] = now
            print(f"[magpie.graduation.scan] CONVERSION detected: {name} cap lifted above the IPOP cap",
                  file=sys.stderr)
        new_class_state[name] = {"class": info["class"], "max_leverage": info["max_leverage"],
                                 "funding": info["funding"]}
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


def _fetch_sm_direction(ctx, asset, min_traders=10):
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
    side_n = {}                                    # per-side headcount of the 4h leaders
    for m in markets:
        if not isinstance(m, dict):
            continue
        token = str(m.get("token", m.get("coin", m.get("asset", "")))).upper()
        if not _sm_row_matches(m, token, asset):
            continue
        found = True
        d = str(m.get("direction", "")).upper()
        side_n[d] = int(m.get("trader_count", 0) or 0)
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
    if side_n.get("LONG" if long_ratio >= 50 else "SHORT", 0) < min_traders:
        return None, 0.0   # the leading side is too thin (< minTraderCount of the 4h leaders) to call a lean
    return ("LONG", long_ratio) if long_ratio >= 50 else ("SHORT", 100 - long_ratio)


def _held_names(ctx):
    """Names the wallet already holds, read off strategy_get_clearinghouse_state.

    Returns (names, ok). `names` is a set of UPPER-cased, xyz-qualified coin names from BOTH
    sections of the one wallet (`main` and `xyz` are two views of the same account; positions
    live INSIDE each section, never at the top level). A coin in the `xyz` section may arrive
    bare (`SPCX`) or qualified (`xyz:SPCX`); both become `XYZ:SPCX`, so a held xyz name always
    matches the universe's spelling and a main-DEX twin (`GOLD` vs `xyz:GOLD`) never does.
    `ok` is False when the read failed or came back in no readable shape: the caller then opens
    nothing this tick, because a held name it cannot see would be re-emitted and pyramided."""
    if not ctx.wallet:
        print("[magpie.graduation.scan] no wallet on ctx — positions unreadable", file=sys.stderr)
        return set(), False
    ch = _read(ctx, "strategy_get_clearinghouse_state", {"strategy_wallet": ctx.wallet})
    if not isinstance(ch, dict) or not ch.get("success", True):
        return set(), False
    data = ch.get("data", ch)
    held, readable = set(), False
    for section_name in ("main", "xyz"):
        section = data.get(section_name) if isinstance(data, dict) else None
        if not isinstance(section, dict):
            continue
        readable = True
        for ap in section.get("assetPositions", []) or []:
            pos = ap.get("position", ap) if isinstance(ap, dict) else None
            if not isinstance(pos, dict) or scoring._f(pos, "szi") == 0:
                continue
            coin = str(pos.get("coin", "") or "").upper()
            if not coin:
                continue
            if section_name == "xyz" and not coin.startswith("XYZ:"):
                coin = "XYZ:" + coin
            held.add(coin)
    if not readable:
        print("[magpie.graduation.scan] clearinghouse read has no main/xyz section — positions unreadable",
              file=sys.stderr)
    return held, readable


def scan(inputs, ctx):
    min_score = int(inputs.get("minScore", 5))
    min_traders = int(inputs.get("minTraderCount", 10))   # 4h-board headcount floor
    margin_pct = float(inputs.get("marginPct", 25))   # PERCENT of withdrawable (0,100], not a fraction
    max_lev = int(inputs.get("maxLeverage", 3))
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

    held, held_ok = _held_names(ctx)
    if not held_ok:
        print("[magpie.graduation.scan] HOLD — wallet positions unreadable; no opens this tick "
              "(a held name cannot be told from a new one; the class cache and the window are kept)",
              file=sys.stderr)
        _persist(class_state, conversions)
        return out

    for name in sorted(in_window):
        cu = name.upper()
        info = scan_now.get(name)
        if not info:                       # converted name dropped from the live board — skip
            continue
        if cu in held:                     # already holding — never re-emit a held name
            continue
        last_sig = recent.get(cu)
        if last_sig is not None and (now - last_sig) < ttl:   # signal-dedup
            continue
        c1h = scoring.closed_candles(_fetch_candles(ctx, name), scoring.HOUR_SECONDS, now)   # closed bars only
        if not c1h:
            continue
        sm_dir, sm_tilt = _fetch_sm_direction(ctx, name, min_traders)
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
