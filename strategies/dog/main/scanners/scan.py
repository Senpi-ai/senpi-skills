"""DOG — supervised scanner (Runtime 3.0 port of the v2 Dog contrarian fader).

Multi-asset BASKET scanner over the four liquid majors (BTC/ETH/SOL/HYPE). Per tick:
  - read the account + held set (clearinghouse, dual-DEX equity via max(), XYZ enumerated),
  - fetch the top-100 smart-money leaderboard markets,
  - build the per-asset map keeping the highest-conviction SM direction per asset
    (v2 pre-pass: max pct_of_top_traders_gain),
  - enrich ONCE per tick (market-wide funding regime + per-asset funding_history +
    per-asset funding rate),
  - score each tracked asset via the pure `scoring.score_market` (CONTRARIAN FLIP +
    every v2.5 gate),
  - filter by MIN_SCORE, drop held assets (defense-in-depth dedup), and emit ALL
    qualifying candidates (v2 main() pushed every qualified candidate; the runtime's
    slots + risk.guard_rails own the ceiling).

Read-only + single-pass — emits a `marginPct` intent (PERCENT, flat) plus a conviction-
tiered `leverage` (7x base, 10x at score >=10, clamped to the per-asset venue cap). The
runtime sizes the dollars, owns cooldowns/risk gates, and trails the DSL exit. No daemon,
no push_signal, no create_position.

FIDELITY NOTES vs dog-producer.py v3.0.0 (thesis frozen at v2.5):
  - v2's wallet/held dedup, regime/funding enrichment, and CONTRARIAN FLIP scoring are
    reproduced verbatim. The per-asset funding rate (v2 fetched inline via
    market_get_asset_data inside score_market) is fetched here in scan.py and passed
    into the pure scoring.score_market(asset_funding=...), keeping scoring.py I/O-free.
    The thresholds (>0.0002 / <-0.0002) and the "funding pays the fade" logic are
    verbatim.
  - v2 read datetime.now(timezone.utc).hour inside score_market; here scan.py reads the
    clock and passes utc_hour in (scoring.py is clock-free). The 13<=h<=21 US-session
    window is verbatim.
  - v2 EMITTED ALL qualifying candidates (qualified list, sorted by score desc), then
    skipped any already-held asset at push time. Preserved: scan() emits every qualified,
    non-held candidate. The runtime's slots:1 + per_asset_cooldown own the real ceiling
    (v2 relied on runtime guard_rails for the same).
  - v2 producer-side dedup was the runtime's per_asset_cooldown (no Python TTL cache in
    v3.0); this port keeps a lightweight per-tick result record in ctx.state for
    observability but does NOT add a producer-side dedup TTL (faithful — v3.0 dropped it).
  - margin is FLAT 30% (v2 runtime.yaml strategy.margin_pct: 30 — already a PERCENT, NOT
    a fraction; no x100 conversion needed). v2 did NOT conviction-tier the margin (only
    the leverage). Defensive guard still applies: a value <=1.0 would be a pasted
    fraction -> x100.
"""

import sys
import time
from datetime import datetime, timezone

import scoring


# v2.5 defaults (dog-producer.py constants; overridable via inputs)
_ASSETS_DEFAULT = ["BTC", "ETH", "SOL", "HYPE"]
_DEFAULT_MIN_SCORE = 8                 # v2.5 MIN_SCORE (contrarian floor)
_DEFAULT_MARGIN_PCT = 30.0             # v2 runtime.yaml strategy.margin_pct (PERCENT)
_DEFAULT_LEADERBOARD_LIMIT = 100       # v2 leaderboard_get_markets limit
_MIN_TRADERS = 30                      # informational; gate lives in scoring.score_market


def _read(ctx, name, args):
    """Guarded MCP read: a transient/permission error on a read must NOT roll back the
    whole tick (the contract rolls ANY exception back to []). Returns None on failure so
    the existing degrade paths apply (markets empty -> skip tick; regime None -> neutral;
    funding 0.0 -> the funding bonus just doesn't fire)."""
    try:
        return ctx.senpi_mcp.call_tool(name, args)
    except Exception as exc:  # noqa: BLE001
        print(f"[dog.scan] {name} read failed: {exc!r}", file=sys.stderr)
        return None


# ── ACCOUNT + HELD ASSETS (port of v2 fetch_held_assets, dual-DEX) ──

def _get_account(ctx):
    """(account_value, held_assets_list) from strategy_get_clearinghouse_state.
    READ-GUARDED. Dual-DEX: account_value via max() across main/xyz (two views of
    ONE cross-margined wallet — summing double-counts the shared free balance);
    assetPositions enumerated across both sub-DEX sections (v2 fetch_held_assets)."""
    ch = _read(ctx, "strategy_get_clearinghouse_state", {"strategy_wallet": ctx.wallet})
    if not ch:
        return 0.0, []
    data = ch.get("data", ch) if isinstance(ch, dict) else ch
    if not isinstance(data, dict):
        return 0.0, []
    account_value = 0.0
    held = []
    for section in ("main", "xyz"):
        s = data.get(section, {})
        if not isinstance(s, dict):
            continue
        ms = s.get("marginSummary", {})
        account_value = max(account_value, scoring.safe_float(ms.get("accountValue", 0)))
        for ap in s.get("assetPositions", []) or []:
            pos = ap.get("position", ap) if isinstance(ap, dict) else {}
            if scoring.safe_float(pos.get("szi", 0)) != 0:
                coin = pos.get("coin", "")
                if coin:
                    held.append(str(coin))
    return account_value, held


# ── MARKET FETCH (port of v2 main() leaderboard parse) ──

def _fetch_markets(ctx, limit):
    raw = _read(ctx, "leaderboard_get_markets", {"limit": limit})
    if not raw:
        return []
    markets = raw.get("data", raw) if isinstance(raw, dict) else raw
    if isinstance(markets, dict):
        markets = markets.get("markets", markets)
    if isinstance(markets, dict):
        markets = markets.get("markets", [])
    if not isinstance(markets, list):
        return []
    return markets


# ── REGIME (port of v2 fetch_funding_regime) ──

def _fetch_regime(ctx):
    r = _read(ctx, "market_get_funding_regime", {})
    if not r:
        return None
    data = r.get("data", r) if isinstance(r, dict) else r
    if isinstance(data, dict):
        return data.get("regime")
    return None


# ── PER-ASSET FUNDING HISTORY (port of v2 fetch_funding_history, parser fix preserved) ──

def _fetch_funding_history(ctx, asset):
    """{persistence_hours, trend} for one asset or None. READ-GUARDED.

    v2.4 parser fix preserved: MCP returns data.data=[{asset, ...}], not data.<field>.
    Iterate the list, match by asset, normalize the funding_trend enum
    (INTENSIFYING->INCREASING, DECAYING->DECREASING)."""
    r = _read(ctx, "market_get_funding_history", {"asset": asset})
    if not r:
        return None
    outer = r.get("data", r) if isinstance(r, dict) else r
    rows = outer.get("data") if isinstance(outer, dict) else None
    if not rows:
        return None
    row = next((x for x in rows if isinstance(x, dict) and x.get("asset") == asset), None)
    if row is None:
        return None
    raw_trend = (row.get("funding_trend") or row.get("trend") or "").upper()
    if raw_trend == "INTENSIFYING":
        trend = "INCREASING"
    elif raw_trend == "DECAYING":
        trend = "DECREASING"
    else:
        trend = raw_trend
    return {"persistence_hours": row.get("persistence_hours"), "trend": trend}


# ── PER-ASSET FUNDING RATE (port of v2 score_market's inline market_get_asset_data) ──

def _fetch_asset_funding(ctx, asset):
    """Current 8h funding rate for one asset (float), 0.0 on any failure.
    READ-GUARDED. v2 fetched this inline inside score_market; pulled out here to
    keep scoring.py pure. The funding bonus simply doesn't fire on a 0.0 fallback."""
    ad = _read(ctx, "market_get_asset_data", {
        "asset": asset,
        "candle_intervals": [],
        "include_funding": True,
        "include_order_book": False,
    })
    if not ad:
        return 0.0
    d = ad.get("data", ad) if isinstance(ad, dict) else ad
    if not isinstance(d, dict):
        return 0.0
    ac = d.get("asset_context", d.get("assetContext", {}))
    if isinstance(ac, dict):
        return scoring.safe_float(ac.get("funding", 0))
    return 0.0


def scan(inputs, ctx):
    now = time.time()
    assets = inputs.get("assets", _ASSETS_DEFAULT)
    min_score = float(inputs.get("minScore", _DEFAULT_MIN_SCORE))
    margin_pct = float(inputs.get("marginPct", _DEFAULT_MARGIN_PCT))   # PERCENT (0,100]
    leaderboard_limit = int(inputs.get("leaderboardLimit", _DEFAULT_LEADERBOARD_LIMIT))

    # Defensive: a value <=1.0 is a pasted FRACTION (v2 stored 0.30) -> x100 (dire/koala
    # guard). Dog's v2 runtime stored 30 (a PERCENT) so this never fires in practice.
    if 0 < margin_pct <= 1.0:
        margin_pct *= 100.0

    assets_upper = [str(a).upper() for a in assets]
    asset_set = set(assets_upper)

    # ── account + held assets ──
    account_value, held_assets = _get_account(ctx)
    if account_value <= 0:
        print("[dog.scan] cannot read account value; skip tick", file=sys.stderr)
        _persist(ctx, now, {"emitted": False, "gate": "no_account"})
        return []
    held_set = {h.upper() for h in held_assets}

    # ── fetch SM markets ──
    markets = _fetch_markets(ctx, leaderboard_limit)
    if not markets:
        print("[dog.scan] failed to fetch leaderboard_get_markets; skip tick", file=sys.stderr)
        _persist(ctx, now, {"emitted": False, "gate": "no_markets"})
        return []

    # ── pre-pass: per-asset map keeping highest-conviction direction (v2 main) ──
    asset_data = {}
    for m in markets:
        if not isinstance(m, dict):
            continue
        token = str(m.get("token", "")).upper()
        dex = m.get("dex", "")
        if dex or token not in asset_set:
            continue
        pct = scoring.safe_float(m.get("pct_of_top_traders_gain", 0))
        if token not in asset_data or pct > scoring.safe_float(
                asset_data[token].get("pct_of_top_traders_gain", 0)):
            asset_data[token] = m

    # ── enrich ONCE per run — shared context across all candidates (v2 main) ──
    regime = _fetch_regime(ctx)
    fh_map = {tok: _fetch_funding_history(ctx, tok) for tok in asset_data}
    funding_map = {tok: _fetch_asset_funding(ctx, tok) for tok in asset_data}
    utc_hour = datetime.now(timezone.utc).hour

    # ── score each tracked asset (v2 iterates ASSETS in fixed order) ──
    candidates = []
    for token in assets_upper:
        m = asset_data.get(token)
        if not m:
            continue
        c = scoring.score_market(m, regime, fh_map.get(token),
                                 funding_map.get(token, 0.0), utc_hour)
        if c is not None:
            candidates.append(c)

    # ── filter by MIN_SCORE, sort desc (v2 main) ──
    qualified = [c for c in candidates if c["score"] >= min_score]
    qualified.sort(key=lambda c: c["score"], reverse=True)

    if not qualified:
        best = max(candidates, key=lambda c: c["score"], default=None)
        note = (f"SNIFFING: best {best['asset']} fade={best['direction']} "
                f"score={best['score']}<{min_score:.0f}") if best else "no exhaustion setups"
        print(f"[dog.scan] WAITING — {note}; regime={regime} "
              f"scanned={len(markets)} held={sorted(held_set)}", file=sys.stderr)
        _persist(ctx, now, {"emitted": False, "gate": "no_candidate",
                            "candidates": len(candidates), "regime": regime,
                            "note": note})
        return []

    # ── emit ALL qualifying, non-held candidates (v2 main pushed every qualified) ──
    out = []
    emitted = []
    for c in qualified:
        if c["asset"].upper() in held_set:        # defense-in-depth dedup (v2 push_signal)
            continue
        out.append({
            "asset": c["asset"],
            "direction": c["direction"],          # the FADE direction (post contrarian flip)
            "marginPct": margin_pct,              # FLAT PERCENT (0,100] — runtime sizes the dollars
            "leverage": c["leverage"],            # conviction-tiered 7/10x; runtime applies + clamps
            "data": {
                "score": c["score"],
                "smDirection": c["sm_direction"],
                "leverage": c["leverage"],
                "reasons": c["reasons"],
                "smPct": float(c["sm_pct"]),
                "smTraders": int(c["sm_traders"]),
                "priceChange4hPct": float(c["p4h"]),
                "priceChange1hPct": float(c["p1h"]),
                "contribChange15m": float(c["cc_15m"]),
                "contribChange1h": float(c["cc_1h"]),
                "contribChange4h": float(c["cc_4h"]),
                "fundingRegime": c["regime"],
                "persistenceHours": c["persistence_hours"],
                "crowdingTrend": c["crowding_trend"],
                "assetFunding": float(c["asset_funding"]),
                "heldAssets": sorted(held_set),
            },
        })
        emitted.append({"asset": c["asset"], "direction": c["direction"],
                        "score": c["score"], "leverage": c["leverage"]})

    if not out:
        print(f"[dog.scan] WAITING — {len(qualified)} qualified but all held "
              f"{sorted(held_set)}; regime={regime}", file=sys.stderr)
        _persist(ctx, now, {"emitted": False, "gate": "all_held",
                            "qualified": len(qualified), "regime": regime})
        return out

    top = emitted[0]
    print(f"[dog.scan] EMIT {len(out)} fade(s); top {top['asset']} {top['direction']} "
          f"score={top['score']} {top['leverage']}x regime={regime} | "
          f"{qualified[0]['reasons'][:6]}", file=sys.stderr)
    _persist(ctx, now, {"emitted": True, "count": len(out), "signals": emitted,
                        "candidates": len(candidates), "qualified": len(qualified),
                        "regime": regime, "held": sorted(held_set)})
    return out


def _persist(ctx, now, result):
    """Append a concise per-tick result to ctx.state EVERY tick (observability).
    Guarded — a disabled history store or append failure must not crash the tick."""
    if ctx.state is None:
        return
    try:
        ctx.state.append({"signaled": result.get("emitted", False),
                          "result": dict(result, ts=now)})
    except Exception as exc:  # noqa: BLE001
        print(f"[dog.scan] WARNING: state append failed: {exc!r}", file=sys.stderr)
