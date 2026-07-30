"""LEMON — supervised scanner (Runtime 3.0 port of the v2 Lemon "Degen Fader").

BASKET fader. Per tick it:
  - reads the account + held set (clearinghouse; dual-DEX equity via max(), never
    sum(); plus the v2 read-sanity guard),
  - fetches the smart-money leaderboard markets board and filters it to the fixed
    TRACKED_ASSETS basket (12 crypto majors + 4 XYZ commodity/index names; XYZ is
    NOT banned — v2 producer XYZ_BANNED=False),
  - derives BTC 4h (crypto-only MACRO_TREND_GATE input),
  - for each non-held, non-cooled-down candidate: scores the CONTRARIAN fade via the
    pure `scoring.evaluate_fade` (with a read-guarded per-asset funding fetch and the
    US-session clock bonus hoisted in),
  - emits the SINGLE highest-scoring fade (v2 main() emitted only `best`), sized at
    MARGIN_PCT (PERCENT) with conviction-tiered leverage (5/7/10x, clamped to 10x).

The emitted `direction` is the OPPOSITE of the SM consensus by design — Lemon fades
the crowd. Read-only + single-pass — emits a `marginPct` intent (PERCENT) plus a
`leverage`; the runtime sizes the dollars, owns slots/dedup/risk gates, and trails
the DSL exit. No daemon, no push_signal, no create_position.

FIDELITY NOTES vs lemon-producer.py v2.0.1:
  - v2 computed two contributors INSIDE evaluate_fade that require I/O or the clock:
    the per-asset funding read (market_get_asset_data) and the US-session check
    (datetime.utcnow()). To keep scoring.py pure, scan.py fetches funding (read-
    guarded; None on failure => the +1 funding bonus simply can't fire, matching v2's
    try/except: pass) and computes us_session, then passes both into evaluate_fade.
    The scoring math is otherwise byte-for-byte the v2 logic.
  - v2 SKILL.md is STALE (describes v1.1: XYZ_BANNED=True, MIN_SCORE 8, leverage to
    20x, margin 50%). The PRODUCER + config + runtime.yaml are the truth and are what
    this port reproduces: XYZ_BANNED=False, MIN_SCORE 9, leverage tiers 5/7/10 capped
    at 10x, MARGIN_PCT 30%, plus the PER_ASSET_TREND_GATE the SKILL never mentions.
  - v2 TRACKED_XYZ listed "SPX", but the live HL XYZ DEX / leaderboard token for the
    S&P is "SP500" (HL meta has no "SPX"). With v2's list, "SPX" could NEVER match a
    live market token, so that name was DEAD in v2. This port uses the live symbol
    "SP500" so the S&P is actually tradeable. FLAGGED in the report.
  - v2 per-asset cooldown (cfg.is_asset_cooled_down, 120 min, Python state file) is
    reproduced in ctx.state here (the runtime also enforces per_asset_cooldown_seconds
    7200 as the primary gate; this is the producer-side backstop, verbatim TTL).
  - v2 emitted exactly one signal (best). Preserved: scan() emits <= 1 signal/tick.
  - v2 margin_usd = account_value * MARGIN_PCT (0.30) computed in the producer. The
    Runtime 3.0 port emits a PERCENT marginPct (30) and lets the runtime size the
    dollars off withdrawable — same intent, no hardcoded account read for sizing.
"""

import sys
import time
from datetime import datetime, timezone

import scoring

# v2 producer universe (lemon-producer.py v2.0.1 TRACKED_CRYPTO + TRACKED_XYZ).
# NOTE: v2's "SPX" -> live HL XYZ symbol is "SP500" (see FIDELITY NOTES).
_TRACKED_CRYPTO_DEFAULT = ["BTC", "ETH", "SOL", "HYPE", "AVAX", "DOGE", "LINK",
                           "XRP", "ADA", "NEAR", "UNI", "AAVE"]
_TRACKED_XYZ_DEFAULT = ["BRENTOIL", "CL", "GOLD", "SP500"]

_DEFAULT_MIN_SCORE = 9                 # v2 MIN_SCORE
_DEFAULT_MARGIN_PCT = 30.0             # v2 MARGIN_PCT=0.30 -> 30 PERCENT
_DEFAULT_MAX_POSITIONS = 1            # v2 slots:1
_DEFAULT_LEADERBOARD_LIMIT = 100      # v2 fetch_sm_data limit=100
_DEFAULT_PER_ASSET_COOLDOWN = 7200    # v2 per_asset_cooldown 120 min -> 7200s
_DEFAULT_TTL = 7200                   # signal-dedup TTL (mirror per-asset cooldown)


def _read(ctx, name, args):
    """Guarded MCP read: a transient/permission error on a read must NOT roll back
    the whole tick. Returns None on failure so the degrade paths apply."""
    try:
        return ctx.senpi_mcp.call_tool(name, args)
    except Exception as exc:  # noqa: BLE001
        print(f"[lemon.scan] {name} read failed: {exc!r}", file=sys.stderr)
        return None


def _dex_for(asset):
    """XYZ (HIP-3) assets pass dex='xyz'; main-DEX assets pass ''."""
    return "xyz" if str(asset).lower().startswith("xyz:") else ""


# ── ACCOUNT + HELD ASSETS (port of v2 cfg.get_positions, verbatim shape) ──

def _get_account(ctx):
    """(account_value, [held_coin, ...]) from strategy_get_clearinghouse_state.

    READ-GUARDED. Dual-DEX equity collapse: account_value via max() across main/xyz
    (two views of ONE cross-margined wallet — summing double-counts the shared free
    balance -> 2x sizing). Includes the v2 read-sanity guard (margin in use + empty
    positions -> skip tick) verbatim."""
    ch = _read(ctx, "strategy_get_clearinghouse_state", {"strategy_wallet": ctx.wallet})
    if not ch:
        return 0.0, []
    data = ch.get("data", ch) if isinstance(ch, dict) else ch
    if not isinstance(data, dict):
        return 0.0, []

    held, account_value = [], 0.0
    for section in ("main", "xyz"):
        s = data.get(section, {})
        if not isinstance(s, dict):
            continue
        ms = s.get("marginSummary", {})
        account_value = max(account_value, scoring.safe_float(ms.get("accountValue", 0)))
        for ap in s.get("assetPositions", []) or []:
            pos = ap.get("position", ap) if isinstance(ap, dict) else {}
            szi = scoring.safe_float(pos.get("szi", 0))
            if szi == 0:
                continue
            coin = pos.get("coin", "")
            if coin:
                held.append(coin)

    # read-sanity guard (funding/$0 glitch 2026-06, ported verbatim from v2): a
    # corrupt clearinghouse read can report margin/notional IN USE while returning an
    # EMPTY positions list; sizing or running the held-asset dedup off that re-enters
    # held names (pyramiding) and mis-sizes. Skip the tick.
    _use = 0.0
    for _sec in ("main", "xyz"):
        _s = data.get(_sec, {}) if isinstance(data, dict) else {}
        _ms = _s.get("marginSummary", {}) if isinstance(_s, dict) else {}
        _use = max(_use, scoring.safe_float(_ms.get("totalMarginUsed", 0)),
                   abs(scoring.safe_float(_ms.get("totalNtlPos", 0))))
    if _use > 1.0 and not held:
        print("[lemon.scan] read-sanity guard: margin in use but empty positions — skipping tick",
              file=sys.stderr)
        return 0.0, []
    return account_value, held


# ── MARKET FETCHING (port of v2 fetch_sm_data, verbatim parse + normalize) ──

def _fetch_sm_map(ctx, limit, tracked, xyz_banned):
    """Returns {TOKEN: normalized_market} filtered to `tracked` (uppercase). XYZ is
    kept unless xyz_banned (v2 XYZ_BANNED=False). Canonical asset uses the 'xyz:'
    prefix for XYZ-DEX names exactly as v2 built it."""
    raw = _read(ctx, "leaderboard_get_markets", {"limit": limit})
    if not raw:
        return {}
    markets = raw
    if isinstance(markets, dict):
        markets = markets.get("data", markets)
    if isinstance(markets, dict):
        markets = markets.get("markets", markets)
    if isinstance(markets, dict):
        markets = markets.get("markets", [])
    if not isinstance(markets, list):
        return {}

    tracked_set = {t.upper() for t in tracked}
    sm_map = {}
    for m in markets:
        if not isinstance(m, dict):
            continue
        raw = str(m.get("token", ""))
        token = raw.upper()  # casing-ok: sm_map key + tracked-set compare only; emit uses raw (case-preserved)
        dex = str(m.get("dex", "")).lower()
        if xyz_banned and dex == "xyz":
            continue
        if token not in tracked_set:
            continue
        sm_map[token] = {
            "asset": f"xyz:{raw}" if dex == "xyz" else raw,
            "dex": dex,
            "is_xyz": dex == "xyz",
            "direction": str(m.get("direction", "")).upper(),
            "pct": scoring.safe_float(m.get("pct_of_top_traders_gain", 0)),
            "traders": int(m.get("trader_count", 0)),
            "price_chg_4h": scoring.safe_float(m.get("token_price_change_pct_4h", 0)),
            "price_chg_1h": scoring.safe_float(m.get("token_price_change_pct_1h",
                                               m.get("price_change_1h", 0))),
            "contrib_15m": scoring.safe_float(m.get("contribution_pct_change_15m", 0)),
            "contrib_1h": scoring.safe_float(m.get("contribution_pct_change_1h", 0)),
            "contrib_4h": scoring.safe_float(m.get("contribution_pct_change_4h", 0)),
        }
    return sm_map


def _get_funding(ctx, canonical):
    """Per-asset funding rate via market_get_asset_data, or None. READ-GUARDED — on
    any failure return None so the +1 funding bonus simply doesn't fire (v2 wrapped
    this exact read in try/except: pass). Ported from v2 evaluate_fade's inline call."""
    ad = _read(ctx, "market_get_asset_data", {
        "asset": canonical,
        "candle_intervals": [],
        "include_funding": True,
        "include_order_book": False,
        "dex": _dex_for(canonical),
    })
    if not ad:
        return None
    d = ad.get("data", ad) if isinstance(ad, dict) else ad
    if not isinstance(d, dict):
        return None
    ac = d.get("asset_context", d.get("assetContext", {}))
    if not isinstance(ac, dict):
        return None
    return scoring.safe_float(ac.get("funding", 0))


# ── ctx.state: per-asset cooldown + signal dedup (port of v2 asset-cooldowns.json) ──

def _load_state(ctx):
    last = (ctx.state.last() or {}) if ctx.state else {}
    cooldowns = dict(last.get("cooldowns") or {})   # {TOKEN: ts}  (per-asset cooldown)
    return cooldowns


def _is_cooled_down(cooldowns, token, cooldown_seconds, now):
    """True if `token` is still within its per-asset cooldown window (v2
    is_asset_cooled_down semantics: blocked while elapsed < cooldown)."""
    last_ts = cooldowns.get(token.upper(), 0)
    if not last_ts:
        return False
    return (now - last_ts) < cooldown_seconds


def scan(inputs, ctx):
    now = time.time()
    tracked_crypto = inputs.get("trackedCrypto", _TRACKED_CRYPTO_DEFAULT)
    tracked_xyz = inputs.get("trackedXyz", _TRACKED_XYZ_DEFAULT)
    tracked = list(tracked_crypto) + list(tracked_xyz)
    min_score = float(inputs.get("minScore", _DEFAULT_MIN_SCORE))
    margin_pct = float(inputs.get("marginPct", _DEFAULT_MARGIN_PCT))      # PERCENT (0,100]
    # defensive: a pasted FRACTION (<=1.0) means margin was stored as 0.30 -> 30%.
    if 0 < margin_pct <= 1.0:
        margin_pct *= 100.0
    max_positions = int(inputs.get("maxPositions", _DEFAULT_MAX_POSITIONS))
    xyz_banned = bool(inputs.get("xyzBanned", False))                      # v2 XYZ_BANNED=False
    leaderboard_limit = int(inputs.get("leaderboardLimit", _DEFAULT_LEADERBOARD_LIMIT))
    tiers = inputs.get("leverageTiers", scoring.DEFAULT_LEVERAGE_TIERS)
    max_leverage = int(inputs.get("maxLeverage", scoring.MAX_LEVERAGE))
    cooldown_seconds = float(inputs.get("perAssetCooldownSeconds", _DEFAULT_PER_ASSET_COOLDOWN))

    cooldowns = _load_state(ctx)

    def _persist(result):
        if ctx.state is None:
            return
        try:
            ctx.state.append({"cooldowns": cooldowns, "result": result})
        except Exception as exc:  # noqa: BLE001
            print(f"[lemon.scan] WARNING: state append failed; next tick may re-emit: {exc!r}",
                  file=sys.stderr)

    # ── account + held set ──
    account_value, held_assets = _get_account(ctx)
    if account_value <= 0:
        print("[lemon.scan] WAITING — no account value; skip tick", file=sys.stderr)
        _persist({"ts": now, "emitted": False, "gate": "no_account"})
        return []
    held_set = {h.upper() for h in held_assets}

    # ── max-positions guard (1-slot fader) ──
    if len(held_set) >= max_positions:
        print(f"[lemon.scan] WAITING — riding open position(s): {sorted(held_set)}",
              file=sys.stderr)
        _persist({"ts": now, "emitted": False, "gate": "max_positions",
                  "held": sorted(held_set)})
        return []

    # ── SM markets board (filtered to the basket) ──
    sm_map = _fetch_sm_map(ctx, leaderboard_limit, tracked, xyz_banned)
    if not sm_map:
        print("[lemon.scan] WAITING — no SM market data; skip tick", file=sys.stderr)
        _persist({"ts": now, "emitted": False, "gate": "no_markets"})
        return []

    # BTC 4h for the crypto-only MACRO_TREND_GATE (v2 main()).
    btc_4h = scoring.safe_float(sm_map.get("BTC", {}).get("price_chg_4h", 0))

    # US session bonus input (v2 used datetime.utcnow().hour in evaluate_fade).
    hour = datetime.now(timezone.utc).hour
    us_session = 13 <= hour <= 21

    # ── score each tracked candidate (held + cooldown filtered BEFORE scoring, v2 main()) ──
    candidates = []
    scanned = 0
    for token, sm in sm_map.items():
        if _is_cooled_down(cooldowns, token, cooldown_seconds, now):
            continue
        # skip if the canonical (possibly xyz:-prefixed) asset is held in any direction
        if sm["asset"].upper() in held_set or token in held_set:
            continue
        scanned += 1
        funding = _get_funding(ctx, sm["asset"])   # None => funding bonus can't fire
        c = scoring.evaluate_fade(sm, btc_4h, funding, us_session, inputs)
        if c is not None and c["score"] >= min_score:
            candidates.append(c)

    if not candidates:
        print(f"[lemon.scan] WAITING — no fade signal (min score {min_score:.0f}); "
              f"scanned={scanned} btc_4h={btc_4h:.2f} held={sorted(held_set)}",
              file=sys.stderr)
        _persist({"ts": now, "emitted": False, "gate": "no_candidate",
                  "scanned": scanned, "btc_4h": round(btc_4h, 3),
                  "held": sorted(held_set)})
        return []

    # ── pick the single strongest fade (v2 main() emits only `best`) ──
    candidates.sort(key=lambda c: c["score"], reverse=True)
    best = candidates[0]
    leverage = scoring.get_leverage_for_score(best["score"], tiers, max_leverage)

    cooldowns[best["asset"].upper()] = now
    # also key the bare token (XYZ canonical is 'xyz:NAME' but the board token is bare)
    cooldowns[best["asset"].upper().replace("XYZ:", "")] = now

    print(f"[lemon.scan] EMIT {best['asset']} {best['direction']} (fade {best['smDirection']}) "
          f"score={best['score']} {leverage}x marginPct={margin_pct:.1f}% | {best['reasons'][:6]}",
          file=sys.stderr)
    _persist({"ts": now, "emitted": True, "asset": best["asset"],
              "direction": best["direction"], "score": best["score"],
              "leverage": leverage, "candidates": len(candidates),
              "scanned": scanned, "btc_4h": round(btc_4h, 3)})

    return [{
        "asset": best["asset"],
        "direction": best["direction"],            # FADE direction (opposite of SM) — by design
        "marginPct": margin_pct,                   # PERCENT in (0,100] — runtime sizes the dollars
        "leverage": leverage,                      # 5/7/10x by score; runtime applies + clamps
        "data": {
            "score": best["score"],
            "leverage": leverage,
            "direction": best["direction"],
            "isXyz": bool(best["is_xyz"]),
            "smDirection": best["smDirection"],
            "smPct": float(best["smPct"]),
            "smTraders": int(best["smTraders"]),
            "priceChg4h": float(best["priceChg4h"]),
            "contrib15m": float(best["contrib15m"]),
            "reasons": best["reasons"],
            "heldAssets": sorted(held_set),
        },
    }]
