"""Canonical per-strategy minimum-budget calculation — PURE (no I/O, no yaml import).

VENDORED BYTE-IDENTICALLY into two skills:
  - senpi-trading-runtime/scripts/min_budget.py  (imported by gen_catalog.py -> bakes the
    number into catalog.json, which discovery reads at card time)
  - senpi-strategy-ops/scripts/min_budget.py     (imported by deploy.py -> enforces the gate;
    computes locally because custom-authored packages never pass through catalog generation)
A checksum test (test_min_budget_vendor_parity) fails CI if the two copies drift.

WHAT IT IS. The smallest TOTAL strategy budget at which the design functions: every wallet
funds (>= the $10 platform floor) AND every wallet's representative slot can open at least the
engine's bumped-minimum notional ($12) at that slot's marginPct and its LOWEST leverage.

    per_wallet_min = max( WALLET_FLOOR,  BUMPED_NOTIONAL / (marginPct/100 * min_leverage) + FEE_BUFFER )
    strategy_min   = max over wallets( per_wallet_min / funding_share )
                     floored at WALLET_FLOOR * wallet_count
                     rounded up to a clean step (STEPS)

Funding shares matter, not just wallet count: deploy splits the budget by share, so a small-
share sleeve must still receive its own per-wallet minimum -> it can DRIVE the total up (a 3-
wallet fund whose 10%-share sleeve needs $17.50 needs $175 total, not $30).

The two platform constants deploy.py used to own (MIN_WALLET, FEE_BUFFER) live HERE now, so
there is one source of truth for the physics.
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0

WALLET_FLOOR = 10.0        # platform min per wallet — mirrors the backend's MINIMUM_STRATEGY_BUDGET
                           # default ($10; senpi-hyperliquid-mcp getMinimumStrategyBudget). Keep in sync.
BUMPED_NOTIONAL = 12.0     # the engine bumps a small order up to this notional
FEE_BUFFER = 1.5           # USDC reserved per wallet for the creation fee (observed ~$1)

# The clean rungs a minimum rounds UP to. 250 is the ceiling; a design that genuinely needs
# more declares catalog.min_budget_floor (one-directional — it can only RAISE this value).
STEPS = (10, 15, 20, 25, 30, 40, 50, 60, 75, 100, 125, 200, 250)


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _round_step(x):
    for s in STEPS:
        if x <= s + 1e-9:
            return s
    return STEPS[-1]


def _scanner_inputs(runtime):
    """The first external_scanner's `inputs` map (where per-signal sizing tunables live)."""
    for sc in (runtime.get("scanners") or []):
        if isinstance(sc, dict) and sc.get("type") == "external_scanner" and isinstance(sc.get("inputs"), dict):
            return sc["inputs"]
    return {}


def _margin_pct(strat, inp):
    """The wallet's representative slot marginPct (a PERCENT). strategy.margin_pct is the
    declared baseline; fall back to the scanner's base marginPct input, then the lowest tier."""
    v = _f(strat.get("margin_pct"))
    if v and v > 0:
        return v
    for k in ("marginPct", "marginPctBase", "baseAllocationPct"):
        v = _f(inp.get(k))
        if v and v > 0:
            return v
    tiers = inp.get("marginPctTiers")
    if isinstance(tiers, dict):
        vals = [_f(x) for x in tiers.values() if _f(x) and _f(x) > 0]
        if vals:
            return min(vals)
    return None


def _lev_values(tiers):
    """Leverage values from a tiers config, whether dict ({apex,good,base}) or list-of-pairs
    ([[score, leverage], ...] where leverage is the last element)."""
    if isinstance(tiers, dict):
        return [_f(x) for x in tiers.values() if _f(x) and _f(x) > 0]
    if isinstance(tiers, list):
        out = []
        for row in tiers:
            if isinstance(row, (list, tuple)) and row:
                out.append(_f(row[-1]))
            elif _f(row):
                out.append(_f(row))
        return [x for x in out if x and x > 0]
    return []


def _min_leverage(strat, inp):
    """The LOWEST leverage the wallet sizes a position at (worst case for notional): the lowest
    EXPLICIT tier if any, else a conservative multiplier, else the tiered-margin std, else the
    default. A minLeverage clamp-floor is deliberately NOT used — it is a safety clamp, not a
    configured sizing tier."""
    vals = _lev_values(inp.get("leverageTiers"))
    if vals:
        return min(vals)
    lm = strat.get("leverage_multipliers")
    if isinstance(lm, dict) and _f(lm.get("conservative")):
        return _f(lm.get("conservative"))
    v = _f(inp.get("stdLeverage"))
    if v and v > 0:
        return v
    dl = _f(strat.get("default_leverage"))
    return dl if dl and dl > 0 else 1.0


def _per_wallet_min(margin_pct, min_leverage):
    return max(WALLET_FLOOR, BUMPED_NOTIONAL / ((margin_pct / 100.0) * min_leverage) + FEE_BUFFER)


def strategy_min_budget(manifest, runtimes):
    """PURE. `manifest` = parsed strategy.yaml dict; `runtimes` = {instance_name: parsed
    runtime.yaml dict}. Returns {min_budget, wallet_count, raw, breakdown}, where breakdown is
    one row per wallet (the 'why' the UI explains). The caller does the yaml parsing (PyYAML in
    gen_catalog, the vendored _yaml in deploy) and hands dicts in — that keeps this module pure
    and byte-identical in both homes."""
    insts = manifest.get("instances") or []
    cat = manifest.get("catalog") or {}
    per_wallet_over_share = []
    breakdown = []
    for inst in insts:
        rt = runtimes.get(inst.get("name")) or {}
        strat = rt.get("strategy") or {}
        inp = _scanner_inputs(rt)
        mp = _margin_pct(strat, inp)
        lev = _min_leverage(strat, inp)
        fs = _f(inst.get("funding_share")) or 1.0
        pw = _per_wallet_min(mp, lev) if mp else WALLET_FLOOR
        per_wallet_over_share.append(pw / fs)
        breakdown.append({
            "wallet": inst.get("name"),
            "margin_pct": mp,
            "min_leverage": lev,
            "funding_share": fs,
            "per_wallet_min": round(pw, 2),
        })
    wallet_count = len(insts)
    raw = max(per_wallet_over_share) if per_wallet_over_share else WALLET_FLOOR
    floored = max(raw, WALLET_FLOOR * max(wallet_count, 1))
    computed = _round_step(floored)
    floor_override = _f(cat.get("min_budget_floor"))
    if floor_override:                              # one-directional: can only RAISE the value
        computed = max(computed, _round_step(floor_override))
    # the binding wallet: the one whose per-wallet-min ÷ share set the raw number
    binding = None
    if breakdown:
        binding = max(breakdown, key=lambda b: b["per_wallet_min"] / (b["funding_share"] or 1.0))["wallet"]
    return {
        "min_budget": computed,
        "wallet_count": wallet_count,
        "raw": round(floored, 2),
        "binding_wallet": binding,
        "breakdown": breakdown,
    }
