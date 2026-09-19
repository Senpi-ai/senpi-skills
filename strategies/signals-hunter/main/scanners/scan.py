"""SIGNALS-HUNTER — the Senpi Signals engine as a live trader.

The feed you read by hand, traded on a clock. Every tick runs the SAME gather and the SAME detector
library that `senpi-signals` renders, takes the TRADE lens, and opens the names that clear the score
floor. `sweep.py`, `score.py` and `smartmoney.py` here are byte-identical copies of the skill's —
tests/test_engine_parity.py fails if they drift — so a detector fixed in the feed is fixed here.

Why the engine is vendored rather than reimplemented: the detector library IS the strategy. A
hand-written approximation of it silently stops being the thing whose signals you were reading.

Three things this scanner owns, because a feed does not have to care about them:

  1. **History.** Most of the library diffs the current reading against an earlier one — OI surges,
     conviction builds, funding flips, whale moves. A feed run gets that from a state file; a
     scanner keeps the ring in `ctx.state`. With no ring the first tick can only fire the
     point-in-time detectors, which is correct and says so rather than looking quiet.
  2. **A degraded gather is not a quiet market.** If the proven-cohort lens went dark the score is
     computed on OI/funding/price alone — a different number wearing the same name. The tick is
     skipped and the log says which lens was missing.
  3. **A direction it cannot name is not a LONG.** Unscored direction is skipped, never defaulted.

Read-only, single-pass. The runtime owns sizing, slots and the DSL exits.
"""
import datetime
import sys

import score
import sweep

# Ring depth. The slow arm of the trend detector looks ~12h back (score.TREND_LOOKBACK_MIN), so an
# hourly tick needs more than 12 snapshots to have one; 24 gives a day of room without carrying the
# engine's full 48. Each snapshot holds the per-asset cohort book, which is what makes it large.
DEFAULT_RING_MAX = 24
DEFAULT_DEADLINE_S = 100          # sweep.FEED_DEADLINE_S — the whole gather's wall clock
DEFAULT_MIN_SCORE = 65.0          # trade_score floor. The feed's own TRADE floor is 45.
DEFAULT_HIGH_SCORE = 80.0
DEFAULT_MARGIN_PCT = 20.0
DEFAULT_HIGH_MARGIN_PCT = 25.0
DEFAULT_LEVERAGE = 5
DEFAULT_TOP_N = 6                 # score.TOP_N
DEFAULT_FAMILY_CAP = 2            # score.FAMILY_CAP — kills the funding flood
DEFAULT_TTL_S = 7200
DEFAULT_MAX_TOTAL_MARGIN_PCT = 80.0   # of withdrawable, across everything one tick opens

# Detectors the FEED may print but this package must not trade.
#
# `whale_open` says "a proven wallet just opened size". Its freshness rests entirely on the
# third-party `startTime` field, and that field has been observed jumping forward on a position
# whose `szi` and `entryPx` had not moved — 4592s of age reading as 17s. The entry-price
# corroboration added alongside it passes that exact reset, because the position was sitting 0.019%
# from its entry. `check_position_age.py` exists to settle whether the cached path resets and has
# not reported yet.
#
# In the feed that is a sentence a reader can discount. Here it is a 5x entry at a fifth of the
# wallet, on an hourly clock, with nobody between the detector and the order — and the direction it
# gets wrong is the direction we take. Every OTHER detector in the library is a statement about a
# level or a change we measured ourselves; this is the only one whose freshness is asserted by a
# field we have caught being wrong. It trades again when that check reports, not before.
NON_TRADEABLE_DETECTORS = frozenset({"whale_open"})


def _f(v, d):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def _ring_from_state(ctx):
    last = (ctx.state.last() or {}) if ctx.state else {}
    ring = last.get("ring")
    return ([r for r in ring if isinstance(r, dict) and r.get("asset_metrics")] if isinstance(ring, list)
            else []), (last.get("recent") or {})


def _withdrawable(ctx):
    """Free margin, USD. `withdrawable` sits beside `marginSummary` in each dex section and BOTH
    sections are views of ONE cross-margined wallet, so this is a max() — summing double-counts.
    Returns None when the read fails, which is not the same as a wallet with nothing in it."""
    try:
        d = ctx.senpi_mcp.call_tool("strategy_get_clearinghouse_state",
                                    {"strategy_wallet": ctx.wallet})
    except Exception as exc:  # noqa: BLE001
        print(f"[signals-hunter.scan] clearinghouse read failed: {exc!r}", file=sys.stderr)
        return None
    d = d.get("data", d) if isinstance(d, dict) else None
    if not isinstance(d, dict):
        return None
    best = None
    for section in ("main", "xyz"):
        s = d.get(section)
        if not isinstance(s, dict) or s.get("withdrawable") is None:
            continue
        try:
            v = float(s["withdrawable"])
        except (TypeError, ValueError):
            continue
        best = v if best is None else max(best, v)
    return best


def _cohort_is_dark(cov):
    """The cohort fan-out is what separates this from market-pulse. `coverage()` reports the share of
    assets that actually carried a proven-cohort read; zero means the lens never looked."""
    return not _f(cov.get("proven_cohort_pct"), 0.0) and not _f(cov.get("smart_divergence_inputs_pct"), 0.0)


def scan(inputs, ctx):
    now = datetime.datetime.now(datetime.timezone.utc)
    min_score = _f(inputs.get("minScore"), DEFAULT_MIN_SCORE)
    high_score = _f(inputs.get("highScoreThreshold"), DEFAULT_HIGH_SCORE)
    base_margin = _f(inputs.get("marginPct"), DEFAULT_MARGIN_PCT)
    high_margin = _f(inputs.get("highMarginPct"), DEFAULT_HIGH_MARGIN_PCT)
    leverage = int(_f(inputs.get("leverage"), DEFAULT_LEVERAGE))
    top_n = int(_f(inputs.get("topN"), DEFAULT_TOP_N))
    family_cap = int(_f(inputs.get("familyCap"), DEFAULT_FAMILY_CAP))
    ring_max = int(_f(inputs.get("ringMax"), DEFAULT_RING_MAX))
    deadline_s = _f(inputs.get("gatherDeadlineSeconds"), DEFAULT_DEADLINE_S)
    ttl = _f(inputs.get("recentSignalTtlSeconds"), DEFAULT_TTL_S)
    max_total_margin = _f(inputs.get("maxTotalMarginPct"), DEFAULT_MAX_TOTAL_MARGIN_PCT)

    ring, recent = _ring_from_state(ctx)
    held = {str(p.get("coin") or "").upper() for p in (getattr(ctx, "positions", None) or [])}

    # ── 1. gather. Every read inside carries the engine's own per-read timeout and this deadline,
    #        so one hung upstream costs a lens, never the tick. ──
    try:
        rep = sweep.gather(ctx.senpi_mcp.call_tool, top_n=int(_f(inputs.get("universeTopN"), 120)),
                           now=now, deadline_s=deadline_s)
    except Exception as exc:  # noqa: BLE001 — a scan never crashes the runtime
        print(f"[signals-hunter.scan] gather failed: {exc!r} — no tick", file=sys.stderr)
        return []

    cur = rep.get("asset_metrics") or {}
    events = [e for e in (score.normalize_event(e) for e in (rep.get("events") or [])) if e]
    cov = score.coverage(cur)
    if not cur:
        print("[signals-hunter.scan] universe read returned nothing — skipping the tick "
              "(this is a failed read, not an empty market)", file=sys.stderr)
        return []
    if _cohort_is_dark(cov):
        print(f"[signals-hunter.scan] SKIP — the proven-cohort lens went dark "
              f"(assets={cov.get('assets')} divergence_inputs={cov.get('smart_divergence_inputs_pct')}% "
              f"flow_inputs={cov.get('base_flow_inputs_pct')}%). Scoring without it is OI/funding/price "
              f"only — a different number under the same name, so this tick opens nothing.",
              file=sys.stderr)
        return []

    # ── 2. detect against the ring, exactly as score.main() does ──
    baseline = score._pick_baseline(ring, now)
    prior = (baseline or {}).get("asset_metrics", {})
    slow_lookup = score.make_slow_lookup(ring, now)
    base_ts = score._parse_ts((baseline or {}).get("ts"))
    fast_age = round((now - base_ts).total_seconds() / 60.0, 1) if base_ts else None
    signals = score.detect_from_metrics(cur, prior, slow_lookup, fast_age_min=fast_age,
                                        wallets=rep.get("wallets") or {}) + events

    for s in signals:
        cred = round(score.credibility(s.get("notional_vol")) * _f(s.get("source_trust"), 1.0), 3)
        s["credibility"] = cred
        s["freshness"] = score.freshness(s["asset"], s["detector"], {}, now)
        s["trade_score"] = score.trade_score(s, cred)

    # a book too thin to trade is excluded from the TRADE lens, same floor the feed uses
    tradeable = [s for s in signals
                 if _f(s.get("notional_vol"), score.TRADE_CRED_FLOOR) >= score.TRADE_CRED_FLOOR]
    parked = [s for s in tradeable if s.get("detector") in NON_TRADEABLE_DETECTORS]
    if parked:
        print(f"[signals-hunter.scan] parked {len(parked)} signal(s) from non-tradeable detectors "
              f"{sorted({s.get('detector') for s in parked})} — see NON_TRADEABLE_DETECTORS",
              file=sys.stderr)
    tradeable = [s for s in tradeable if s.get("detector") not in NON_TRADEABLE_DETECTORS]
    ranked = score.rank(tradeable, "trade_score", min_score, top_n, family_cap)

    # ── 3. emit ──
    # Every signal in one tick is sized off the SAME balance read, so emitting more than the wallet
    # funds does not shrink the later positions — it fails them outright with insufficient margin,
    # and it fails the LOWER-ranked ones, which is a silent quality bias rather than log noise.
    # 5 slots x 20% is already exactly 100% of withdrawable with zero headroom; the 25% tier puts
    # the intended total over it. So emit only what fits, worst-ranked dropped first.
    free = _withdrawable(ctx)
    out, skipped_dir, committed_pct, unfunded = [], 0, 0.0, 0
    for s in ranked:
        asset = s.get("asset")
        direction = str(s.get("direction") or "").upper()
        if direction not in ("LONG", "SHORT"):
            skipped_dir += 1          # a direction the engine could not name is not a LONG
            continue
        key = f"{str(asset).upper()}_{direction}"
        if asset is None or str(asset).upper() in held:
            continue
        if _f(recent.get(key), 0.0) and (now.timestamp() - _f(recent.get(key), 0.0)) < ttl:
            continue
        ts = _f(s.get("trade_score"), 0.0)
        want_pct = high_margin if ts >= high_score else base_margin
        # `free` is None when the read FAILED, which is not a wallet with nothing in it — in that
        # case fall back to the cap alone rather than refusing to trade on a missing read.
        if committed_pct + want_pct > max_total_margin:
            unfunded += 1
            continue
        committed_pct += want_pct
        out.append({
            "asset": asset,
            "direction": direction,
            "marginPct": want_pct,
            "leverage": leverage,
            "data": {
                "score": ts,
                "direction": direction,
                "detector": s.get("detector"),
                "credibility": s.get("credibility"),
                "oneSidedness": s.get("smart_share"),
                "reasons": [f"{s.get('detector')}: {'; '.join(s.get('numbers') or [])}".strip(": ")],
            },
        })
        recent[key] = now.timestamp()

    if unfunded:
        # Say this on EVERY tick it happens, not only on a quiet one: a tick that opened four and
        # silently dropped two qualifying signals for want of margin is the case worth seeing.
        print(f"[signals-hunter.scan] {unfunded} qualifying signal(s) not emitted — "
              f"{committed_pct:.0f}% of withdrawable already committed this tick against a "
              f"{max_total_margin:.0f}% cap. Lowest-ranked dropped first.", file=sys.stderr)

    if not out:
        free_s = "unread" if free is None else f"${free:,.0f}"
        base_s = (baseline or {}).get("ts") or "none (first tick — diff detectors cannot fire yet)"
        print(f"[signals-hunter.scan] WAITING — no emit | assets={cov.get('assets')} "
              f"signals={len(signals)} tradeable={len(tradeable)} ranked={len(ranked)} "
              f"held={sorted(held)} ring={len(ring)} baseline={base_s} "
              f"(minScore {min_score:.0f}, unnamed_direction={skipped_dir}, "
              f"unfunded={unfunded}, free={free_s})", file=sys.stderr)

    # ── 4. persist the ring ──
    if ctx.state is not None:
        ring.append({"ts": now.isoformat(), "asset_metrics": cur})
        cutoff = now.timestamp() - ttl * 3
        try:
            ctx.state.append({"ring": ring[-max(2, ring_max):],
                              "recent": {k: v for k, v in recent.items() if _f(v, 0.0) >= cutoff}})
        except Exception as exc:  # noqa: BLE001
            print(f"[signals-hunter.scan] WARNING: state append failed: {exc!r} — the next tick "
                  f"loses its diff baseline", file=sys.stderr)
    return out
