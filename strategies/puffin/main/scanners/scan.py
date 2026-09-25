"""PUFFIN — the Senpi Signals engine, concentrated into one conviction position.

SIGNALS-HUNTER with the aperture closed: the SAME vendored engine and the SAME detectors, but a
much higher score floor, ONE slot, most of the wallet behind it at 10x, and a 15-minute clock.

What that changes, and what it does not:

  - It does NOT make the strategy see more. The feed produces thousands of signals a day and an
    85 floor takes roughly three; the binding constraint is the single slot, never the supply.
  - It DOES change time-to-entry. An hourly tick finds a threshold crossing up to 60 minutes after
    it happened. At 15 minutes the entry lands nearer the signal that justified it.
  - It concentrates outcome into one name. Five 20% slots average their mistakes; one 75-90% slot
    does not. That is the trade this package exists to make, and it is stated on the card.

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

# Ring depth is a CAP on state size, NOT the thing that decides how far back the detectors can
# see — see _stratify_ring for why those had to be separated before this package could tick faster
# than hourly. Each snapshot holds the per-asset cohort book, which is what makes it large.
DEFAULT_RING_MAX = 24
DEFAULT_RING_DENSE_MIN = 120        # keep EVERY snapshot inside this age (serves the ~60min fast arm)
DEFAULT_RING_SPARSE_STEP_MIN = 120  # beyond it, ~1 per this many minutes (serves the ~12h slow arm)
DEFAULT_DEADLINE_S = 100          # sweep.FEED_DEADLINE_S — the whole gather's wall clock
# These mirror runtime.yaml's scanner `inputs:` deliberately. Per-signal marginPct/leverage
# outrank the strategy block in the runtime, so `inputs:` is what ships — and a package whose
# module defaults still carried the ANCESTOR's numbers would silently trade as SIGNALS-HUNTER the
# moment that block went missing. Change these and runtime.yaml together.
DEFAULT_MIN_SCORE = 85.0          # the feed's own TRADE floor is 45; SIGNALS-HUNTER uses 65.
                                  # Measured over 14 days: 82/116 observations clear 65, 39 clear
                                  # 85, 33 clear 90 — and 21 sit at exactly 100, so the score
                                  # SATURATES. 85 is the last floor that still discriminates.
DEFAULT_HIGH_SCORE = 93.0
DEFAULT_MARGIN_PCT = 75.0
DEFAULT_HIGH_MARGIN_PCT = 90.0
DEFAULT_LEVERAGE = 10
DEFAULT_TOP_N = 6                 # score.TOP_N — the ranked pool; one slot takes its head
DEFAULT_FAMILY_CAP = 1            # one funding flood must not BE the candidate pool
DEFAULT_TTL_S = 7200
# MUST exceed DEFAULT_HIGH_MARGIN_PCT. SIGNALS-HUNTER's 80 against a 90% tier rejects every signal
# as unfunded on an EMPTY book, forever, while reporting a healthy tick.
DEFAULT_MAX_TOTAL_MARGIN_PCT = 92.0   # of withdrawable, across everything one tick opens

# Detectors the FEED may print but this package must not trade.
#
# `whale_open` says "a proven wallet just opened size". Its freshness rests entirely on the
# third-party `startTime` field, and that field has been observed jumping forward on a position
# whose `szi` and `entryPx` had not moved — 4592s of age reading as 17s. The entry-price
# corroboration added alongside it passes that exact reset, because the position was sitting 0.019%
# from its entry. `check_position_age.py` exists to settle whether the cached path resets and has
# not reported yet.
#
# In the feed that is a sentence a reader can discount. Here it is a 10x entry at three quarters
# of the wallet, with nobody between the detector and the order — and the direction it
# gets wrong is the direction we take — and here that is the whole book, not a fifth of it. Every
# OTHER detector in the library is a statement about a
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


def _stratify_ring(ring, now, ring_max, dense_min, sparse_step_min):
    """Keep a ring that SPANS what the detectors ask for, at a depth the state can hold.

    score.py picks both baselines by wall-clock AGE, never by position: the fast arm wants a
    partner ~DIFF_TARGET_MIN (60min) old, the slow trend arm ~TREND_LOOKBACK_MIN (720min) old and
    refuses outright to call a trend below TREND_MIN_AGE_MIN (360min). What the ring owes them is
    therefore SPAN — and a plain tail-keep (`ring[-24:]`, which is what SIGNALS-HUNTER does) makes
    span a function of the tick interval. At this package's 15-minute clock that tail spans exactly
    6h, so the slow arm does not go dark; it lands ON TREND_MIN_AGE_MIN and is accepted (see the
    note below), and the trend detector reports a 6h baseline as its ~12h arm while every tick
    still looks healthy. Exactly the silent fail-open this tree has shipped before.

    So put the density where each arm actually reads:
      - every snapshot inside `dense_min`, so the 60-minute arm always has a partner of about the
        right age instead of one drifting to 120 as it does on the hourly package;
      - beyond that, at most one per `sparse_step_min` out to score.SNAP_MAX_AGE_MIN (~25h), which
        is all the 12h arm needs — it takes the snapshot CLOSEST to 720min, not an exact one.

    At 15-minute ticks that is ~9 dense + ~12 sparse = ~21 snapshots spanning 25h: FEWER than the
    24 the hourly package carries, over a LONGER span, with both arms better aged. The cap is
    newest-first so that when it binds it drops the oldest, and the result is returned oldest-first
    to preserve the append order the rest of this file assumes.

    Not to be confused with score._prune_ring, which is the FEED's prune and is also count-tailed
    (drop past SNAP_MAX_AGE_MIN, keep the last SNAP_RING_MAX=48). It carries the same coupling with
    a larger constant, so it is not the fix either; per the parity test, strategy-only behaviour
    like this belongs in scan.py and never in the vendored engine.

    Worth being exact about what the inherited tail does at a 15-minute tick, because it is worse
    than going dark: make_slow_lookup skips baselines with `age < TREND_MIN_AGE_MIN` and otherwise
    takes the one CLOSEST to 720min, so a 6h-spanning ring does not refuse — it silently returns a
    ~360min baseline and the trend prints as a 12h read. A wrong number wearing the right name.

    Snapshots with an unparseable `ts` are dropped: both baseline finders already skip them, so
    they are pure state weight.
    """
    dated = []
    for snap in ring or []:
        ts = score._parse_ts(snap.get("ts"))
        if ts is None:
            continue
        dated.append(((now - ts).total_seconds() / 60.0, snap))
    dated.sort(key=lambda x: x[0])              # youngest first
    kept, last_sparse = [], None
    for age, snap in dated:
        if age > score.SNAP_MAX_AGE_MIN:
            break                                # older than the engine would ever read
        if age <= dense_min:
            kept.append(snap)
            continue
        if last_sparse is None or (age - last_sparse) >= sparse_step_min:
            kept.append(snap)
            last_sparse = age
    out = list(reversed(kept[:max(2, ring_max)]))

    # The dense tier is taken FIRST, so a dense window that fills `ring_max` on its own starves the
    # sparse tier and the slow arm dies. That is real: at a 5-MINUTE tick, 120min of dense window is
    # 24 snapshots — the entire cap — and the ring collapses to a 2h span. The shipped 15-minute
    # clock keeps 8 dense of 24, so it has room; anyone who speeds the clock up further or widens
    # ringDenseMinutes loses the trend arm, and must not lose it silently.
    if dated and out:
        had = dated[-1][0]
        got = max((now - score._parse_ts(s["ts"])).total_seconds() / 60.0 for s in out)
        if had >= score.TREND_MIN_AGE_MIN and got < score.TREND_MIN_AGE_MIN:
            print(f"[puffin.scan] WARNING: ringMax={ring_max} truncated the ring to a {got:.0f}min "
                  f"span from {had:.0f}min available, below score.TREND_MIN_AGE_MIN "
                  f"({score.TREND_MIN_AGE_MIN}) — the ~12h trend arm cannot fire and every other "
                  f"detector keeps reporting normally. The dense tier is taken first and is "
                  f"{dense_min:.0f}min wide against this tick interval: raise ringMax or lower "
                  f"ringDenseMinutes.", file=sys.stderr)
    return out


def _wallet(ctx):
    """(free_usd, committed_margin_usd) from ONE read.

    The two numbers aggregate in OPPOSITE directions and getting that backwards is the whole bug
    this function exists to avoid:

      - `withdrawable` sits beside `marginSummary` in each dex section and BOTH sections are views
        of ONE cross-margined wallet, so free is a **max()** — summing double-counts it.
      - each section lists its OWN `assetPositions`, so committed margin **sums** across sections.

    Verified against a live wallet holding three `main` and two `xyz` positions: the per-position
    `marginUsed` summed to $393.19, matching the two sections' `totalMarginUsed` (199.34 + 193.85)
    exactly, while `withdrawable` read identically ($149.91) in both.

    Capital is then `free + committed`, which is deliberately NOT `accountValue`: an isolated xyz
    position posts its margin out of the cross balance, so `accountValue` means different things in
    the two views and reconciling them is guesswork. free+committed needs no such interpretation —
    on that same wallet it came to $543.10 against $550 funded, the difference being fees, funding
    and open losses.

    Returns (None, 0.0) when the read fails, which is not the same as a wallet with nothing in it.
    """
    try:
        d = ctx.senpi_mcp.call_tool("strategy_get_clearinghouse_state",
                                    {"strategy_wallet": ctx.wallet})
    except Exception as exc:  # noqa: BLE001
        print(f"[puffin.scan] clearinghouse read failed: {exc!r}", file=sys.stderr)
        return None, 0.0
    d = d.get("data", d) if isinstance(d, dict) else None
    if not isinstance(d, dict):
        return None, 0.0
    best, used = None, 0.0
    for section in ("main", "xyz"):
        s = d.get(section)
        if not isinstance(s, dict):
            continue
        v = _f(s.get("withdrawable"), None)
        if v is not None:
            best = v if best is None else max(best, v)
        for ap in (s.get("assetPositions") or []):
            pos = ap.get("position") if isinstance(ap, dict) else None
            if isinstance(pos, dict):
                used += abs(_f(pos.get("marginUsed"), 0.0))
    return best, used


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
    dense_min = _f(inputs.get("ringDenseMinutes"), DEFAULT_RING_DENSE_MIN)
    sparse_step_min = _f(inputs.get("ringSparseStepMinutes"), DEFAULT_RING_SPARSE_STEP_MIN)
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
        print(f"[puffin.scan] gather failed: {exc!r} — no tick", file=sys.stderr)
        return []

    cur = rep.get("asset_metrics") or {}
    events = [e for e in (score.normalize_event(e) for e in (rep.get("events") or [])) if e]
    cov = score.coverage(cur)
    if not cur:
        print("[puffin.scan] universe read returned nothing — skipping the tick "
              "(this is a failed read, not an empty market)", file=sys.stderr)
        return []
    if _cohort_is_dark(cov):
        print(f"[puffin.scan] SKIP — the proven-cohort lens went dark "
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
        print(f"[puffin.scan] parked {len(parked)} signal(s) from non-tradeable detectors "
              f"{sorted({s.get('detector') for s in parked})} — see NON_TRADEABLE_DETECTORS",
              file=sys.stderr)
    tradeable = [s for s in tradeable if s.get("detector") not in NON_TRADEABLE_DETECTORS]
    ranked = score.rank(tradeable, "trade_score", min_score, top_n, family_cap)

    # ── 3. emit ──
    # Every signal in one tick is sized off the SAME balance read, so emitting more than the wallet
    # funds does not shrink the later positions — it fails them outright with insufficient margin,
    # and it fails the LOWER-ranked ones, which is a silent quality bias rather than log noise.
    # With one slot that matters less than it does on the five-slot package, but the guard is what
    # makes the ONE slot enforceable independently of `slots`: the counter is seeded with margin
    # already at risk, so an open 90% position puts 90 + 90 over the 92 cap and the tick opens
    # nothing. So emit only what fits, worst-ranked dropped first.
    free, used = _wallet(ctx)
    # The cap is a PORTFOLIO cap, and it was only ever a per-TICK one. `committed_pct` started at 0
    # on every tick and counted only that tick's emits, so an hourly clock was free to commit another
    # `maxTotalMarginPct` on top of a book that was already full — five ticks, 400%. What actually
    # held the line was `slots` and the held-set, not the guard written to do it. Seed the counter
    # with what is already at risk so the cap means what its name says.
    capital = (free + used) if free is not None else None
    already_pct = (100.0 * used / capital) if capital and capital > 0 else 0.0
    out, skipped_dir, committed_pct, unfunded = [], 0, already_pct, 0
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
        # `oneSidedness` is declared `{type: number, required: false}`, and the runtime reads that as
        # "absent is fine, null is not": a key present with None fails the type and the whole SIGNAL
        # is rejected — after a tick that reported itself healthy, so the loss is invisible from here.
        # And `smart_share` is a field on the asset METRICS; not one of score.py's four signal
        # construction sites copies it onto the signal dict. `s.get("smart_share")` was therefore
        # None on 100% of emits, for every detector, and nothing this strategy produced could open.
        # Read it from the metrics row the signal was derived from (`cur` is keyed by the same asset
        # score.py iterated), and OMIT the key when there is no number rather than sending a null.
        m = cur.get(asset)
        one_sided = _f(m.get("smart_share"), None) if isinstance(m, dict) else None
        data = {
            "score": ts,
            "direction": direction,
            "detector": s.get("detector"),
            "credibility": s.get("credibility"),
            "reasons": [f"{s.get('detector')}: {'; '.join(s.get('numbers') or [])}".strip(": ")],
        }
        if one_sided is not None:
            data["oneSidedness"] = one_sided
        out.append({
            "asset": asset,
            "direction": direction,
            "marginPct": want_pct,
            "leverage": leverage,
            "data": data,
        })
        recent[key] = now.timestamp()

    if unfunded:
        # Say this on EVERY tick it happens, not only on a quiet one: a tick that opened four and
        # silently dropped two qualifying signals for want of margin is the case worth seeing.
        print(f"[puffin.scan] {unfunded} qualifying signal(s) not emitted — "
              f"{committed_pct:.0f}% of capital committed against a {max_total_margin:.0f}% cap "
              f"({already_pct:.0f}% of it already at risk in open positions). "
              f"Lowest-ranked dropped first.", file=sys.stderr)

    if not out:
        free_s = "unread" if free is None else f"${free:,.0f}"
        base_s = (baseline or {}).get("ts") or "none (first tick — diff detectors cannot fire yet)"
        print(f"[puffin.scan] WAITING — no emit | assets={cov.get('assets')} "
              f"signals={len(signals)} tradeable={len(tradeable)} ranked={len(ranked)} "
              f"held={sorted(held)} ring={len(ring)} baseline={base_s} "
              f"(minScore {min_score:.0f}, unnamed_direction={skipped_dir}, "
              f"unfunded={unfunded}, free={free_s}, committed={already_pct:.0f}%/"
              f"{max_total_margin:.0f}%)", file=sys.stderr)

    # ── 4. persist the ring ──
    if ctx.state is not None:
        ring.append({"ts": now.isoformat(), "asset_metrics": cur})
        cutoff = now.timestamp() - ttl * 3
        try:
            ctx.state.append({"ring": _stratify_ring(ring, now, ring_max, dense_min, sparse_step_min),
                              "recent": {k: v for k, v in recent.items() if _f(v, 0.0) >= cutoff}})
        except Exception as exc:  # noqa: BLE001
            print(f"[puffin.scan] WARNING: state append failed: {exc!r} — the next tick "
                  f"loses its diff baseline", file=sys.stderr)
    return out
