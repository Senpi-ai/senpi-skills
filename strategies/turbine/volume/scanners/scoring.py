"""TURBINE VOLUME — pure volume-rotation math (no I/O, no MCP, no clock-as-side-effect).

A faithful Runtime 3.0 port of the v2 Turbine producer's volume-rotation logic
(turbine-producer.py v3.2.2). Turbine is a VOLUME / MARKET-MAKING engine, NOT a
directional scorer: there is no "score" — every gated candidate is emitted to churn
notional volume for builder-fee recycling. The "thesis" here is just:

  1. funding-fade DIRECTION selection (choose_direction)        — keeps the book neutral
  2. spread parsing from an order book (parse_asset_data)       — the only entry gate
  3. deterministic rotation-asset selection (pick_rotation_asset)

Reproduced VERBATIM from the v2 producer so a fidelity harness can diff this against
turbine-producer.py on the same market snapshot. Behaviour-preserving quirks are kept
and flagged `# v2-quirk`. This module is pure (no MCP, no file I/O); the caller
(scan.py) fetches order-book/funding via ctx.senpi_mcp and passes plain dicts in.

The randomness in the v2 producer (random.random() XYZ/main pool pick, random.choice
LONG/SHORT on a neutral funding regime) is preserved — it is core to the volume engine
spreading evenly across the universe. The caller seeds `rng` so the pool/coin-flip
choices stay test-reproducible while remaining stochastic in production.
"""


def _f(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def is_xyz(asset):
    return bool(asset) and asset.startswith("xyz:")


def normalize_coin_key(coin):
    """Canonicalize a coin string for held-slot dedup sets.

    v3.2.2: preserve the xyz: prefix so main:HYPE and xyz:HYPE are DISTINCT slot
    occupiers (the v3.2.1 bug stripped the prefix, collapsing them and undercounting
    slots -> over-emit). Lowercase the prefix, uppercase the symbol. Ported verbatim
    from cfg.normalize_coin_key."""
    if not coin:
        return ""
    s = coin.strip()
    if ":" in s:
        prefix, _, symbol = s.partition(":")
        return f"{prefix.lower()}:{symbol.upper()}"
    return s.upper()


def choose_direction(regime, rng):
    """Funding fade. Crowded longs -> SHORT, crowded shorts -> LONG, flat -> coin-flip.

    Verbatim from v2 choose_direction. `rng` is the caller's random.Random so the
    neutral-regime coin-flip is reproducible in tests but stochastic in production
    (the v2 producer used the module-global `random`)."""
    r = (regime or "").upper()
    if r in ("LONG_CROWDED", "LONG_HEAVY"):
        return "SHORT", "funding_fade_short"
    if r in ("SHORT_CROWDED", "SHORT_HEAVY"):
        return "LONG", "funding_fade_long"
    return rng.choice(["LONG", "SHORT"]), "alternate_neutral"


def parse_asset_data(resp):
    """Extract {bid, ask, mid, spread_bps, funding_regime, funding_annualized_pct}
    from a market_get_asset_data response, or None if the book is unusable.

    Ported VERBATIM from the v2 producer's query_asset_data parsing (the MCP call
    itself lives in scan.py to keep this module pure). Returns None on any missing /
    malformed level so the caller skips the asset (degrade, never crash)."""
    if not resp or not isinstance(resp, dict):
        return None
    ad = resp.get("data", resp)
    if not isinstance(ad, dict):
        return None
    ob = ad.get("order_book") or ad.get("orderBook") or {}
    levels = ob.get("levels", []) if isinstance(ob, dict) else []
    if not isinstance(levels, list) or len(levels) < 2:
        return None
    bids, asks = levels[0], levels[1]
    if not bids or not asks:
        return None

    def _lvl_px(lvl):
        if isinstance(lvl, dict):
            return _f(lvl.get("px", lvl.get("price", 0)))
        if isinstance(lvl, list) and lvl:
            return _f(lvl[0])
        return 0.0

    bid = _lvl_px(bids[0])
    ask = _lvl_px(asks[0])
    if bid <= 0 or ask <= 0:
        return None
    mid = (bid + ask) / 2.0
    spread_bps = ((ask - bid) / mid) * 10000 if mid > 0 else 999

    funding_regime = (ad.get("funding_regime") or ad.get("fundingRegime") or "UNKNOWN").upper()
    funding_annualized_pct = _f(
        ad.get("funding_annualized_pct") or ad.get("fundingAnnualizedPct") or 0
    )
    return {
        "bid": bid,
        "ask": ask,
        "mid": mid,
        "spread_bps": round(spread_bps, 3),
        "funding_regime": funding_regime,
        "funding_annualized_pct": funding_annualized_pct,
    }


def pick_rotation_asset(rot_idx, xyz_weight, held_set, last_closed, now,
                        xyz_pool, main_pool, rng, cooldown_seconds=90):
    """Pick the next rotation asset. Probabilistic XYZ/main pool weighting +
    deterministic rotation index inside each pool. Skips held names + post-close
    cooldown. Returns (asset|None, new_rot_idx).

    Ported VERBATIM from the v2 producer's pick_rotation_asset. `last_closed` is the
    dedup map {coin_key: epoch_seconds_closed}; `now` is the caller's wall clock (the
    v2 producer read time.time() inline — passed in here to keep this module pure)."""
    use_xyz = rng.random() < xyz_weight
    pool = xyz_pool if use_xyz else main_pool

    def _scan_pool(pool, rot_idx):
        n = len(pool)
        for _ in range(n):
            candidate = pool[rot_idx % n]
            rot_idx = (rot_idx + 1) % n
            coin_key = normalize_coin_key(candidate)
            if coin_key in held_set:
                continue
            last = last_closed.get(coin_key, {})
            if last and (now - _f(last.get("ts", 0))) < cooldown_seconds:
                continue
            return candidate, rot_idx
        return None, rot_idx

    asset, rot_idx = _scan_pool(pool, rot_idx)
    if asset is not None:
        return asset, rot_idx
    # Fall back to the other pool (v2 behaviour: if the chosen pool is fully
    # held/cooled, try the other before giving up).
    other = main_pool if use_xyz else xyz_pool
    return _scan_pool(other, rot_idx)
