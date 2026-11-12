"""MANATEE — Large-cap Mean-Reversion Inventory. The single supervised scanner.

Once per closed DAILY bar, for an identity basket of large caps: a name whose daily close is at
least devPct below its one-week mean is a LONG, one at least devPct above it is a SHORT. No bias:
the sign of the stretch is the direction. A held name gets one more clip (an add) when the next
daily close is a further addStepPct against the position AND the position is under water, up to
maxAddsPerAsset; the runtime merges the add into the open position and the DSL re-anchors on the
new average entry (it archives the old state as position_increased and tracks the merged position
from scratch), which is the modelled vault's "harvest over average cost". NEVER closes: the DSL
owns every exit, every hour of the day. Read-only, single-pass. marginPct is a PERCENT in (0,100]
of withdrawable.

THE DAILY CLOCK: the scanner ticks every few minutes but acts on a name at most once per closed
daily bar (state `acted[name] = that bar's open time`), so the first tick after 00:00 UTC does the
day's work and the rest of the day is quiet. The in-progress bar (the MCP's last row) is dropped.
One exception: `acted` is marked on EMIT, so a signal the action layer never opened would spend
the bar on a position that does not exist. A name never once seen on the book gets exactly ONE
more attempt inside the same bar; a name that filled and was harvested does not.

FREE-MARGIN GATE: every signal in one tick is sized off the same balance read, so the scanner reads
free margin (withdrawable) once, prices each clip as the runtime will ((marginPct/100) x withdrawable
x leverage), skips the tick when a clip is under the venue minimum notional, and stops emitting when
the clips it has already emitted would exhaust free margin.
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import sys
import time

import scoring


def _read(ctx, tool, args, label):
    try:
        raw = ctx.senpi_mcp.call_tool(tool, args)
    except Exception as exc:  # noqa: BLE001 — degrade, never crash the tick
        print(f"[manatee.scan] {label} read failed: {exc!r}", file=sys.stderr)
        return None
    if not raw:
        return None
    return raw.get("data", raw) if isinstance(raw, dict) else raw


def _bare(name):
    return str(name).split(":", 1)[-1].upper()


def _dex_for(name):
    """XYZ (HIP-3) assets need dex='xyz' on every market read; main-board names send no dex."""
    return "xyz" if str(name).lower().startswith("xyz:") else ""


def _account(ctx):
    """(held, free_margin, account_value), or None when the state is unreadable or contradicts
    itself (margin in use but no positions) — then the tick stands down.

    held = bare-upper name -> (side, average entry, margin % of ACCOUNT VALUE). That last number is
    read from the position's own marginUsed every tick rather than tallied from what this scanner
    emitted. A tally cannot see a signal that never filled, nor one that filled twice, and
    FEE_OPTIMIZED_LIMIT does occasionally double-fill — on a book that adds into losers, the
    per-name cap is the only thing bounding that, so it has to look at the book.

    The 'main' and 'xyz' sections are two views of ONE cross-margined wallet: account value and
    withdrawable are taken once via max(), never summed.
    """
    data = _read(ctx, "strategy_get_clearinghouse_state", {"strategy_wallet": ctx.wallet}, "clearinghouse")
    if not isinstance(data, dict):
        return None
    held, account_value, used, withdrawable = {}, 0.0, 0.0, 0.0
    for view in [v for v in data.values() if isinstance(v, dict)] + [data]:
        ms = view.get("marginSummary") if isinstance(view.get("marginSummary"), dict) else {}
        account_value = max(account_value, scoring._f(ms.get("accountValue")))
        used = max(used, scoring._f(ms.get("totalMarginUsed")))
        withdrawable = max(withdrawable, scoring._f(view.get("withdrawable")))
        for ap in (view.get("assetPositions") or []):
            pos = ap.get("position", ap) if isinstance(ap, dict) else {}
            coin = pos.get("coin") or pos.get("asset")
            szi = scoring._f(pos.get("szi"), 0.0)
            if coin and szi != 0.0:
                held[_bare(coin)] = ("LONG" if szi > 0 else "SHORT",
                                     scoring._f(pos.get("entryPx"), 0.0),
                                     scoring._f(pos.get("marginUsed"), 0.0))
    if used > 1.0 and not held:
        print("[manatee.scan] margin in use but no positions — corrupt read, standing down", file=sys.stderr)
        return None
    free = withdrawable if withdrawable > 0 else max(0.0, account_value - used)
    if account_value > 0:                      # marginUsed -> % of account value
        held = {k: (sd, px, m / account_value * 100.0) for k, (sd, px, m) in held.items()}
    return held, free, account_value


def _instruments(ctx):
    """BOTH venue boards -> {NAME_UPPER: {venue_name, oi_usd, delisted}}, or None on FAILURE.

    None and {} are opposite facts once the universe is DERIVED from this read: "unreadable" must
    trade nothing, "the venue lists nothing" would too, but collapsing both to {} would silently
    trade an unvetted board. A HALF-read board is the same hazard one notch up: the two halves
    of this universe do NOT behave alike (see derive_universe), so losing one would quietly
    re-aim the strategy at whichever half answered.

    ONE read serves both boards. Verified 2026-10-16: the no-dex call returns 370 instruments —
    234 bare perps plus all 136 xyz names, and that xyz subset is byte-identical to what
    dex="xyz" returns. `dex` is a FILTER, not a switch, so reading both boards separately is a
    wasted call per tick and an extra way to fail. The xyz board is asked for by name only if
    the merged read comes back without it: that half of the universe is the half that carries
    this strategy, and losing it in silence would ship a crypto-only book under a cross-asset
    card. If the board is genuinely gone the named read returns [] and the universe simply
    shrinks; if it is unreadable we are back to None, for the reason above.

    `openInterest` and `markPx` sit under `context`; oi_usd is their product.
    """
    data = _read(ctx, "market_list_instruments", {}, "market_list_instruments")
    rows = data.get("instruments") if isinstance(data, dict) else data
    if not isinstance(rows, list):
        return None
    out = _board(rows)
    if not any(k.startswith("XYZ:") for k in out):
        data = _read(ctx, "market_list_instruments", {"dex": "xyz"}, "market_list_instruments(xyz)")
        rows = data.get("instruments") if isinstance(data, dict) else data
        if not isinstance(rows, list):
            return None
        out.update(_board(rows))
    return out


def _board(rows):
    """One instrument list -> {NAME_UPPER: {...}}. Pure; the caller owns the read."""
    out = {}
    for r in rows:
        if not isinstance(r, dict) or not r.get("name"):
            continue
        c = r.get("context") if isinstance(r.get("context"), dict) else {}
        out[str(r["name"]).upper()] = {
            # the venue's OWN spelling: kSHIB/kBONK do not survive upper(), and `asset` is what the
            # order carries, so a derived universe returning the KEY would emit KSHIB and be refused
            "venue_name": str(r["name"]),
            "oi_usd": scoring._f(c.get("openInterest")) * scoring._f(c.get("markPx")),
            "delisted": bool(r.get("is_delisted", False)),
        }
    return out


def derive_universe(instruments, oi_floor, max_names):
    """The liquid board — BOTH of them — read off the venue instead of hand-listed: every listed
    name above an open-interest floor, ranked by OI and capped. Pure — the caller owns the read.

    WHY THIS REPLACED THE 19-NAME LIST. The list was the Growi HF vault's, and the vault trades it
    because it runs $13.8M and needs the depth — a constraint that is theirs, not a user's at $825.
    Measured venue-wide, the reversion this strategy harvests is NOT a large-cap property: the
    >=$200M-OI bucket had the LOWEST harvest rate, the highest stop rate and the only negative
    fav/adverse skew. The hand list was the single worst universe available for this rule, and it
    had rotted in the usual way: at the time of writing only 9 of the 19 still cleared the floor.

    WHY xyz IS IN. Replaying this exact rule over 2026-06-19..2026-10-16 (every signal day in the
    derived top-24, one clip, 45-day horizon, exits filled at the DSL floor) splits cleanly by
    board, and the split is the whole strategy:

        perp   443 signals   +0.159% gross   +0.02% net    15 of 443 stopped at -30%
        xyz    371 signals   +1.116% gross   +1.005% net    0 of 371 stopped at -30%

    Perp-only — what this template shipped at 1.0.0 — is a LOSS once fees are paid, and at the
    3.0 harvest it was -0.153% net per clip. The equity board is not a diversifier here, it is
    the edge; the perp half is carried, and it is carried by a margin of 15 stops. Two caveats
    belong next to those numbers: the xyz half booked ZERO stops in a four-month window (14 of
    371 were still open and under water at 45 days), and signal days on one name overlap, so the
    effective sample is well under 814. Neither is a reason to exclude the board that pays.

    A bare name may in principle be listed on both boards. The scanner keys held positions and
    per-bar state by the bare name, so only the deeper book of a colliding pair is ever served —
    holding both halves of one ticker under one key would merge two positions into one.
    """
    best = {}
    for name, info in (instruments or {}).items():
        if info.get("delisted"):
            continue
        oi = scoring._f(info.get("oi_usd"))
        if oi_floor > 0 and oi < oi_floor:
            continue
        venue = info.get("venue_name") or name
        bare = _bare(venue)
        if bare not in best or oi > best[bare][1]:
            best[bare] = (venue, oi)
    out = sorted(best.values(), key=lambda kv: -kv[1])
    return [n for n, _ in (out[:max_names] if max_names > 0 else out)]


def _closed_candles(ctx, name, interval):
    """The CLOSED bars for `name`, oldest first: the MCP's last row is the bar in progress and is dropped."""
    md = _read(ctx, "market_get_asset_data", {
        "asset": name, "candle_intervals": [interval],
        "include_funding": False, "include_order_book": False, "dex": _dex_for(name),
    }, f"market_get_asset_data({name})")
    if not isinstance(md, dict):
        return None
    candles = (md.get("candles") or {}).get(interval)
    if not isinstance(candles, list) or len(candles) < 2:
        return None
    return candles[:-1]


def _bar_key(candle, fallback_day):
    """Identifies the closed bar a decision was made on: its open time when the row carries one,
    else the UTC day of the tick."""
    t = candle.get("t") if isinstance(candle, dict) else None
    return str(t) if t not in (None, "") else fallback_day


def scan(inputs, ctx):
    now = time.time()
    today = time.strftime("%Y-%m-%d", time.gmtime(now))
    max_slots = int(scoring._f(inputs.get("maxSlots"), 19))
    interval = str(inputs.get("candleInterval") or "1d")
    min_history = int(scoring._f(inputs.get("minHistoryBars"), 60))
    max_adds = int(scoring._f(inputs.get("maxAddsPerAsset"), 1))
    add_step = scoring._f(inputs.get("addStepPct"), 3.0) / 100.0
    cap_mgn = scoring._f(inputs.get("maxMarginPctPerAsset"), 3.0)
    min_notional = scoring._f(inputs.get("venueMinNotionalUsd"), 12.0)
    headroom = scoring._f(inputs.get("freeMarginHeadroom"), 0.9)

    st = (ctx.state.last() or {}) if ctx.state else {}
    acted = dict(st.get("acted", {}) or {})       # bare -> key of the closed bar already acted on
    entries = dict(st.get("entries", {}) or {})   # bare -> {px, adds, side, t, filled?}
    retried = dict(st.get("retried", {}) or {})   # bare -> bar key whose unfilled emit was retried

    acct = _account(ctx)
    if acct is None:
        print("[manatee.scan] clearinghouse unreadable — no opens this tick", file=sys.stderr)
        return []
    held, free, account_value = acct
    instruments = _instruments(ctx)
    if instruments is None:
        print("[manatee.scan] instrument list unreadable — the universe is derived from it, so "
              "no opens this tick", file=sys.stderr)
        return []
    universe = derive_universe(instruments,
                               scoring._f(inputs.get("oiFloorUsd"), 50_000_000.0),
                               int(scoring._f(inputs.get("maxNames"), 24)))
    # a name already held stays in scope even if its OI slipped under the floor: the DSL owns that
    # exit, and dropping it here would only stop it being added to. It re-enters under the VENUE's
    # spelling, not the bare name the clearinghouse reports: a held xyz name re-added as "NVDA"
    # would be read off the main board, find no candles, and be silently dropped every tick.
    spelling = {}
    for info in instruments.values():
        venue = info.get("venue_name") or ""
        bare = _bare(venue)
        if venue and (bare not in spelling
                      or scoring._f(info.get("oi_usd")) > spelling[bare][1]):
            spelling[bare] = (venue, scoring._f(info.get("oi_usd")))
    universe += [spelling.get(h, (h, 0.0))[0] for h in held if h not in {_bare(n) for n in universe}]
    if not universe:
        print("[manatee.scan] no instrument clears the OI floor — nothing to trade", file=sys.stderr)
    # `acted` is marked when a signal is EMITTED, not when it fills. If the action layer never opened
    # the position (clearinghouse unreadable, order refused), the bar would otherwise be spent on a
    # position that does not exist — and this scanner judges a name once per CLOSED DAILY bar, so a
    # single failed open costs the whole day. Un-mark it for ONE more attempt, but only for a name
    # never once observed on the book: a name that actually traded and was harvested must stay spent,
    # because re-entering inside the same bar is the 4h-sampled behaviour the replay showed loses
    # money after fees. The retry key is held OUTSIDE `entries` because PASS 2 overwrites that record
    # on re-emit, which would wipe an in-record counter and retry the same name forever.
    for bare in list(entries):
        rec = entries[bare]
        if bare in held:
            rec["filled"] = True              # observed on the book at least once
            continue
        if rec.get("filled") or int(rec.get("adds", 0)) > 0:
            del entries[bare]                 # it traded: this bar stays spent
            continue
        bar = acted.get(bare)
        if bar is not None and retried.get(bare) != bar:
            retried[bare] = bar               # exactly one retry per name per closed bar
            acted.pop(bare, None)
        del entries[bare]

    # one clip, expressed against the SAME base as the cap: marginPct is a share of withdrawable,
    # the cap is a share of account value, and withdrawable shrinks as the book fills.
    def clip_pct_of_account(mgn):
        return mgn * (free / account_value) if account_value > 0 else mgn

    # ── PASS 1 · judge every name. No sizing decision here, so the order of `universe` cannot
    #    decide who gets funded. ──
    cands = []
    for name in universe:
        bare = _bare(name)
        is_add = bare in held
        rec = entries.get(bare) or {}
        if is_add:
            _side, _avg, live_mgn = held[bare]
            next_clip = clip_pct_of_account(scoring.sizing_for(inputs, add=True)[1])
            if int(rec.get("adds", 0)) >= max_adds or live_mgn + next_clip > cap_mgn:
                continue
        candles = _closed_candles(ctx, name, interval)
        if not candles:
            continue
        if len(candles) < min_history:
            continue      # too freshly listed to judge: the venue-wide measurement that set this
                          # floor excluded late listings, and they underperformed by ~17%
        key = _bar_key(candles[-1], today)
        if acted.get(bare) == key:
            continue                      # this closed bar has been acted on (or judged) already
        th = scoring.build_thesis(name, candles, inputs)
        if th is None:
            acted[bare] = key             # judged: nothing to do on this bar
            continue
        close = scoring._close(candles[-1])
        if is_add:
            side, avg_px, _live = held[bare]
            if th["direction"] != side:
                acted[bare] = key
                continue              # the signal flipped against an open position: the DSL owns that exit
            last_px = scoring._f(rec.get("px"), 0.0) or avg_px
            # scale only while the move deepens against the position AND it is still under water,
            # so an add never lands on a locked profit
            deeper = close <= last_px * (1.0 - add_step) if side == "LONG" else close >= last_px * (1.0 + add_step)
            under_water = (close < avg_px) if side == "LONG" else (close > avg_px)
            if not (last_px > 0 and deeper and (avg_px <= 0 or under_water)):
                acted[bare] = key
                continue
        cands.append((abs(scoring._f(th.get("dev_pct"))), name, bare, key, th, close, is_add, rec))

    # ── PASS 2 · the DEEPEST stretch gets the margin. Serving `universe` in order meant the names
    #    typed first were funded every tight day and the tail never was — a fixed bias, not a
    #    tie-break. A name skipped here is NOT marked acted, so it is retried on the next tick. ──
    cands.sort(key=lambda c: -c[0])
    budget = free * headroom                       # USD of margin this tick may commit
    slots = max_slots - len(held)
    out = []
    for _depth, name, bare, key, th, close, is_add, rec in cands:
        if not is_add and slots <= 0:
            continue
        lev, mgn = scoring.sizing_for(inputs, add=is_add)
        # ── free-margin gate: price the clip as the runtime will, off the one balance read ──
        margin_usd = free * mgn / 100.0
        if margin_usd * lev < min_notional:
            print(f"[manatee.scan] clip {mgn}% of ${free:.0f} free margin is under the ${min_notional:.0f} venue minimum — no opens this tick", file=sys.stderr)
            break
        if margin_usd > budget:
            print(f"[manatee.scan] free margin exhausted after {len(out)} signal(s) — the rest wait for the next tick", file=sys.stderr)
            break
        budget -= margin_usd
        adds = int(rec.get("adds", 0)) + 1 if is_add else 0
        if not is_add:
            slots -= 1
        acted[bare] = key
        entries[bare] = {"px": close, "adds": adds, "side": th["direction"], "t": now}
        out.append({
            "asset": name, "direction": th["direction"], "marginPct": mgn, "leverage": lev,
            "data": scoring.signal_data(th, lev, "add" if is_add else "entry", adds),
        })

    if ctx.state is not None:
        try:
            ctx.state.append({"acted": acted, "entries": entries, "retried": retried})
        except Exception as exc:  # noqa: BLE001
            print(f"[manatee.scan] state append failed: {exc!r}", file=sys.stderr)
    return out
