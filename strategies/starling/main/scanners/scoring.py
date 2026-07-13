"""STARLING — pure consensus engine (no I/O, no MCP, no clock).

Smart-Money Rotation Follower. All the tunable/decidable logic lives here so it is
unit-testable without a network. scan.py does the MCP orchestration and calls into
these pure functions.

The edge: a cohort of proven-profitable Hyperliquid wallets is snapshotted every
tick; when SEVERAL of them are NEWLY, freshly piling into the same name in the same
direction at the same time (consensus FORMING = a rotation), Starling opens with
them — bigger when more of them agree. It fires on the snapshot-to-snapshot DIFF
(consensus forming / rising), NOT on a name that has been at consensus for a while
(stale standing consensus is already priced). It NEVER closes — the DSL owns every
exit (rotate-by-attrition, exactly like gibbon).

Two pure halves:
  1. COHORT / STATE EXTRACTION — `traders_of` / `realized` (cohort derivation, ported
     verbatim-in-spirit from gibbon/_cohorts) and the tolerant position readers that
     turn a batch of discovery_get_trader_state payloads into consensus counts.
  2. THE DIFF + SIZING — `consensus_counts` (distinct wallets per asset/direction),
     `fresh_picks` (the newly-formed/rising snapshot diff), and band → leverage/margin.

NOTE: the discovery_get_trader_state / discovery_get_top_traders payload SHAPES were
NOT live-verified when this was written (the auth token was invalid), so extraction
tries every spelling the deployed corpus uses (gibbon/oxpecker/albatross patterns):
trader address under traderAddress|trader_address|address|wallet; open positions under
openPositions|open_positions|positions; coin under coin|asset; direction from the
`szi` sign. Verify against a real payload before trusting the counts.
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0


def _f(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def _num(v):
    """Float or None (distinguishes a real 0.0 from a missing / unparseable field)."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _bare_upper(name):
    """Bare, upper-cased asset key: strips any venue prefix (e.g. 'xyz:NVDA' -> 'NVDA')."""
    return str(name).split(":", 1)[-1].upper()


# ── COHORT DERIVATION — tolerant readers (ported verbatim-in-spirit from gibbon) ──

def traders_of(d):
    """Unwrap a discovery_* list from a bare list or a {traders|data|results: [...]} dict."""
    if isinstance(d, list):
        return d
    if isinstance(d, dict):
        for k in ("traders", "data", "results"):
            if isinstance(d.get(k), list):
                return d[k]
    return []


def realized(t):
    """Realized PnL of one top-trader record, trying every spelling in the corpus."""
    for k in ("realizedProfitAndLoss", "realized_profit_and_loss",
              "profit_and_loss_realized", "realizedPnl", "realized_pnl"):
        v = _num((t or {}).get(k))
        if v is not None:
            return v
    return 0.0


def trader_address(t):
    """Wallet address of a top-trader record, lower-cased ('' if none)."""
    return str((t or {}).get("address") or (t or {}).get("trader_address")
               or (t or {}).get("wallet") or "").lower()


# ── TRADER-STATE EXTRACTION — one cohort wallet's open book -> positions ──

def _state_wallet(st):
    return str((st or {}).get("traderAddress") or (st or {}).get("trader_address")
               or (st or {}).get("address") or (st or {}).get("wallet") or "").lower()


def _positions_of(st):
    """The open-position list for one trader state (tolerant of the corpus spellings)."""
    if not isinstance(st, dict):
        return []
    pos = (st.get("openPositions") or st.get("open_positions") or st.get("positions") or [])
    return pos if isinstance(pos, list) else []


def _coin_raw(pos):
    """Raw coin string of one position (carries any venue prefix, e.g. 'xyz:NVDA')."""
    return (pos or {}).get("coin") or (pos or {}).get("asset")


def direction_of(pos):
    """LONG / SHORT from the szi sign; None when flat or undeterminable.
    Falls back to an explicit side/direction string only when szi is absent."""
    szi = _num((pos or {}).get("szi"))
    if szi is not None:
        if szi > 0:
            return "LONG"
        if szi < 0:
            return "SHORT"
        return None  # explicit flat
    s = str((pos or {}).get("direction") or (pos or {}).get("side") or "").upper()
    if s in ("LONG", "BUY", "B"):
        return "LONG"
    if s in ("SHORT", "SELL", "A", "S"):
        return "SHORT"
    return None


def consensus_counts(trader_states):
    """{ASSET: {"LONG": n, "SHORT": n}} — DISTINCT cohort wallets holding each
    (bare-upper asset, direction). One position per coin per wallet in practice;
    a (wallet, asset, direction) seen-set makes the count robust to duplicate
    states across overlapping batches. PURE — no I/O."""
    counts = {}
    seen = set()
    for st in trader_states or []:
        if not isinstance(st, dict):
            continue
        wallet = _state_wallet(st)
        for pos in _positions_of(st):
            if not isinstance(pos, dict):
                continue
            coin = _coin_raw(pos)
            direction = direction_of(pos)
            if not coin or direction is None:
                continue
            asset = _bare_upper(coin)
            # dedup by wallet when known; anonymous positions always count
            wkey = wallet or f"_anon_{id(pos)}"
            key = (wkey, asset, direction)
            if key in seen:
                continue
            seen.add(key)
            rec = counts.setdefault(asset, {"LONG": 0, "SHORT": 0})
            rec[direction] += 1
    return counts


def name_map(trader_states):
    """{BARE_UPPER: representative RAW coin string} across the snapshot, so scan.py
    can emit signal.asset with the venue prefix the DEX requires (xyz: markets
    reject a bare name). Prefers a prefixed spelling if one is ever seen. PURE."""
    out = {}
    for st in trader_states or []:
        if not isinstance(st, dict):
            continue
        for pos in _positions_of(st):
            if not isinstance(pos, dict):
                continue
            coin = _coin_raw(pos)
            if not coin:
                continue
            bare = _bare_upper(coin)
            if bare not in out or (":" in str(coin) and ":" not in str(out[bare])):
                out[bare] = str(coin)
    return out


# ── THE DIFF — fire on newly-formed / rising consensus, never on stale standing ──

def fresh_picks(cur, prev, inputs):
    """Snapshot diff. A candidate (asset, direction) qualifies iff:
        cur >= minConsensus  AND  (prev < minConsensus  OR  cur > prev)
    i.e. consensus has just reached the bar (newly formed) or is still rising —
    NEVER a name that was already at/above the bar and hasn't grown (prev == cur
    >= minConsensus => no pick: stale standing consensus is already priced in).
    Returns [{asset, direction, count}] sorted by count desc. PURE."""
    min_c = int(_f(inputs.get("minConsensus"), 3))
    picks = []
    for asset, dirs in (cur or {}).items():
        for direction in ("LONG", "SHORT"):
            c = int(_f((dirs or {}).get(direction), 0))
            if c < min_c:
                continue
            pc = int(_f(((prev or {}).get(asset) or {}).get(direction), 0))
            if pc < min_c or c > pc:
                picks.append({"asset": asset, "direction": direction, "count": c})
    picks.sort(key=lambda p: p["count"], reverse=True)
    return picks


# ── SIZING — conviction band from the agreement count -> leverage / marginPct ──

def band_for(count, inputs):
    """Conviction band from how many cohort wallets agree."""
    if count >= _f(inputs.get("apexConsensus"), 6):
        return "apex"
    if count >= _f(inputs.get("goodConsensus"), 4):
        return "good"
    return "base"


def sizing_for(band, inputs, venue_max=None):
    """(leverage, marginPct). marginPct is a PERCENT in (0,100]; both clamped to the
    fleet caps (maxLeverage / maxMarginPct) and leverage additionally to venue max."""
    lev_tiers = inputs.get("leverageTiers") or {"apex": 5, "good": 4, "base": 3}
    mgn_tiers = inputs.get("marginPctTiers") or {"apex": 14, "good": 10, "base": 7}
    cap = int(_f(inputs.get("maxLeverage"), 5))
    if venue_max:
        cap = min(cap, int(_f(venue_max, cap)))
    lev = max(1, min(int(_f(lev_tiers.get(band), 3)), cap))
    mgn = _f(mgn_tiers.get(band), 7)
    mgn = max(1.0, min(mgn, _f(inputs.get("maxMarginPct"), 25)))
    return lev, round(mgn, 2)
