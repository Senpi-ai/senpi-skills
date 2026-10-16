"""ROTATOR — pure blended-conviction math (no I/O, no MCP, no clock).

Every rebalance the scan hands this module three SHORT-HORIZON reads — cross-asset flows
(alts lagging the leader's move), the market funding regime (crowding = contrarian), and
leaderboard momentum events (winners entering strong phases) — and it blends them into ONE
conviction score per candidate, in the direction the leader moved. scan.py owns the
reads/state; this module is the numbers, unit-testable in isolation."""

from datetime import datetime, timezone


def _f(x, *keys, default=0.0):
    """Float from x. If keys given, x is a dict and we try each key in order."""
    if keys:
        if not isinstance(x, dict):
            return default
        for k in keys:
            if x.get(k) is not None:
                try:
                    return float(x[k])
                except (TypeError, ValueError):
                    continue
        return default
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def unwrap_flow(raw):
    """market_get_cross_asset_flows -> the flow dict ({leader/leader_move_pct, laggards})."""
    if not isinstance(raw, dict):
        return {}
    d = raw.get("data", raw)
    if isinstance(d, dict) and isinstance(d.get("flows"), dict):
        d = d["flows"]
    return d if isinstance(d, dict) else {}


def leader_move_from_flow(flow_data):
    """Read the leader's current 4h move from an unwrapped flow dict.

    v2-quirk: producer reads `data['leader']['move_pct']` (a `leader` BLOCK with a
    `move_pct` field). The cross-asset-flow-guide documents the alternate shape
    with TOP-LEVEL `leader_move_pct`. We honour the producer's primary path first
    (leader block) then fall back to the top-level field, so either tool shape
    yields the same number. Returns float (0.0 if absent)."""
    if not isinstance(flow_data, dict):
        return 0.0
    leader_block = flow_data.get("leader") or {}
    if isinstance(leader_block, dict) and "move_pct" in leader_block:
        return _f(leader_block.get("move_pct"))
    return _f(flow_data.get("leader_move_pct"))


def laggards(flow, inputs):
    """Filtered catch-up candidates, each tagged with the direction the LEADER moved
    (leader up => laggards should catch up => LONG; leader down => SHORT). Gates on the
    tool's pre-computed follow_rate, confidence, |gap_pct|."""
    lead = leader_move_from_flow(flow)
    if lead == 0:
        return []
    direction = "LONG" if lead > 0 else "SHORT"
    min_follow = float(inputs.get("minFollowRate", 0.7))
    min_conf = float(inputs.get("minConfidence", 0.5))
    min_gap = float(inputs.get("minGapPct", 0.5))
    out = []
    for lg in (flow.get("laggards") or []):
        if not isinstance(lg, dict):
            continue
        coin = lg.get("asset") or lg.get("coin") or lg.get("symbol")
        if not coin:
            continue
        follow, conf, gap = _f(lg, "follow_rate"), _f(lg, "confidence"), _f(lg, "gap_pct")
        if follow < min_follow or conf < min_conf or abs(gap) < min_gap:
            continue
        out.append({"coin": coin, "direction": direction, "leader_move": lead,
                    "follow": follow, "conf": conf, "gap": gap})
    return out


def funding_tilt(regime, direction, weight):
    """Crowding = contrarian. LONG_CROWDED (longs pay, crowded) dampens LONGs / boosts
    SHORTs; SHORT_CROWDED the reverse; NEUTRAL is 0. Returns an additive tilt."""
    r = (regime or "").upper()
    if r == "LONG_CROWDED":
        return -weight if direction == "LONG" else +weight
    if r == "SHORT_CROWDED":
        return +weight if direction == "LONG" else -weight
    return 0.0


def _iso_epoch(s):
    """ISO-8601 (the feed's `detected_at`, e.g. 2026-09-15T20:22:06Z) -> epoch seconds, None if unparseable."""
    try:
        dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def events_by_coin(events, now, inputs):
    """Aggregate fresh (within eventWindowMin of `detected_at`) tier>=minTier momentum events
    per coin, with the net side lean. An event is a TRADER crossing a 4h delta-PnL tier; the
    markets driving it are nested in `top_positions` [{market, direction, delta_pnl}], so each
    position counts for its bare-upper market. Returns ({COIN: {count, longs, shorts}}, event_count)."""
    win = float(inputs.get("eventWindowMin", 240)) * 60.0
    min_tier = int(inputs.get("minEventTier", 2))
    per, total = {}, 0
    for e in events or []:
        if not isinstance(e, dict):
            continue
        if int(_f(e, "tier", default=1)) < min_tier:
            continue
        ts = _iso_epoch(e.get("detected_at"))
        if ts and now and (now - ts) > win:
            continue
        total += 1
        for p in e.get("top_positions") or []:
            if not isinstance(p, dict):
                continue
            coin = str(p.get("market") or "").split(":", 1)[-1].upper()
            if not coin:
                continue
            side = str(p.get("direction") or "").upper()
            d = per.setdefault(coin, {"count": 0, "longs": 0, "shorts": 0})
            d["count"] += 1
            if side == "LONG":
                d["longs"] += 1
            elif side == "SHORT":
                d["shorts"] += 1
    return per, total


def event_boost(coin, direction, ev_per, ev_total, inputs):
    """Per-coin boost if winners are entering this coin in OUR direction, plus a small
    market-wide activity floor (winners active => a touch more conviction)."""
    w_coin = float(inputs.get("eventCoinWeight", 1.5))
    w_mkt = float(inputs.get("eventMarketWeight", 0.5))
    rec = ev_per.get(str(coin).split(":", 1)[-1].upper(), {})
    aligned = rec.get("longs", 0) if direction == "LONG" else rec.get("shorts", 0)
    return min(1.0, aligned / 2.0) * w_coin + min(1.0, ev_total / 8.0) * w_mkt


def blended_score(lg, regime, ev_per, ev_total, inputs):
    """Blend the three short-horizon reads into ONE conviction score:
      flow  = normalized |gap| * follow_rate * confidence (the catch-up expectation)
      fund  = contrarian funding tilt
      event = winners entering this name + market activity."""
    w_flow = float(inputs.get("flowWeight", 4.0))
    w_fund = float(inputs.get("fundingWeight", 1.5))
    gap_norm = min(1.0, abs(lg["gap"]) / float(inputs.get("gapFullPct", 3.0)))
    flow = gap_norm * lg["follow"] * lg["conf"] * w_flow
    fund = funding_tilt(regime, lg["direction"], w_fund)
    ev = event_boost(lg["coin"], lg["direction"], ev_per, ev_total, inputs)
    score = round(flow + fund + ev, 3)
    reasons = [
        f"{lg['coin']} lags leader {lg['leader_move']:+.2f}% (gap {lg['gap']:+.2f}%, "
        f"follow {lg['follow']:.0%}, conf {lg['conf']:.2f})",
        f"funding {regime or 'NEUTRAL'} tilt {fund:+.2f}",
        f"momentum-event boost {ev:+.2f} ({ev_total} tier2+ events)",
    ]
    return {"coin": lg["coin"], "direction": lg["direction"], "score": score,
            "gap": lg["gap"], "follow": lg["follow"], "conf": lg["conf"], "reasons": reasons}


def band_for(score, inputs):
    if score >= float(inputs.get("apexScore", 7.0)):
        return "apex"
    if score >= float(inputs.get("goodScore", 5.5)):
        return "good"
    return "base"


def sizing_for(band, inputs):
    """Aggressive, conviction-banded sizing for a concentrated (<=2 slot) book.
    Returns (leverage:int, marginPct:float) — marginPct a PERCENT of withdrawable."""
    mtiers = inputs.get("marginPctTiers") or {"apex": 42, "good": 32, "base": 22}
    ltiers = inputs.get("leverageTiers") or {"apex": 5, "good": 5, "base": 4}
    return int(_f(ltiers, band, default=4)), round(_f(mtiers, band, default=22), 4)
