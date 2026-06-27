"""SCORPION — supervised external scanner (Runtime 3.0 port of the v2 multi-market trader).

Port of senpi-skills/scorpion (producer v5.0.0, runtime v4.1.2) to the v2 supervised
`scan(inputs, ctx)` contract. The producer daemon is gone — the runtime supervisor calls
scan() every interval_seconds. No push_signal, no execution, no in-Python counters.

Per tick:
  1. READ leaderboard_get_markets (the SM market rows), score every row via the pure
     `scoring.score_market` (verbatim v2 thesis), keep candidates >= MIN_SCORE.
  2. ENRICH once/run: BTC 24h macro (crypto only), funding regime, current held assets.
  3. DEDUP: skip held assets (v2 producer's PRIMARY held-asset fix), skip post-close
     cooldown (v2's Cheetah-pattern serial-reentry backstop, now in ctx.state), skip
     recently-emitted coins (in-flight dedup).
  4. EMIT all survivors with marginPct (PERCENT) + per-row direction; the runtime sizes
     the dollars, owns the slot ceiling + risk gates, and trails the DSL exit.

What moved from the v2 producer to the runtime / scan:
  - The v2 LLM gate's filters were DETERMINISTIC (held-asset hard skip, score floors,
    recentEntry>=2). Those are ported into the scan + producer dedup here; the action is
    rule-mode (the whole strategy-v2 fleet convention — no llm rubber-stamp). The
    btcMacroDirection / fundingRegime / xyzPeerMomentum fields are still computed and
    carried on data{} for telemetry + parity with the v2 signal payload.
  - The v2 producer's per-tick emission CAP (it never capped — it emitted all candidates)
    is unchanged: emit all survivors, runtime's strategy.slots: 2 applies the ceiling.

READ-GUARD: every ctx.senpi_mcp.call_tool is wrapped — a transient/permission read error
must NOT roll back the whole tick (the scaffold rolls back ctx.state on an exception). A
failed market read returns []; a failed enrichment read degrades that one factor.
"""

import sys
import time

import scoring

_DEFAULT_POST_CLOSE_SECONDS = 600   # v2 POST_CLOSE_COOLDOWN_MINUTES: 10
_DEFAULT_RECENT_TTL = 60            # don't re-emit a coin within one tick window


# ── guarded MCP reads ──────────────────────────────────────────

def _read(ctx, name, args):
    """Guarded read. Returns the tool result, or None on any failure (the caller's
    degrade path applies). Never lets a read error roll back the tick."""
    try:
        return ctx.senpi_mcp.call_tool(name, args)
    except Exception as exc:  # noqa: BLE001
        print(f"[scorpion.scan] {name} read failed: {exc!r}", file=sys.stderr)
        return None


def _get_markets(ctx):
    """leaderboard_get_markets → list of market-row dicts (or [] on failure)."""
    raw = _read(ctx, "leaderboard_get_markets", {"limit": 100})
    if not raw:
        return []
    markets = raw.get("data", raw) if isinstance(raw, dict) else raw
    if isinstance(markets, dict):
        markets = markets.get("markets", markets)
    if isinstance(markets, dict):
        markets = markets.get("markets", [])
    return markets if isinstance(markets, list) else []


def _fetch_btc_macro(ctx):
    """v2 fetch_btc_macro: BTC 1h candles → 24h macro direction/pct. Read here,
    arithmetic in scoring.btc_macro_from_candles."""
    ad = _read(ctx, "market_get_asset_data", {
        "asset": "BTC",
        "candle_intervals": ["1h"],
        "include_funding": False,
        "include_order_book": False,
    })
    if not ad:
        return {"direction": None, "pct": None}
    data = ad.get("data", ad) if isinstance(ad, dict) else ad
    candles_1h = ((data or {}).get("candles", {}) or {}).get("1h", []) if isinstance(data, dict) else []
    return scoring.btc_macro_from_candles(candles_1h)


def _fetch_funding_regime(ctx):
    """v2 fetch_funding_regime: market_get_funding_regime → regime string or None."""
    fr = _read(ctx, "market_get_funding_regime", {})
    if not fr:
        return None
    data = fr.get("data", fr) if isinstance(fr, dict) else fr
    if isinstance(data, dict):
        return data.get("regime")
    return None


def _fetch_held_assets(ctx):
    """v2 fetch_held_assets: strategy_get_clearinghouse_state → list of held coins.
    Enumerates BOTH main + xyz sub-DEX sections (one cross-margined wallet, two views)."""
    ch = _read(ctx, "strategy_get_clearinghouse_state", {"strategy_wallet": ctx.wallet})
    if not ch:
        return []
    data = ch.get("data", ch) if isinstance(ch, dict) else ch
    if not isinstance(data, dict):
        return []
    held = []
    for section in ("main", "xyz"):
        s = data.get(section, {})
        if not isinstance(s, dict):
            continue
        for ap in s.get("assetPositions", []):
            pos = ap.get("position", ap)
            try:
                szi = float(pos.get("szi", 0) or 0)
            except (TypeError, ValueError):
                szi = 0.0
            if szi == 0:
                continue
            coin = pos.get("coin", "")
            if coin:
                held.append(coin)
    return held


# ── ctx.state: post-close cooldown + recent-signal dedup ───────
#
# One snapshot record per tick:
#   {"prevHeld": [coins], "lastClosed": {COIN: epoch}, "signaled": {COIN: epoch},
#    "result": {...telemetry...}}
# v2 kept previously_held.json + last_closed.json on disk; here both live in ctx.state.

def _load_state(ctx):
    if ctx.state is None or len(ctx.state) == 0:
        return set(), {}, {}
    last = ctx.state.last() or {}
    prev_held = set(last.get("prevHeld", []) or [])
    last_closed = dict(last.get("lastClosed", {}) or {})
    signaled = dict(last.get("signaled", {}) or {})
    return prev_held, last_closed, signaled


def _detect_closes(current_held_set, prev_held_set, last_closed, now):
    """v2 detect_closes — anything in prev_held but not current_held just closed →
    record its close timestamp for post-close cooldown enforcement."""
    closed = prev_held_set - current_held_set
    for asset in closed:
        last_closed[asset] = now
    return closed


def _token_of(asset):
    """Bare token for an asset label (xyz:BRENTOIL → BRENTOIL; ZEC → ZEC)."""
    if ":" in asset:
        return asset.split(":", 1)[1].upper()
    return asset.upper()


def _in_post_close_cooldown(asset, last_closed, cooldown_seconds, now):
    """v2 is_in_post_close_cooldown — block if the asset closed within the window."""
    ts = last_closed.get(_token_of(asset))
    if ts is None:
        return False
    return (now - ts) < cooldown_seconds


def _prune(d, max_age, now):
    return {k: v for k, v in d.items() if (now - v) < max_age}


# ── entry point ────────────────────────────────────────────────

def scan(inputs, ctx):
    now = time.time()
    margin_pct = float(inputs.get("marginPct", 25))   # PERCENT of withdrawable (0,100], not a fraction
    post_close_seconds = float(inputs.get("postCloseCooldownSeconds", _DEFAULT_POST_CLOSE_SECONDS))
    recent_ttl = float(inputs.get("recentSignalTtlSeconds", _DEFAULT_RECENT_TTL))

    # 1) READ + SCORE every market row (verbatim v2 thesis)
    markets = _get_markets(ctx)
    if not markets:
        return []
    candidates = []
    for m in markets:
        if not isinstance(m, dict):
            continue
        c = scoring.score_market(m, inputs)
        if c is not None:
            candidates.append(c)
    if not candidates:
        if ctx.state is not None:
            try:
                ctx.state.append({"prevHeld": [], "lastClosed": {}, "signaled": {},
                                  "result": {"ts": now, "scanned": len(markets), "candidates": 0,
                                             "emitted": 0}})
            except Exception as exc:  # noqa: BLE001
                print(f"[scorpion.scan] WARNING: state append failed: {exc!r}", file=sys.stderr)
        return []

    # 2) ENRICH once/run (shared context for every candidate)
    btc_macro = _fetch_btc_macro(ctx)
    funding_regime = _fetch_funding_regime(ctx)
    held_assets = _fetch_held_assets(ctx)
    held_norm = {h.upper() for h in held_assets}
    current_held_set = set(held_norm)

    # 3) ctx.state: post-close cooldown (detect closes since last tick) + dedup
    prev_held_set, last_closed, signaled = _load_state(ctx)
    _detect_closes(current_held_set, prev_held_set, last_closed, now)
    last_closed = _prune(last_closed, post_close_seconds * 4, now)
    signaled = _prune(signaled, recent_ttl * 4, now)

    candidates.sort(key=lambda c: c["score"], reverse=True)
    xyz_peer_map = scoring.compute_xyz_peer_momentum(candidates)

    out = []
    skipped_held = skipped_post_close = skipped_recent = 0
    for c in candidates:
        asset = c["asset"]
        au = asset.upper()
        token = _token_of(asset)
        # v2 PRIMARY held-asset dedup — never add to an existing position
        if au in held_norm or token in held_norm:
            skipped_held += 1
            continue
        # v2 post-close cooldown backstop (serial-reentry)
        if _in_post_close_cooldown(asset, last_closed, post_close_seconds, now):
            skipped_post_close += 1
            continue
        # in-flight recent-signal dedup
        last_sig = signaled.get(au)
        if last_sig is not None and (now - last_sig) < recent_ttl:
            skipped_recent += 1
            continue

        macro_ctx = scoring.build_macro_context(c, btc_macro)
        peer_count = xyz_peer_map.get((c["token"], c["direction"]), 0) if c["is_xyz"] else 0

        out.append({
            "asset": asset,
            "direction": c["direction"],
            "marginPct": margin_pct,           # SIZING INTENT (PERCENT) — runtime sizes the dollars
            "leverage": 5,                     # v2 had no per-signal tiers; moderate default (also strategy.default_leverage)
            "data": {
                "score": c["score"],
                "isXyz": c["is_xyz"],
                "direction": c["direction"],
                "reasons": c["reasons"],
                "smPct": c["sm_pct"],
                "smTraders": c["sm_traders"],
                "priceChange4hPct": c["p4h"],
                "priceChange1hPct": c["p1h"],
                "contribChange15m": c["cc_15m"],
                "contribChange1h": c["cc_1h"],
                "contribChange4h": c["cc_4h"],
                "btcMacroDirection": macro_ctx["direction"],
                "btcMacro24hPct": macro_ctx["pct"],
                "fundingRegime": funding_regime or "UNKNOWN",
                "heldAssets": held_assets,
                "xyzPeerMomentum": peer_count,
            },
        })
        signaled[au] = now

    # 4) PERSIST next-tick state (rolled back automatically if this tick errors/times out)
    result = {"ts": now, "scanned": len(markets), "candidates": len(candidates),
              "emitted": len(out), "skipped_held": skipped_held,
              "skipped_post_close": skipped_post_close, "skipped_recent": skipped_recent,
              "btc_macro": btc_macro, "funding_regime": funding_regime,
              "held_assets": held_assets}
    print(f"[scorpion.scan] scanned={len(markets)} candidates={len(candidates)} "
          f"emitted={len(out)} skipped(held={skipped_held},post_close={skipped_post_close},"
          f"recent={skipped_recent})", file=sys.stderr)
    if ctx.state is not None:
        try:
            ctx.state.append({
                "prevHeld": sorted(current_held_set),
                "lastClosed": last_closed,
                "signaled": signaled,
                "result": result,
            })
        except Exception as exc:  # noqa: BLE001
            print(f"[scorpion.scan] WARNING: state append failed; next tick may re-emit "
                  f"suppressed signals: {exc!r}", file=sys.stderr)
    return out
