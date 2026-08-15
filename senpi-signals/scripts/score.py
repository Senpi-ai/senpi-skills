#!/usr/bin/env python3
"""senpi-signals ranker — the stateful diff + noteworthiness engine.

Input JSON: { "asset_metrics": { "<asset>": {oi, price, smart_share, smart_dir, crowd_dir,
              funding_pctile, funding_annualized_pct, notional_vol, trader_count, dex, oi_side} },
              "events": [ pre-formed signals (whale_move / momentum_event / cross_asset_laggard) ] }

It diffs asset_metrics against the prior snapshot in --state, fires the threshold detectors, merges
the pre-formed events, scores noteworthiness, drops noise, dedupes per asset, ranks, writes the new
snapshot, and prints the ranked signals as JSON (markdown to --out). Stdlib only.

Thresholds mirror references/detectors.md — change them in BOTH places.
"""
import argparse, json, sys, os, datetime

# ── thresholds (keep in sync with references/detectors.md) ──
OI_SURGE_PCT   = 0.10      # OI change vs prior to fire oi_surge
PRICE_FLAT     = 0.01      # |price change| below this => OI-price divergence (conflict)
SMART_SHARE_MIN= 25.0      # min top-trader concentration for a divergence to count
SMART_JUMP_PP  = 12.0      # jump in concentration (pts) to fire sm_conviction
FUNDING_PCTILE = 95.0      # funding percentile to fire funding_dislocation
WHALE_MIN_USD  = 1_000_000 # for magnitude scaling of whale_move events
VOL_FLOOR      = 1_000_000 # full-credibility notional volume
HARD_VOL_FLOOR = 250_000   # below this => dropped as illiquid
MIN_SCORE      = 45.0
TOP_N          = 6

NON_OBVIOUS = {  # how invisible-on-a-chart the detector is (the moat weight)
    "oi_surge": 1.0, "funding_dislocation": 1.0, "sm_divergence": 1.0, "whale_move": 1.0,
    "sm_conviction": 0.8, "cross_asset_laggard": 0.8, "momentum_event": 0.6, "regime_shift": 0.6,
}


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _sign(x):
    return 0 if x is None or x == 0 else (1 if x > 0 else -1)


def detect_from_metrics(cur, prior):
    """Fire the diff/threshold detectors from current asset_metrics vs the prior snapshot."""
    out = []
    for asset, m in (cur or {}).items():
        if not isinstance(m, dict):
            continue
        p = (prior or {}).get(asset, {}) if isinstance(prior, dict) else {}
        dex = m.get("dex", "")
        vol = _num(m.get("notional_vol")) or _num(m.get("day_notional_volume"))

        def sig(detector, direction, magnitude, numbers, conflict=False, flip=False):
            out.append({"asset": asset, "dex": dex, "detector": detector, "direction": direction,
                        "numbers": numbers, "notional_vol": vol, "concrete_entity": None,
                        "magnitude": magnitude, "conflict": conflict, "flip": flip})

        oi, poi = _num(m.get("oi")), _num(p.get("oi"))
        if oi is not None and poi:
            pct = (oi - poi) / poi
            if pct >= OI_SURGE_PCT:
                price, pprice = _num(m.get("price")), _num(p.get("price"))
                flat = price is not None and pprice not in (None, 0) and abs((price - pprice) / pprice) < PRICE_FLAT
                nums = [f"OI +{pct*100:.0f}% since last check"]
                if flat:
                    nums.append("price ~flat")
                sig("oi_surge", m.get("oi_side"), pct, nums, conflict=flat)

        sd, cd, share = m.get("smart_dir"), m.get("crowd_dir"), _num(m.get("smart_share"))
        if sd and cd and sd != cd and share is not None and share >= SMART_SHARE_MIN:
            flip = bool(p.get("smart_dir")) and p.get("smart_dir") != sd
            sig("sm_divergence", sd, share / 100.0,
                [f"smart money {str(sd).upper()} ({share:.0f}% of top-trader PnL)", f"crowd {str(cd).upper()}"],
                conflict=True, flip=flip)

        pshare = _num(p.get("smart_share"))
        if share is not None and pshare is not None and abs(share - pshare) >= SMART_JUMP_PP:
            delta = share - pshare
            flow = "piling in" if delta > 0 else "unwinding"
            side = (str(sd).upper() + " ") if sd else ""
            sig("sm_conviction", sd, abs(share) / 100.0,
                [f"top traders {flow} {side}".rstrip()
                 + f" — concentration {'+' if delta > 0 else '−'}{abs(delta):.0f}pp to {share:.0f}%"])

        fp = _num(m.get("funding_pctile"))
        if fp is not None:
            fa, pfa = _num(m.get("funding_annualized_pct")), _num(p.get("funding_annualized_pct"))
            flip = fa is not None and pfa is not None and _sign(fa) != _sign(pfa) and _sign(pfa) != 0
            if fp >= FUNDING_PCTILE or flip:
                nums = [f"funding {fp:.0f}th pctile"]
                if fa is not None:
                    nums.append(f"{fa:.0f}%/yr")
                sig("funding_dislocation", None, fp / 100.0, nums, flip=flip)
    return out


def normalize_event(e):
    if not isinstance(e, dict) or not e.get("asset") or not e.get("detector"):
        return None
    det = e["detector"]
    if det == "whale_move":
        # MOVES, not holdings. A big position held from an old entry with no recent change is NOT a
        # signal. Require a recent size change (opened/added/flipped) or a large 4h PnL swing.
        chg = _num(e.get("change_usd")) or _num(e.get("pnl_swing_usd"))
        if not (chg or e.get("opened") or e.get("flipped")):
            return None
        base = abs(chg) if chg else WHALE_MIN_USD
        mag = min(1.0, base / (10 * WHALE_MIN_USD))
    else:
        mag = _num(e.get("magnitude"))
        if mag is None:
            mag = 0.6
    return {"asset": e["asset"], "dex": e.get("dex", ""), "detector": e["detector"],
            "direction": e.get("direction"), "numbers": e.get("numbers") or [],
            "notional_vol": _num(e.get("notional_vol")), "concrete_entity": e.get("concrete_entity"),
            "magnitude": max(0.0, min(1.0, mag)), "conflict": bool(e.get("conflict")),
            "flip": bool(e.get("flip"))}


def score(s):
    no = NON_OBVIOUS.get(s["detector"], 0.6)
    mag = max(0.0, min(1.0, _num(s.get("magnitude")) or 0.0))
    conflict = 1.0 if s.get("conflict") else 0.0
    concrete = 1.0 if s.get("concrete_entity") else 0.3
    vol = _num(s.get("notional_vol")) or 0.0
    cred = 1.0 if vol >= VOL_FLOOR else (vol / VOL_FLOOR if VOL_FLOOR else 0.0)
    val = 100 * (0.35 * no + 0.25 * mag + 0.20 * conflict + 0.10 * concrete + 0.10 * cred)
    if s.get("flip"):
        val += 5  # a "just flipped" is fresher
    return round(min(val, 100.0), 1)


def badge(sc):
    """Severity flag by noteworthiness score — so the eye lands on the biggest first."""
    return "🔥" if sc >= 80 else ("🟠" if sc >= 65 else "🟡")


def frame(s):
    """Every headline states DIRECTION (long/short). A surge with no side is useless."""
    a, d = s["asset"], (s.get("direction") or "")
    dl = f" {d.upper()}" if d else ""
    nums = "; ".join(s.get("numbers") or [])
    det = s["detector"]
    if det == "sm_divergence":
        return f"Smart money is{dl} on {a} while the crowd is the other way — {nums}."
    if det == "oi_surge":
        side = f" {d.upper()}S" if d else " (side unresolved — pull the OI long/short split)"
        return f"OI building on {a}{side} — {nums}."
    if det == "funding_dislocation":
        return f"{a}: {nums} — a funding dislocation most screens miss."
    if det == "whale_move":
        who = s.get("concrete_entity") or "a top trader"
        return f"{who} on {a}{dl}: {nums}."
    if det == "sm_conviction":
        return f"{a}{dl}: {nums}."
    return f"{a}{dl}: {nums}."


def dedupe_rank(signals, top_n):
    for s in signals:
        s["score"] = score(s)
    signals = [s for s in signals if s["score"] >= MIN_SCORE]
    # drop illiquid (only when a positive volume is known and below the hard floor)
    signals = [s for s in signals if not ((_num(s.get("notional_vol")) or 0) > 0
                                          and (_num(s.get("notional_vol")) or 0) < HARD_VOL_FLOOR)]
    signals.sort(key=lambda s: -s["score"])
    kept, per_asset = [], {}
    for s in signals:
        seen = per_asset.setdefault(s["asset"], [])
        if not seen:
            seen.append(s["detector"]); kept.append(s)
        elif s["detector"] not in seen and s["score"] >= 70:  # a 2nd only if distinct + strong
            seen.append(s["detector"]); kept.append(s)
    return kept[:top_n]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input", help="current signals JSON (asset_metrics + events)")
    ap.add_argument("--state", default=os.path.join(__import__("tempfile").gettempdir(),
                    "senpi-signals-state.json"))
    ap.add_argument("--top", type=int, default=TOP_N)
    ap.add_argument("--now", default=None, help="ISO timestamp (default: now UTC)")
    ap.add_argument("--out", default="signals.md")
    a = ap.parse_args()

    data = json.load(open(a.input))
    cur_metrics = data.get("asset_metrics") or {}
    events = [e for e in (normalize_event(e) for e in (data.get("events") or [])) if e]

    prior = {}
    if os.path.isfile(a.state):
        try:
            prior = (json.load(open(a.state)) or {}).get("asset_metrics", {})
        except Exception:
            prior = {}

    signals = detect_from_metrics(cur_metrics, prior) + events
    ranked = dedupe_rank(signals, a.top)

    now = a.now or datetime.datetime.now(datetime.timezone.utc).isoformat()
    try:
        os.makedirs(os.path.dirname(a.state) or ".", exist_ok=True)
        json.dump({"ts": now, "asset_metrics": cur_metrics}, open(a.state, "w"))
    except Exception as e:  # noqa — never sink the run on a state-write failure
        print(f"[warn] state write failed: {e}", file=sys.stderr)

    lines = [f"# Live Signals — {now[:16]}Z · {len(ranked)} noteworthy", "",
             "_Observation, not advice. Every number is from a live read — verify before posting._", ""]
    for i, s in enumerate(ranked):
        star = "⭐ " if i == 0 else ""
        lines.append(f"{star}{badge(s['score'])} **{s['score']}** · `{s['asset']}` — {s['detector']}"
                     + ("  ⚑ whale" if s.get("concrete_entity") else ""))
        lines.append(f"  {frame(s)}")
        lines.append("")
    md = "\n".join(lines)
    open(a.out, "w").write(md)
    print(json.dumps({"generated": now, "count": len(ranked), "signals": ranked}, indent=2))
    print(f"\n[wrote {a.out} · {len(ranked)} of {len(signals)} candidates · state {a.state}]",
          file=sys.stderr)


if __name__ == "__main__":
    main()
