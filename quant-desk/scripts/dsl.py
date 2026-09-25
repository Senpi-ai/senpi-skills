#!/usr/bin/env python3
"""What senpi's own runtime is doing to a position — read-only, and never an overclaim.

A DSL-managed position already shows as PROTECTED because the desk reads the resting order off the
exchange and finds a real one. What the desk could not say is WHAT that order is: a price with no
context reads as a static stop when it is a floor that ratchets. This module supplies that — and
the two things it must never do are present the ladder as protection already in force, or trust a
backend row it cannot corroborate.

**How the runtime actually works** (verified in senpi-trading-runtime@main, @0xsarvesh #753):

* **Phase 1 is runtime-local.** The runtime posts its own exchange stop with
  `editPosition({stopLoss})` (`monitor/process-one-position.ts:877`), priced at
  `phase1.absoluteFloor` — the strategy's `max_loss_pct` (`engine/exchange-stop.ts:18`). No backend
  ratchet is registered.
* **The backend row appears at the phase-2 handoff.** `addRatchetStop` is gated on
  `next.phase === 2` (`:807`/`:824`). **A position in phase 1 normally has no `ratchet_stop_list`
  row at all.**
* So `currentTierIndex: -1` does **NOT** mean "phase 1". An ACTIVE row with no tier armed may be
  stale — `backendHasLiveSl()` (`:1052`) exists precisely because a row can name an exchange SL
  that is no longer resting, and a ratchet can stay ACTIVE after its stop filled and the strategy
  re-entered. It can also be a direct `ratchet_stop_add` on a raw position, which is profit-lock
  only. **"Stale" is an inference from the runtime source; neither of us has confirmed it in
  telemetry** — which is the point, and why the desk corroborates instead of deciding.

An earlier draft of this module read 7,778 telemetry rows of `ACTIVE / currentTierIndex: -1 /
activeSLOrderId: null` as "phase 1, stop posted at entry". The conclusion it drew (the positions
are protected) was right — the public order book showed full-size reduce-only triggers on all
three spot-checked wallets — but the mechanism was wrong, and a wrong mechanism on this screen is
how you render "tier 2 armed, floor X" for a position the row no longer belongs to.

**So this module renders a tier only when the exchange agrees with the row.** Corroboration is
either the row's `activeSLOrderId` matching an order genuinely resting in the book the desk already
read, or its `tierFloorPrice` sitting where a resting stop actually sits. Uncorroborated, it says
nothing at all — which leaves the protection audit reading exactly as it does without this module.

  tiers: [{triggerRoe: 8, lockRoe: 35}, ...]   currentTierIndex: 0   tierFloorPrice: 3.1134

`lockRoe` is a share of the HIGH-WATER GAIN, not an absolute:
`floor = entry + lock% x (high_water - entry)`. Saying "protected in all tiers" would claim tier
3's 88% floor when 35% is locked — on the worked example, 1.45% of entry at 5x of protection that
does not exist. An agent made exactly that mistake in production once: it reported "Tier 3 locked
17.7%" when only breakeven was locked.

Every read is `.get()`, and any failure leaves the book exactly as it was.
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0

# How close a row's claimed floor must sit to a stop actually resting on the venue before the desk
# will believe the row describes that order. Rounding is 5 significant figures (~0.01-0.02%), so
# 0.5% was two orders of magnitude looser than the thing it was correcting for: @0xsarvesh
# reproduced a stale row sitting 0.45% from an unrelated stop, passing, and having its floor
# rendered — 0.5% of price at 5x is 2.5% of ROE. Tolerance is for rounding, nothing else.
FLOOR_TOL = 0.0005
# Entry is what actually distinguishes a row written for THIS position from one left behind by an
# earlier entry on the same coin — two stops can sit a tick apart for unrelated reasons, two entries
# rarely do. Required only on the weak path: an `activeSLOrderId` that names an order resting right
# now is conclusive on its own, and demanding entry as well would drop the annotation every time a
# reader adds to a position and moves their average.
ENTRY_TOL = 0.001


def _rows(resp, key):
    d = (resp or {}).get("data") or {}
    v = d.get(key)
    return v if isinstance(v, list) else []


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def strategies_for(mcp, addr):
    """Every senpi strategy whose wallet IS this address. Empty for an external wallet."""
    if mcp is None or not addr:
        return []
    try:
        # filtered server-side rather than fetching every strategy the user has ever had
        resp = mcp.mcp_call("strategy_list", strategyAddresses=[addr], timeout=20)
    except Exception:  # noqa: BLE001
        return []
    a = addr.lower()
    return [s for s in _rows(resp, "strategies")
            if str(s.get("strategyWalletAddress") or "").lower() == a]


def _near(a, b, tol):
    return bool(a and b and abs(a - b) <= abs(b) * tol)


def corroborated(row, p):
    """Does the exchange agree with this backend row, for THIS position?

    Returns False when there is no resting stop at all, which is the important one: a position the
    table calls UNPROTECTED must never carry a sentence claiming a floor.

    Two paths, and they are not equally strong:

    * **The row names an order resting right now.** Conclusive — nothing else is needed. Note the
      ids are compared AS STRINGS: `activeSLOrderId` comes off the wire as a string
      (`"413893122747"`) while Hyperliquid's `oid` is an int, so a direct `in` test is always False
      and every row silently fell through to the weak path below. It passed its test only because
      the fixture I wrote used an int on both sides. (@0xsarvesh, #753, second pass — reproduced
      against the wire data rather than the source.)
    * **The floor sits on a resting stop.** Weak: two stops can land a tick apart for unrelated
      reasons. So this path also requires the row's ENTRY to match the live position's, which is
      what actually rules out a row left behind by an earlier entry on the same coin.

    Either way the side must match when the row states one — a row from the opposite direction
    renders a floor on the wrong side of the mark.
    """
    if not p.get("stop_covered_share"):
        return False
    side = str(row.get("direction") or "").upper()
    if side in ("LONG", "SHORT") and side != p["side"]:
        return False
    oid = row.get("activeSLOrderId")
    if oid is not None and str(oid) in {str(o) for o in (p.get("stop_oids") or [])}:
        return True
    if not _near(_f(row.get("entryPrice")), _f(p.get("entry")), ENTRY_TOL):
        return False
    return _near(_f(row.get("tierFloorPrice")), _f(p.get("stop_px")), FLOOR_TOL)


def attach(mcp, addr, book, meta=None):
    """Annotate each open position with the DSL state senpi holds for it, where the exchange agrees.

    Adds `position["dsl"]` only where there is a corroborated, armed tier. Silent no-op for an
    external wallet, a missing token, or any failure — the protection audit must read exactly as it
    does today rather than lose a section because a side read went down.
    """
    strats = strategies_for(mcp, addr)
    if not strats:
        return 0
    if meta is not None:
        meta.setdefault("sources", {})["dsl"] = (
            f"senpi runtime — {len(strats)} strateg{'y' if len(strats) == 1 else 'ies'} on this wallet")
    # ONE call per strategy: `asset` is optional on ratchet_stop_list, so asking per position made
    # it N calls for one answer. (@0xsarvesh, #753.)
    by_coin, skipped = {}, 0
    for s in strats:
        sid = s.get("id")
        if not sid:
            continue
        try:
            resp = mcp.mcp_call("ratchet_stop_list", strategyId=sid,
                                strategy_wallet_address=addr, status="ACTIVE", limit=500, timeout=20)
        except Exception:  # noqa: BLE001
            continue
        for r in _rows(resp, "positions"):
            if r.get("status") != "ACTIVE" or not r.get("asset"):
                continue
            # A HIP-3 row may carry `asset: "GOLD"` with `dex: "xyz"` where the book's coin is
            # always "xyz:GOLD". Index both spellings rather than assume which one arrives.
            # (@0xsarvesh, #753 — unconfirmed from the captured data, so handle both.)
            by_coin.setdefault(r["asset"], (r, s))
            dex = r.get("dex")
            if dex and ":" not in str(r["asset"]):
                by_coin.setdefault(f"{dex}:{r['asset']}", (r, s))
    found = 0
    for p in book.get("positions") or []:
        hit = by_coin.get(p["coin"])
        if not hit:
            continue
        r, s = hit
        idx = r.get("currentTierIndex")
        idx = -1 if idx is None else int(idx)
        tiers = (((r.get("dslConfig") or {}).get("tiered") or {}).get("tiers")) or []
        # An unarmed row describes no protection in force, and we cannot tell a genuinely unarmed
        # ratchet from a stale one. Either way there is nothing true to say, so say nothing.
        if idx < 0 or idx >= len(tiers):
            continue
        if not corroborated(r, p):
            skipped += 1
            continue
        p["dsl"] = dict(
            strategy=s.get("strategyName"), tiers=tiers, tier_index=idx, n_tiers=len(tiers),
            floor_px=r.get("tierFloorPrice"), high_water_px=r.get("highWaterPrice"),
            high_water_roe=r.get("highWaterRoe"), armed=tiers[idx],
            stop_px=p.get("stop_px"),
            next_tier=tiers[idx + 1] if idx + 1 < len(tiers) else None,
        )
        found += 1
    if skipped and meta is not None:
        meta.setdefault("warnings", []).append(
            f"{skipped} senpi ratchet row(s) did not match an order resting on the venue — not shown")
    return found


def line(p):
    """One sentence for a DSL-managed position. States what is LOCKED, then what is merely ahead.

    The distinction is the whole point: an armed tier is protection, an unarmed one is a rule that
    has not fired. Never collapse them into "protected in all tiers"."""
    d = p.get("dsl")
    if not d:
        return None
    a, nxt = d.get("armed") or {}, d.get("next_tier")
    # The price quoted is the one RESTING ON THE VENUE, not the row's `tierFloorPrice`. I claimed
    # the sentence was self-verifying — "we only ever quote a price an order really rests at" — and
    # it was not: it quoted the row. Corroboration proves the two agree; printing the venue's number
    # makes that true by construction rather than by argument. (@0xsarvesh, #753, second pass.)
    floor = d.get("stop_px") if d.get("stop_px") is not None else d.get("floor_px")
    # .4g turns 3.1134 into 3.113. This is a price the reader may place by hand —
    # keep the venue's own precision.
    floor_s = f"{float(floor):,.6g}" if floor is not None else "its floor"
    if nxt:
        ahead = (f" Next: +{float(nxt.get('triggerRoe') or 0):.0f}% ROE locks "
                 f"{float(nxt.get('lockRoe') or 0):.0f}%.")
    else:
        ahead = " That is the last tier."
    # "rises" is only true of a long. A short's floor ratchets DOWNWARD toward the entry — same
    # mechanism, opposite direction, and a short reader told their floor "only ever rises" is being
    # told their stop moves away from them. (@0xsarvesh, #753.)
    return (f"**DSL tier {d['tier_index'] + 1} of {d['n_tiers']}** armed — floor {floor_s}, "
            f"locking {float(a.get('lockRoe') or 0):.0f}% of the gain from entry.{ahead}"
            " The floor only ever tightens.")
