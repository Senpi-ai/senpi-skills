#!/usr/bin/env python3
"""senpi-signals ranker — dual-lens (trade + social) stateful diff engine.

ONE sweep → TWO ranked feeds from the same detected signals:
  - trade  : Senpi USERS building ideas — actionable EDGE, price CONFIRMATION, credible enough to
             act on. A static funding extreme scores LOW here (carry, not a directional edge); a
             winning smart-money divergence scores HIGH.
  - social : team AUTOMATION / market-news content — surprising, non-obvious, and FRESH (anti-repeat
             so a cron doesn't re-post the same six), credibility-gated. Static extremes are fine here.

Core principle: rank credible CHANGE and actionable EDGE — and never repeat yourself.

Input JSON: { "asset_metrics": { "<asset>": {oi, price, price_change_pct, smart_share, smart_dir,
              crowd_dir, funding_pctile, funding_annualized_pct, notional_vol, dex, oi_side} },
              "events": [ pre-formed signals (whale_move / momentum_event / cross_asset_laggard) ] }

State is a RING of snapshots: the change-detectors diff `cur` against the snapshot ~DIFF_TARGET_MIN
old (not merely the last run), so a 3-minute re-run still sees a meaningful delta. `surfaced` records
when each (asset,detector) last appeared, for the social freshness penalty. Stdlib only.

Thresholds mirror references/detectors.md — change them in BOTH places.
"""
import argparse
import datetime
import json
import os
import sys

try:
    import fcntl  # POSIX advisory lock for the shared state file (the agents are Linux)
except ImportError:  # pragma: no cover — non-POSIX fallback is last-writer-wins
    fcntl = None

# ── detector thresholds (keep in sync with references/detectors.md) ──
OI_SURGE_PCT = 0.10
PRICE_FLAT = 0.01
SMART_SHARE_MIN = 25.0
SMART_JUMP_PP = 12.0
FUNDING_PCTILE = 95.0
WHALE_MIN_USD = 1_000_000

# ── credibility: a MULTIPLIER on the whole score, not a 10% additive term ──
FULL_CRED_VOL = 25_000_000    # notional vol at/above which liquidity is a non-issue (mult 1.0)
CRED_FLOOR_VOL = 1_000_000    # below this a market is too thin to trust the read at all → dropped
CRED_MIN_MULT = 0.45          # multiplier at the drop floor, ramping to 1.0 by FULL_CRED_VOL
TRADE_CRED_FLOOR = 5_000_000  # you can't trade a book this thin → excluded from the TRADE feed only

# ── cadence / freshness ──
DIFF_TARGET_MIN = 60          # diff the change-detectors against the snapshot ~this old (fix 3-min noise)
SNAP_MAX_AGE_MIN = 360        # prune snapshots older than this from the ring
SNAP_RING_MAX = 48            # cap the ring length
FRESH_WINDOW_MIN = 45         # an (asset,detector) shown within this window is penalized, decaying to 1.0
FRESH_MIN_MULT = 0.30         # freshness multiplier the instant after it was surfaced

# ── output ──
MIN_SOCIAL = 30.0   # content lens is inclusive-but-flagged (a thin curiosity can be a tweet)…
MIN_TRADE = 45.0    # …the trade lens is strict (you can't act on a thin book)
TOP_N = 6
FAMILY_CAP = 2               # max signals per detector FAMILY in a single feed (kills the funding flood)

# how invisible-on-a-chart each detector is (the social "moat" weight)
NON_OBVIOUS = {
    "oi_surge": 1.0, "funding_flip": 1.0, "sm_divergence": 1.0, "whale_move": 1.0,
    "funding_extreme": 0.9, "sm_conviction": 0.85, "cross_asset_laggard": 0.8,
    "momentum_event": 0.6, "regime_shift": 0.6,
}
# how tradeable each detector is (a clear, actionable directional edge)
EDGE = {
    "sm_divergence": 1.0, "sm_conviction": 0.9, "whale_move": 0.85, "oi_surge": 0.75,
    "cross_asset_laggard": 0.75, "funding_flip": 0.7, "momentum_event": 0.6, "regime_shift": 0.6,
    "funding_extreme": 0.35,  # a static extreme is carry, not a directional edge → low trade score
}
# collapse detectors to families for the per-feed diversity cap
FAMILY = {
    "funding_flip": "funding", "funding_extreme": "funding",
    "sm_divergence": "smart_money", "sm_conviction": "smart_money",
    "oi_surge": "oi", "whale_move": "whale", "cross_asset_laggard": "cross_asset",
    "momentum_event": "momentum", "regime_shift": "regime",
}
# detectors that fire from a CHANGE vs the prior snapshot (vs a static level)
CHANGE_DETECTORS = {"oi_surge", "sm_conviction", "funding_flip", "whale_move",
                    "cross_asset_laggard", "momentum_event", "regime_shift"}


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _sign(x):
    return 0 if x is None or x == 0 else (1 if x > 0 else -1)


def _parse_ts(s):
    try:
        dt = datetime.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except Exception:
        return None
    return dt.replace(tzinfo=datetime.timezone.utc) if dt.tzinfo is None else dt


def credibility(vol):
    """Liquidity → a 0..1 credibility MULTIPLIER on the whole score. Unknown/0 vol → neutral 0.8
    (not proof of thinness). Below CRED_FLOOR_VOL the signal is dropped upstream."""
    v = _num(vol)
    if v is None or v <= 0:
        return 0.8
    if v >= FULL_CRED_VOL:
        return 1.0
    if v <= CRED_FLOOR_VOL:
        return CRED_MIN_MULT
    frac = (v - CRED_FLOOR_VOL) / (FULL_CRED_VOL - CRED_FLOOR_VOL)
    return round(CRED_MIN_MULT + frac * (1.0 - CRED_MIN_MULT), 3)


def freshness(asset, detector, surfaced, now):
    """1.0 if this (asset,detector) wasn't surfaced recently; decays toward FRESH_MIN_MULT the more
    recently it was. Keeps a continuous cron from re-posting the same six every run. Social-only."""
    last = _parse_ts((surfaced or {}).get(f"{asset}|{detector}"))
    if last is None or now is None:
        return 1.0
    age_min = (now - last).total_seconds() / 60.0
    if age_min < 0 or age_min >= FRESH_WINDOW_MIN:
        return 1.0
    return round(FRESH_MIN_MULT + (age_min / FRESH_WINDOW_MIN) * (1.0 - FRESH_MIN_MULT), 3)


def confirmation(s):
    """0..1 — is price CONFIRMING the signal's direction (the smart side being proven right)?
    long + price up / short + price down = confirmed (a working setup); opposite = early/contrarian
    (weaker as a trade); unknown/non-directional = neutral 0.5. ~3% move = full confirmation."""
    d = s.get("direction")
    pc = _num(s.get("price_change_pct"))
    if not d or pc is None:
        return 0.5
    aligned = (d == "long" and pc > 0) or (d == "short" and pc < 0)
    strength = min(1.0, abs(pc) / 3.0)
    return round(0.5 + (0.5 * strength if aligned else -0.5 * strength), 3)


def social_score(s, cred, fresh):
    """Content lens: surprising · non-obvious · a story · fresh. cred + freshness are multipliers."""
    no = NON_OBVIOUS.get(s["detector"], 0.6)
    mag = max(0.0, min(1.0, _num(s.get("magnitude")) or 0.0))
    conf = 1.0 if s.get("conflict") else 0.0          # a divergence is a story
    concr = 1.0 if s.get("concrete_entity") else 0.3  # a named whale is a great story
    change = 1.0 if s.get("is_change") else 0.0
    base = 0.34 * no + 0.22 * mag + 0.20 * conf + 0.14 * change + 0.10 * concr
    return round(100 * base * cred * fresh, 1)


def trade_score(s, cred):
    """Trade lens: directional EDGE · price CONFIRMATION · CHANGE · a divergence · size. cred multiplies.
    NOT freshness-gated — a standing edge is still an edge even if it showed last run."""
    edge = EDGE.get(s["detector"], 0.5)
    cfm = confirmation(s)
    mag = max(0.0, min(1.0, _num(s.get("magnitude")) or 0.0))
    conf = 1.0 if s.get("conflict") else 0.0
    change = 1.0 if s.get("is_change") else 0.0
    base = 0.30 * edge + 0.22 * cfm + 0.18 * change + 0.16 * conf + 0.14 * mag
    return round(100 * base * cred, 1)


def detect_from_metrics(cur, prior):
    """Fire the diff/threshold detectors from current asset_metrics vs the ~1h-old baseline snapshot."""
    out = []
    for asset, m in (cur or {}).items():
        if not isinstance(m, dict):
            continue
        p = (prior or {}).get(asset, {}) if isinstance(prior, dict) else {}
        dex = m.get("dex", "")
        vol = _num(m.get("notional_vol")) or _num(m.get("day_notional_volume"))
        pcp = _num(m.get("price_change_pct"))
        if pcp is None:
            pcp = _num(m.get("token_price_change_pct_4h"))

        def sig(detector, direction, magnitude, numbers, conflict=False, flip=False, is_change=None):
            out.append({
                "asset": asset, "dex": dex, "detector": detector, "direction": direction,
                "numbers": numbers, "notional_vol": vol, "concrete_entity": None,
                "price_change_pct": pcp, "magnitude": max(0.0, min(1.0, magnitude)),
                "conflict": conflict, "flip": flip,
                "is_change": (detector in CHANGE_DETECTORS if is_change is None else is_change),
            })

        # oi_surge (change)
        oi, poi = _num(m.get("oi")), _num(p.get("oi"))
        if oi is not None and poi:
            pct = (oi - poi) / poi
            if pct >= OI_SURGE_PCT:
                price, pprice = _num(m.get("price")), _num(p.get("price"))
                flat = (price is not None and pprice not in (None, 0)
                        and abs((price - pprice) / pprice) < PRICE_FLAT)
                nums = [f"OI +{pct * 100:.0f}% vs baseline"]
                if flat:
                    nums.append("price ~flat")
                sig("oi_surge", m.get("oi_side"), min(1.0, pct * 2), nums, conflict=flat)

        # smart-money divergence (state; a fresh flip counts as change)
        sd, cd, share = m.get("smart_dir"), m.get("crowd_dir"), _num(m.get("smart_share"))
        if sd and cd and sd != cd and share is not None and share >= SMART_SHARE_MIN:
            flip = bool(p.get("smart_dir")) and p.get("smart_dir") != sd
            sig("sm_divergence", sd, share / 100.0,
                [f"smart money {str(sd).upper()} ({share:.0f}% of top-trader PnL)",
                 f"crowd {str(cd).upper()}"],
                conflict=True, flip=flip, is_change=flip)

        # smart-money conviction jump (change) — fires on a move in EITHER direction (piling in OR unwinding)
        pshare = _num(p.get("smart_share"))
        if share is not None and pshare is not None and abs(share - pshare) >= SMART_JUMP_PP:
            delta = share - pshare
            flow = "piling in" if delta > 0 else "unwinding"
            side = (str(sd).upper() + " ") if sd else ""
            sig("sm_conviction", sd, share / 100.0,
                [f"top traders {flow} {side}".rstrip()
                 + f" — concentration {'+' if delta > 0 else '−'}{abs(delta):.0f}pp to {share:.0f}%"])

        # funding: split a FLIP (change → tradeable regime shift) from a static EXTREME (state → social)
        fp = _num(m.get("funding_pctile"))
        fa, pfa = _num(m.get("funding_annualized_pct")), _num(p.get("funding_annualized_pct"))
        flipped = (fa is not None and pfa is not None
                   and _sign(fa) != _sign(pfa) and _sign(pfa) != 0)
        if flipped:
            sig("funding_flip", None, 1.0,
                [f"funding flipped to {fa:+.0f}%/yr (was {pfa:+.0f})"], flip=True, is_change=True)
        elif fp is not None and fp >= FUNDING_PCTILE:
            nums = [f"funding {fp:.0f}th pctile"]
            if fa is not None:
                nums.append(f"{fa:+.0f}%/yr")
            sig("funding_extreme", None, fp / 100.0, nums, is_change=False)
    return out


def normalize_event(e):
    if not isinstance(e, dict) or not e.get("asset") or not e.get("detector"):
        return None
    det = e["detector"]
    if det == "whale_move":
        # MOVES, not holdings. A big position held from an old entry with no recent change is NOT a
        # signal — a whale sitting on $78M from a months-old entry is holdings. Require a recent size
        # change (opened/added/flipped) or a large 4h PnL swing; magnitude from the CHANGE, not size.
        chg = _num(e.get("change_usd")) or _num(e.get("pnl_swing_usd"))
        if not (chg or e.get("opened") or e.get("flipped")):
            return None
        mag = min(1.0, (abs(chg) if chg else WHALE_MIN_USD) / (10 * WHALE_MIN_USD))
    else:
        mag = _num(e.get("magnitude"))
        if mag is None:
            usd = _num(e.get("usd"))
            mag = min(1.0, usd / WHALE_MIN_USD / 10) if usd else 0.6
    return {
        "asset": e["asset"], "dex": e.get("dex", ""), "detector": e["detector"],
        "direction": e.get("direction"), "numbers": e.get("numbers") or [],
        "notional_vol": _num(e.get("notional_vol")), "concrete_entity": e.get("concrete_entity"),
        "price_change_pct": _num(e.get("price_change_pct")),
        "magnitude": max(0.0, min(1.0, mag)), "conflict": bool(e.get("conflict")),
        "flip": bool(e.get("flip")), "is_change": bool(e.get("is_change", True)),
    }


def frame(s):
    """Content voice (social feed)."""
    a, d = s["asset"], (s.get("direction") or "")
    nums = "; ".join(s.get("numbers") or [])
    det = s["detector"]
    if det == "sm_divergence":
        return f"Smart money is {d.upper()} on {a} while the crowd leans the other way — {nums}."
    if det == "oi_surge":
        return (f"{nums} on {a}" + (f" {d}s" if d else "")
                + " — positioning building under a quiet chart.")
    if det in ("funding_extreme", "funding_flip"):
        return f"{a}: {nums} — a funding dislocation most screens never show."
    if det == "whale_move":
        return f"{s.get('concrete_entity') or 'A top trader'} on {a}: {nums}."
    if det == "sm_conviction":
        return f"{nums} on {a}."   # nums already states the flow (piling in / unwinding) + side
    if det == "cross_asset_laggard":
        return f"{a} hasn't followed the move — {nums}."
    return f"{a}: {nums}."


def badge(sc):
    """Severity flag by score — so the eye lands on the biggest first (a round-3 output convention)."""
    return "🔥" if sc >= 80 else ("🟠" if sc >= 65 else "🟡")


def trade_read(s):
    """Actionable framing (trade feed) — names the side and whether price is confirming it."""
    a, d = s["asset"], (s.get("direction") or "")
    nums = "; ".join(s.get("numbers") or [])
    det = s["detector"]
    cfm = confirmation(s)
    tag = "price confirming" if cfm >= 0.66 else ("not yet confirmed by price" if cfm <= 0.4 else "price neutral")
    if det == "sm_divergence":
        return f"Smart-money {d} vs the crowd on {a}, {tag} — a follow-the-smart-money {d} read."
    if det == "sm_conviction":
        return f"{nums} on {a} ({tag}) — a conviction shift to weigh."
    if det == "oi_surge":
        return (f"Positioning building on {a}" + (f" {d}" if d else "")
                + " with price quiet — a coil; watch for the break.")
    if det == "whale_move":
        return f"A proven wallet is adding {d} size on {a} — size following conviction."
    if det == "funding_flip":
        return f"Funding just flipped on {a} — the carry regime changed; the paid side moved."
    if det == "funding_extreme":
        return f"{a}: extreme funding — a carry read (collect the paid side), not a directional call."
    if det == "cross_asset_laggard":
        return f"{a} is the rotation laggard — a catch-up watch."
    return f"{a}: {'; '.join(s.get('numbers') or [])}."


def rank(signals, score_key, min_score, top_n, family_cap):
    """Sort by a score, drop below the floor, cap per detector FAMILY, one entry per (asset,family) so
    near-duplicate angles collapse to the strongest — and a 2nd, DIFFERENT-family angle on an
    already-shown asset (real corroboration) only if it's strong."""
    kept, per_asset_fams, per_family = [], {}, {}
    for s in sorted(signals, key=lambda s: -(s.get(score_key) or 0)):
        if (s.get(score_key) or 0) < min_score:
            continue
        fam = FAMILY.get(s["detector"], s["detector"])
        if per_family.get(fam, 0) >= family_cap:
            continue
        fams = per_asset_fams.setdefault(s["asset"], set())
        if fam in fams:
            continue                       # same asset+family already taken (e.g. divergence + conviction)
        if fams and (s.get(score_key) or 0) < 60:
            continue                       # a 2nd, different-family angle on one asset only if strong
        per_family[fam] = per_family.get(fam, 0) + 1
        fams.add(fam)
        kept.append(s)
        if len(kept) >= top_n:
            break
    return kept


def _pick_baseline(ring, now):
    """The snapshot whose age is closest to DIFF_TARGET_MIN (so a 3-min re-run still diffs against ~1h
    ago). If everything is younger, the OLDEST available; empty ring → None (first run, no diff)."""
    dated = [(s, _parse_ts(s.get("ts"))) for s in (ring or [])]
    dated = [(s, t) for s, t in dated if t is not None]
    if not dated:
        return None
    old_enough = [(s, t) for s, t in dated if (now - t).total_seconds() / 60.0 >= DIFF_TARGET_MIN]
    if old_enough:
        return max(old_enough, key=lambda x: x[1])[0]   # most-recent snapshot that's still ≥ target old
    return min(dated, key=lambda x: x[1])[0]             # else the oldest we have


def _prune_ring(ring, now):
    out = []
    for s in ring or []:
        t = _parse_ts(s.get("ts"))
        if t is not None and (now - t).total_seconds() / 60.0 <= SNAP_MAX_AGE_MIN:
            out.append(s)
    return out[-SNAP_RING_MAX:]


def _prune_surfaced(surfaced, now):
    out = {}
    for k, v in (surfaced or {}).items():
        t = _parse_ts(v)
        if t is not None and (now - t).total_seconds() / 60.0 <= FRESH_WINDOW_MIN * 2:
            out[k] = v
    return out


def _render_md(now, social, trade, lens):
    """Two badged, ranked feeds. Badges (🔥/🟠/🟡), ⭐ top-of-feed, ⚑ named-wallet — keep them."""
    ts = now.isoformat()[:16]
    out = [f"# 🔭 Senpi Signals — {ts} UTC", "",
           "_Observation, not advice. Every number is from a live read this run — verify before posting._", ""]
    if lens in ("both", "trade") and trade:
        out += ["## Tradeable dislocations — for building ideas", ""]
        for i, s in enumerate(trade):
            star = "⭐ " if i == 0 else ""
            out.append(f"{star}{badge(s['trade_score'])} **{s['trade_score']}** · `{s['asset']}` — "
                       f"{s['detector']} · {s.get('credibility', 0):.2f} cred"
                       + ("  ⚑" if s.get("concrete_entity") else ""))
            out.append(f"  {trade_read(s)}")
            out.append(f"  _{'; '.join(s.get('numbers') or [])}_")
            out.append("")
    if lens in ("both", "social") and social:
        out += ["## Market news — for content", ""]
        for i, s in enumerate(social):
            star = "⭐ " if i == 0 else ""
            out.append(f"{star}{badge(s['social_score'])} **{s['social_score']}** · `{s['asset']}` — "
                       f"{s['detector']}" + ("  ⚑" if s.get("concrete_entity") else ""))
            out.append(f"  {frame(s)}")
            out.append("")
    if not social and not trade:
        out += ["_Nothing notable stands out right now — a quiet read is a correct answer._", ""]
    return "\n".join(out)


def _default_state_path():
    """A DURABLE path co-located with the Senpi RUNTIME's own state, so it survives across chats AND
    redeploys exactly as runtimes do — never a per-chat scratchpad or /tmp, where the diff engine
    silently resets every chat. Mirrors the runtime's resolver (senpi-trading-runtime
    resolveSenpiBaseStateDir): $SENPI_SIGNALS_STATE (explicit file) › $SENPI_STATE_DIR/signals/state.json
    › ~/.openclaw/senpi-state/signals/state.json. In the hyperclaw container the claw already exports
    SENPI_STATE_DIR=/data/.openclaw/senpi-state — the Railway persistent volume mounted at /data — so
    this lands beside installed_runtimes.json on durable storage with no configuration."""
    f = os.environ.get("SENPI_SIGNALS_STATE")
    if f:
        return f
    base = os.environ.get("SENPI_STATE_DIR") or os.path.join(
        os.path.expanduser("~"), ".openclaw", "senpi-state")
    return os.path.join(base, "signals", "state.json")


def _read_state(path):
    """(ring, surfaced_by) from the state file, migrating older shapes. The snapshot ring is SHARED
    across consumers (one market baseline for everyone); surfaced_by = {consumer: {asset|detector: ts}}
    keeps each consumer's anti-repeat memory separate."""
    try:
        st = json.load(open(path)) or {}
    except Exception:
        return [], {}
    if isinstance(st.get("snapshots"), list):
        ring = st["snapshots"]
    elif st.get("asset_metrics"):                       # oldest shape: a single bare snapshot
        ring = [{"ts": st.get("ts"), "asset_metrics": st["asset_metrics"]}]
    else:
        ring = []
    sb = st.get("surfaced_by")
    if not isinstance(sb, dict):                        # migrate a non-empty flat `surfaced` → adhoc bucket
        flat = st.get("surfaced")
        sb = {"adhoc": flat} if isinstance(flat, dict) and flat else {}
    return ring, sb


def _commit_state(path, cur_metrics, now, consumer, social_picks):
    """Locked read-merge-write: re-read under an exclusive lock and MERGE this snapshot into whatever
    ring is current, so a concurrent cron/user run never clobbers the other's baseline; update only
    THIS consumer's freshness map; prune; write. Best-effort — never sinks the run."""
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        lock = open(path + ".lock", "w")
        try:
            if fcntl is not None:
                fcntl.flock(lock, fcntl.LOCK_EX)
            ring, sb = _read_state(path)                 # re-read INSIDE the lock
            by_ts = {s.get("ts"): s for s in ring if s.get("ts")}
            by_ts[now.isoformat()] = {"ts": now.isoformat(), "asset_metrics": cur_metrics}
            ring = _prune_ring(sorted(by_ts.values(), key=lambda s: s.get("ts") or ""), now)
            seen = dict(sb.get(consumer) or {})
            for s in social_picks:                       # only the social feed drives anti-repeat
                seen[f"{s['asset']}|{s['detector']}"] = now.isoformat()
            sb[consumer] = _prune_surfaced(seen, now)
            json.dump({"ts": now.isoformat(), "snapshots": ring, "surfaced_by": sb}, open(path, "w"))
        finally:
            if fcntl is not None:
                fcntl.flock(lock, fcntl.LOCK_UN)
            lock.close()
    except Exception as e:  # noqa — a state-write failure must not fail the read
        print(f"[warn] state commit failed: {e}", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input", help="current signals JSON (asset_metrics + events)")
    ap.add_argument("--state", default=None,
                    help="state file (default: durable ~/.senpi/signals/state.json — survives across chats)")
    ap.add_argument("--consumer", default="adhoc",
                    help="freshness namespace: 'adhoc' for a user's on-demand run, e.g. 'social' for the "
                         "content cron. The market baseline (ring) is shared; only anti-repeat is per-consumer.")
    ap.add_argument("--top", type=int, default=TOP_N)
    ap.add_argument("--now", default=None, help="ISO timestamp (default: now UTC)")
    ap.add_argument("--out", default="signals.md")
    ap.add_argument("--lens", choices=["both", "trade", "social"], default="both")
    a = ap.parse_args()
    state_path = a.state or _default_state_path()

    data = json.load(open(a.input))
    cur_metrics = data.get("asset_metrics") or {}
    events = [e for e in (normalize_event(e) for e in (data.get("events") or [])) if e]
    now = _parse_ts(a.now) or datetime.datetime.now(datetime.timezone.utc)

    # shared snapshot ring + THIS consumer's freshness memory (migrates older state shapes)
    ring, surfaced_by = _read_state(state_path)
    surfaced = surfaced_by.get(a.consumer, {})

    baseline = _pick_baseline(ring, now)
    prior = (baseline or {}).get("asset_metrics", {})

    signals = detect_from_metrics(cur_metrics, prior) + events
    for s in signals:
        cred = credibility(s.get("notional_vol"))
        s["credibility"] = cred
        s["freshness"] = freshness(s["asset"], s["detector"], surfaced, now)
        s["social_score"] = social_score(s, cred, s["freshness"])
        s["trade_score"] = trade_score(s, cred)

    # drop markets too thin to trust the read at all
    live = [s for s in signals if (_num(s.get("notional_vol")) or CRED_FLOOR_VOL) >= CRED_FLOOR_VOL]
    tradeable = [s for s in live if (_num(s.get("notional_vol")) or TRADE_CRED_FLOOR) >= TRADE_CRED_FLOOR]

    social = rank(live, "social_score", MIN_SOCIAL, a.top, FAMILY_CAP)
    trade = rank(tradeable, "trade_score", MIN_TRADE, a.top, FAMILY_CAP)

    # advance state: locked read-merge-write into the SHARED ring + THIS consumer's freshness map
    _commit_state(state_path, cur_metrics, now, a.consumer, social)

    open(a.out, "w").write(_render_md(now, social, trade, a.lens))
    print(json.dumps({"generated": now.isoformat(),
                      "diff_baseline_ts": (baseline or {}).get("ts"),
                      "trade": trade, "social": social}, indent=2))
    print(f"[wrote {a.out} · trade {len(trade)} · social {len(social)} · baseline "
          f"{(baseline or {}).get('ts', 'none')} · consumer {a.consumer} · state {state_path}]", file=sys.stderr)


if __name__ == "__main__":
    main()
