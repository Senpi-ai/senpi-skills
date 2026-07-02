#!/usr/bin/env python3
"""senpi-improve-trades engine — retrospective trade-review + coaching data work (hidden).

The agent (LLM) runs this via the OpenClaw `exec` tool, reads the JSON on stdout, and NARRATES a
disciplined trade review + improvement coaching under the SKILL.md guardrails. This script does the
precise, deterministic data work — reconstruct every CLOSED trade, attribute its exit mechanism, compute
the honest "if I'd held to now" counterfactual, and cross the book against what the market did — and the
LLM does the prose, the process-framing, and the fix-depth CTAs.

  python3 review.py                        # last ~7d review (all strategy wallets)
  python3 review.py --window 30            # last 30 days
  python3 review.py --last 20              # cap to the last 20 closed trades (per wallet)
  python3 review.py --no-market            # skip the current-price / book-vs-market pull
  python3 review.py --fixture f.json       # offline: recorded MCP-response map (tests)
  python3 review.py --dry                  # dump raw MCP responses for schema debugging

WHY THIS EXISTS — the anti-fabrication mechanism:
Live agents answering "did I sell too early?" fall into hindsight bias (grading an exit "wrong" because
the asset later reversed), invent forward "+$X/week" projections, and confuse the USER with the
autonomous STRATEGY that actually exited the trade. This engine computes the timing table + the
market-gap FOR the LLM so it cannot skip the entry->exit->current-price comparison or invent forward
numbers — it narrates real, engine-computed values. It reports realized PnL + engine counterfactuals
(if_held, if_all_reclosed_now) as PROCESS-framed COUNTS, never a $/week projection.

SOURCE BOUNDARY (telemetry-ready): trades[] are assembled behind a single `_collect_trades()` step that
fuses discovery_get_trader_history + ratchet_stop_list + market prices. Each trade carries a `source`
tag ("reconstructed"). v2 telemetry (the successor to the removed audit_* tools) slots in HERE as an
additional/primary trade+exit source without touching the narration, the guardrails, or the output shape.

⚠ All tools here are USER-scoped (your own account): needs a USER-scoped SENPI_AUTH_TOKEN.
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
CLOSED_HISTORY_PULL = 100    # closed positions to pull per wallet before the window filter
WINDOW_DEFAULT_DAYS = 7      # the default review window
TOP_MOVERS_CAP = 12          # cap the book-vs-market movers surfaced

# The runtime registers every deployed strategy in installed_runtimes.json in the state dir — the
# UNIVERSAL source of a strategy's mandate (the runtime.yaml `description`) + its DSL ladder. Reused
# verbatim in spirit from senpi-portfolio (the extractions there are already correct).
STATE_DIR_ENV = "SENPI_STATE_DIR"
REGISTRY_FILENAME = "installed_runtimes.json"


def _resolve_state_dir():
    """Locate the OpenClaw runtime state dir holding installed_runtimes.json — robustly, WITHOUT relying
    on $HOME (it may be /root while OpenClaw lives under /data). On a real host the skill installs at
    `<root>/.openclaw/skills/<skill>/scripts/` and the state dir is a sibling: `<root>/.openclaw/senpi-state`.
    Order: (1) $SENPI_STATE_DIR; (2) derive from THIS file's install path — the enclosing `.openclaw` dir
    → its `senpi-state` (or any ancestor that actually holds the registry); (3) common host locations;
    (4) ~/.openclaw/senpi-state as last resort."""
    env = os.environ.get(STATE_DIR_ENV)
    if env:
        return env
    d = os.path.abspath(__file__)
    for _ in range(8):
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
        if os.path.basename(d) == ".openclaw":
            return os.path.join(d, "senpi-state")
        if os.path.isfile(os.path.join(d, "senpi-state", REGISTRY_FILENAME)):
            return os.path.join(d, "senpi-state")
    for base in ("~/.openclaw/senpi-state", "/data/.openclaw/senpi-state", "/root/.openclaw/senpi-state"):
        p = os.path.expanduser(base)
        if os.path.isdir(p):
            return p
    return os.path.expanduser("~/.openclaw/senpi-state")


# ──────────────────────────────────────────────────────────────── guarded I/O helpers (lifted from portfolio.py)
def _ok(resp):
    if isinstance(resp, dict):
        if resp.get("success") is False:
            return None
        return resp.get("data", resp)
    return resp


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _f(d, *keys, default=0.0):
    if isinstance(d, dict):
        for k in keys:
            if k in d and d[k] is not None:
                n = _num(d[k])
                if n is not None:
                    return n
    return default


def _field(d, *names, default=None):
    if isinstance(d, dict):
        for n in names:
            if n in d and d[n] is not None:
                return d[n]
    return default


# ──────────────────────────────────────────────────────────────── vendored YAML (runtime.yaml parse)
def _yaml_loads(text):
    """Parse runtime.yaml text via the vendored stdlib loader (scripts/_yaml.py — no cross-skill
    import). Returns the parsed mapping or None; never raises here (caller guards)."""
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    import _yaml
    return _yaml.loads(text)


def _collapse_ws(s):
    """Collapse internal whitespace/newlines in a folded `description` block to single spaces + strip."""
    if not isinstance(s, str):
        return None
    out = " ".join(s.split()).strip()
    return out or None


# ──────────────────────────────────────────────── DSL ladder (config side — which lever a bad exit maps to)
def _dsl_ladder(exit_block):
    """Parse the DSL PROTECTION LADDER from a runtime.yaml `exit:` block (lifted from portfolio.py). Says
    HOW DSL works for this strategy — the phase1 hard-stop floor (active FROM ENTRY) + the phase2
    profit-lock tiers — so a bad exit can be routed to the exact lever (widen the hard stop, retune a
    tier). NEVER raises; fail-open to None.

      - inline mapping with phase1/phase2 → {hard_stop_roe_pct, arm_at_roe_pct, tiers[], has_phase2}
      - a NAMED string preset ("conviction", …) → {preset_name, note}
      - no dsl_preset → None
    """
    if not isinstance(exit_block, dict):
        return None
    dp = exit_block.get("dsl_preset")
    if isinstance(dp, str):
        return {"preset_name": dp, "note": "named preset — ladder not inlined"}
    if not isinstance(dp, dict) or not dp:
        return None

    p1 = dp.get("phase1") if isinstance(dp.get("phase1"), dict) else {}
    p2 = dp.get("phase2") if isinstance(dp.get("phase2"), dict) else {}

    hard = None
    if p1 and p1.get("enabled") is not False:
        ml = _num(p1.get("max_loss_pct"))
        if ml is not None:
            hard = -abs(ml)

    tiers, has_phase2, arm_at = [], False, None
    if p2 and p2.get("enabled") is not False:
        raw_tiers = p2.get("tiers")
        if isinstance(raw_tiers, list):
            for t in raw_tiers:
                if not isinstance(t, dict):
                    continue
                trig = _num(t.get("trigger_pct"))
                lock = _num(t.get("lock_hw_pct"))
                tiers.append({"trigger_pct": trig, "lock_hw_pct": lock})
        has_phase2 = bool(tiers)
        if tiers and tiers[0].get("trigger_pct") is not None:
            arm_at = tiers[0]["trigger_pct"]

    return {
        "hard_stop_roe_pct": hard,       # e.g. -14.0 — floor, active FROM ENTRY (phase1)
        "arm_at_roe_pct": arm_at,        # e.g. 8 — where the profit-ratchet ARMS (Tier 1), or null
        "tiers": tiers,                  # the profit-lock ladder ([] when phase2 off)
        "has_phase2": has_phase2,
    }


def _profile_from_runtime_yaml(text):
    """Parse one deployed runtime.yaml TEXT into the mandate + DSL ladder (lifted from portfolio.py).
    Returns a dict (possibly partial) or None if the text doesn't parse to a mapping. Never raises."""
    doc = _yaml_loads(text)
    if not isinstance(doc, dict):
        return None
    exit_block = doc.get("exit")
    return {
        "runtime_name": doc.get("name"),
        "group": doc.get("group"),
        "version": doc.get("version"),
        "description": _collapse_ws(doc.get("description")),   # the UNIVERSAL mandate — "what it does"
        "dsl": _dsl_ladder(exit_block),                        # which lever a bad exit maps to
        "has_exit": bool(isinstance(exit_block, dict) and exit_block),
    }


def load_runtime_registry(meta):
    """wallet_lower → runtime-profile for every deployed strategy the runtime has registered (lifted from
    portfolio.py). SOURCE OF TRUTH for a strategy's mandate (the runtime.yaml `description`) + DSL ladder,
    read from the DEPLOYED runtime.yaml in installed_runtimes.json (state dir). UNIVERSAL — covers
    user-authored strategies, not just catalog templates. Read-guarded + fail-open: any problem → ({},
    None). A meta.warnings note is added ONLY for a real parse error, not an absent registry file.
    Returns (map, source)."""
    state_dir = _resolve_state_dir()
    meta["state_dir"] = state_dir          # surfaced for debugging path issues
    path = os.path.join(state_dir, REGISTRY_FILENAME)
    if not os.path.isfile(path):          # absent registry is normal, not an error
        return {}, None
    try:
        with open(path) as fh:
            raw = json.load(fh)
    except Exception as e:  # noqa — a corrupt registry is a real parse error worth surfacing
        meta.setdefault("warnings", []).append(
            f"runtime registry unreadable ({e}); mandates unavailable")
        return {}, None
    entries = raw.get("runtimes", raw) if isinstance(raw, dict) else raw
    out = {}
    for entry in (entries if isinstance(entries, list) else []):
        if not isinstance(entry, dict):
            continue
        wallet = entry.get("wallet")
        if not wallet:
            continue
        text = entry.get("runtimeYamlContent")
        if text is None:
            ypath = entry.get("runtimeYamlPath")   # rarer form — a file path instead of inline content
            if ypath and os.path.isfile(ypath):
                try:
                    with open(ypath) as yf:
                        text = yf.read()
                except Exception:  # noqa — a missing/unreadable path is fail-open, skip this entry
                    text = None
        if not text:
            continue
        try:
            prof = _profile_from_runtime_yaml(text)
        except Exception as e:  # noqa — one bad runtime.yaml must not sink the whole registry
            meta.setdefault("warnings", []).append(
                f"runtime.yaml parse failed for {str(wallet)[:8]} ({e})")
            prof = None
        if prof:
            out[str(wallet).lower()] = prof
    return out, "registry"


# ──────────────────────────────────────────────────────────────── client
def _get_client():
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    from mcp_client import MCPClient
    return MCPClient()


class _FixtureClient:
    """Offline stand-in. Keys a call by (tool, strategy_wallet) or (tool, asset/dex) so a fixture can
    return per-wallet history/ratchet state. Falls back to the bare tool name."""
    def __init__(self, recorded):
        self._r = recorded

    def mcp_call(self, tool, timeout=12, **kw):
        for keyer in ("strategy_wallet", "strategy_wallet_address", "trader_address", "strategyId", "asset"):
            if kw.get(keyer):
                k = f"{tool}::{str(kw[keyer]).lower()}"
                if k in self._r:
                    return self._r[k]
        if "dex" in kw:
            k = f"{tool}::{kw['dex']}"
            if k in self._r:
                return self._r[k]
        return self._r.get(tool)


# ──────────────────────────────────────────────────────────────── strategies (mandate + DSL + wallet)
def fetch_strategies(client, meta):
    """Enumerate the user's strategies → per strategy {label, wallet, strategy_id, skill_name, status,
    mandate, dsl}. **Includes CLOSED + PAUSED, not just ACTIVE** — this is a RETROSPECTIVE skill, and a
    churned book's recent closed trades live on strategies the user has since CLOSED; an ACTIVE-only
    enumeration misses exactly the trades a "review my last trades / what did I miss" is asking about.
    Mandate + DSL come from the deployed runtime.yaml registry (universal), keyed by wallet — None for a
    closed strategy whose runtime was deregistered (the trade is still reviewed; exit attribution still
    comes from the ratchet record). Fail-open: []."""
    try:
        sl = _ok(client.mcp_call("strategy_list", status=["ACTIVE", "PAUSED", "CLOSED"], timeout=20))
    except Exception as e:  # noqa
        meta.setdefault("warnings", []).append(f"strategy_list failed: {e}")
        return []
    rows = sl if isinstance(sl, list) else _field(sl, "strategies", "data", default=[])
    registry, registry_src = load_runtime_registry(meta)   # wallet_lower → runtime profile
    meta["registry_source"] = registry_src
    strategies = []
    for s in (rows or []):
        wallet = _field(s, "strategyWalletAddress", "strategy_wallet_address", "walletAddress")
        if not wallet:
            continue
        skill_name = None
        meta_obj = _field(s, "strategyMetadata", "metadata")
        if isinstance(meta_obj, dict):
            skill_name = _field(meta_obj, "skillName", "skill_name")
        if not skill_name:
            skill_name = _field(s, "skillName", "skill_name", "skill")
        prof = registry.get(str(wallet).lower()) or {}
        strategies.append({
            "label": _field(s, "tradingStrategyName", "name", default="strategy"),
            "wallet": wallet,
            "strategy_id": _field(s, "id", "strategyId", "strategy_id"),
            "skill_name": skill_name,
            # so the narration can flag a trade on a strategy the user has since closed
            "status": _field(s, "status", default="ACTIVE"),
            "group": prof.get("group"),
            # mandate = the strategy's declared job, from its DEPLOYED runtime.yaml (universal). The
            # yardstick to judge every closed trade against — and the source of "it's the strategy, not
            # you." None when the registry is absent (a warning already noted).
            "mandate": prof.get("description"),
            # the DSL ladder — WHICH lever a bad exit maps to (hard stop / arm-at / a tier).
            "dsl": prof.get("dsl"),
        })
    if not registry_src and strategies:
        meta.setdefault("warnings", []).append(
            "runtime registry absent — mandates + DSL levers unavailable; review runs on trades only")
    return strategies


# ──────────────────────────────────────────────────────────────── exit attribution (ratchet_stop_list)
def _load_ratchet_records(client, strat, meta):
    """One ratchet_stop_list(strategyId, wallet, status:ALL) call per strategy → {asset: record}. Lifted
    in spirit from portfolio.py's attach_position_dsl, but status:ALL so a CLOSED trade's terminal record
    (SL_TRIGGERED / MANUALLY_CLOSED / LIQUIDATED / ADL) is included, not just ACTIVE. Read-guarded +
    fail-open: on any error → {} + a meta.warnings note (exit_reason falls back to UNKNOWN)."""
    sid = strat.get("strategy_id")
    wallet = strat.get("wallet")
    out = {}
    try:
        rl = _ok(client.mcp_call("ratchet_stop_list", strategyId=sid,
                                 strategy_wallet_address=wallet, status="ALL", timeout=15))
    except Exception as e:  # noqa — fail-open: exit_reason becomes UNKNOWN, never guessed
        meta.setdefault("warnings", []).append(
            f"ratchet_stop_list {str(wallet)[:8]} failed: {e}; exit attribution degraded to UNKNOWN")
        return out
    rows = rl if isinstance(rl, list) else _field(rl, "configs", "ratchetStops", "data", "items", default=[])
    for r in (rows if isinstance(rows, list) else []):
        if not isinstance(r, dict):
            continue
        asset = _field(r, "asset", "coin")
        if asset:
            out[str(asset)] = r
    return out


# terminal status → the exit_reason.terminal enum in the output contract. UNKNOWN is the honest default:
# no ratchet record for a closed trade means we don't KNOW the mechanism — never guess it (guardrail 6).
_TERMINAL_MAP = {
    "SL_TRIGGERED": "SL_TRIGGERED",     # the DSL fired — a hard stop or a locked profit tier
    "MANUALLY_CLOSED": "MANUAL_CLOSE",
    "LIQUIDATED": "LIQUIDATED",
    "ADL": "ADL",
}


def _exit_reason_for(asset, ratchet_records):
    """Authoritative exit attribution for one closed trade, from the ratchet record keyed by asset. A
    SL_TRIGGERED record → terminal SL_TRIGGERED + tier_reached (currentTierIndex) + high_water_roe (which
    tier locked → which DSL lever to tune). No record → terminal UNKNOWN (never guessed)."""
    rec = ratchet_records.get(str(asset)) if isinstance(ratchet_records, dict) else None
    if not isinstance(rec, dict):
        return {"terminal": "UNKNOWN", "tier_reached": None, "high_water_roe": None}
    status = str(_field(rec, "status", default="") or "").upper()
    terminal = _TERMINAL_MAP.get(status, "UNKNOWN")
    return {
        "terminal": terminal,
        "tier_reached": _field(rec, "currentTierIndex", "current_tier_index"),
        "high_water_roe": _field(rec, "highWaterRoe", "high_water_roe"),
        "status_raw": status or None,
    }


# ──────────────────────────────────────────────────────────────── closed trades (discovery_get_trader_history)
def _ms(ts):
    """Normalize a Unix timestamp to MILLISECONDS. trader-history close/open times have been seen in both
    seconds and ms; anything below ~1e12 (≈ 2001 in ms) is seconds → scale ×1000. Without this a
    seconds-valued closeTime is wrongly judged 'older than the window' and EVERY trade gets filtered out
    (the 0-trades-on-a-book-that-has-trades bug)."""
    n = _num(ts)
    if n is None:
        return None
    return n * 1000.0 if n < 1e12 else n


def fetch_closed_trades(client, wallet, since_ms, until_ms, cap, meta):
    """Read-guarded closed-position ledger for one strategy wallet, filtered to the review window. Lifts
    portfolio.py's fetch_closed extraction (the real discovery_get_trader_history shape: closedPositions[]
    of coin, signed szi, string realizedPnl, Unix-ms openTime/closeTime, entryPx/exitPx). `since_ms`
    filters by closeTime; `cap` (optional) keeps only the last N by close time. Fails OPEN → []."""
    try:
        h = _ok(client.mcp_call("discovery_get_trader_history", trader_address=wallet,
                                sort_by="CLOSED_TIME", sort_direction="DESC",
                                limit=CLOSED_HISTORY_PULL, timeout=20))
    except Exception as e:  # noqa
        meta.setdefault("warnings", []).append(f"trader_history {wallet[:8]} failed: {e}")
        return []
    if h is None:
        meta.setdefault("warnings", []).append(f"trader_history {wallet[:8]} returned no data")
        return []
    rows = h if isinstance(h, list) else _field(h, "closedPositions", "closed_positions", "positions", default=[])
    if not isinstance(rows, list):
        rows = []
    trades = []
    for p in rows:
        if not isinstance(p, dict):
            continue
        close_ms = _ms(_field(p, "closeTime", "closed_time", "closeTimeMs"))
        if since_ms is not None and close_ms is not None and close_ms < since_ms:
            continue                        # older than the window — skip
        if until_ms is not None and close_ms is not None and close_ms > until_ms:
            continue
        szi = _f(p, "szi", "size", default=0.0)
        lev = p.get("leverage") or {}
        trades.append({
            "asset": _field(p, "coin", "coinDisplayName", "asset"),
            "direction": "long" if szi >= 0 else "short",   # closed-side sign (szi>0 closed a long)
            "size": abs(szi),
            "leverage": _f(lev, "value", default=None) if isinstance(lev, dict) else _num(lev),
            "entry_px": _num(_field(p, "entryPx", "entry_px")),
            "exit_px": _num(_field(p, "exitPx", "exit_px")),
            "realized_pnl": round(_f(p, "realizedPnl", "realized_pnl", default=0.0), 2),
            "margin_used": _f(p, "marginUsed", "margin_used", default=None),
            "open_time": _ms(_field(p, "openTime", "open_time")),
            "close_time": close_ms,
            "closed_order_id": _field(p, "closedOrderId", "closed_order_id"),
        })
    trades.sort(key=lambda t: _num(t.get("close_time")) or 0, reverse=True)
    if cap:
        trades = trades[:cap]
    return trades


# ──────────────────────────────────────────────────────────────── current price (market_get_asset_data)
def _price_now(client, asset, dex, meta):
    """CURRENT mark price for one asset (lifted from portfolio.py's market_get_asset_data extraction —
    reads markPx from the context block). Only CURRENT price is needed for v1 (no historical candles).
    Read-guarded → None on any failure."""
    kw = dict(asset=asset, candle_intervals=[], include_order_book=False, include_funding=False, timeout=12)
    if dex == "xyz" or str(asset).startswith("xyz:"):
        kw["dex"] = "xyz"
    try:
        data = _ok(client.mcp_call("market_get_asset_data", **kw))
    except Exception:  # noqa
        return None
    ctx = _field(data, "asset_context", "context", default={}) or {}
    inner = ctx if ("markPx" in ctx) else (_field(data, "context", default={}) or {})
    mark = _field(ctx, "markPx", default=None) or _field(inner, "markPx", default=None)
    return _num(mark)


# ──────────────────────────────────────────────────────────────── the counterfactual math (per trade)
def _if_held(trade, price_now):
    """The honest 'if I'd held to now' counterfactual for ONE closed trade — CONTEXT, never the verdict.

    since_exit_pct = (price_now - exit_px)/exit_px  (raw price move since the exit)
    if_held_delta_usd = notional × since_exit_pct, DIRECTION-ADJUSTED — a short GAINS when price falls,
      so a short's delta flips the sign. This is the extra $ the position WOULD have made (or lost) if it
      had stayed open from the exit to now, on top of the realized PnL.
    exit_vs_hold ∈ {beat, worse, flat}: did the realized exit BEAT holding-to-now? A negative
      if_held_delta means holding would have LOST money vs the exit → the exit BEAT holding.
    Returns (since_exit_pct, if_held_delta_usd, exit_vs_hold) — any None-input → (None, None, "unknown")."""
    exit_px = _num(trade.get("exit_px"))
    if price_now is None or exit_px is None or exit_px == 0:
        return None, None, "unknown"
    since_exit_pct = round((price_now - exit_px) / exit_px * 100, 2)
    # notional at exit = size × exit_px. (Position value the counterfactual moves on.)
    size = _num(trade.get("size"))
    notional = abs(size * exit_px) if size is not None else None
    if notional is None:
        return since_exit_pct, None, "unknown"
    raw_move = (price_now - exit_px) / exit_px
    # direction-adjusted: long gains when price rises (+), short gains when price falls (so flip sign).
    signed = raw_move if trade.get("direction") == "long" else -raw_move
    if_held_delta = round(notional * signed, 2)
    # exit_vs_hold: holding would have added if_held_delta on top of realized. Positive delta → holding
    # beat the exit (exit was "worse"); negative → the exit beat holding; ~0 → flat.
    eps = max(1.0, 0.001 * (notional or 0.0))     # $1 or 0.1% of notional, whichever larger — noise floor
    if if_held_delta > eps:
        verdict = "worse"       # holding would have made more → the exit was worse than holding
    elif if_held_delta < -eps:
        verdict = "beat"        # holding would have lost → the exit beat holding
    else:
        verdict = "flat"
    return since_exit_pct, if_held_delta, verdict


# ──────────────────────────────────────────────────────────────── the source boundary (telemetry-ready)
def _collect_trades(client, strategies, meta, since_ms, until_ms, cap, want_market):
    """THE SOURCE BOUNDARY (per DESIGN — keep this seam clean for v2 telemetry).

    Fuses the reconstructed sources — discovery_get_trader_history (the closed ledger) +
    ratchet_stop_list(status:ALL) (the exit mechanism) + market_get_asset_data (current price) — into one
    flat trades[]. Every trade is tagged source:"reconstructed"; v2 telemetry becomes an additional or
    primary source here (higher-fidelity exit_reason + entry context) without touching narration/output.

    For each closed trade: attaches exit_reason (authoritative, by asset), price_now, price_since_exit_pct,
    if_held_delta_usd, and exit_vs_hold. Fail-open per source — a missing source degrades that field, not
    the whole trade."""
    trades = []
    for strat in strategies:
        closed = fetch_closed_trades(client, strat["wallet"], since_ms, until_ms, cap, meta)
        if not closed:
            continue
        ratchet_records = _load_ratchet_records(client, strat, meta)
        for t in closed:
            asset = t.get("asset")
            dex = "xyz" if str(asset).startswith("xyz:") else "main"
            price_now = _price_now(client, asset, dex, meta) if want_market else None
            since_pct, if_held, verdict = _if_held(t, price_now)
            t.update({
                "strategy_label": strat.get("label"),
                "strategy_wallet": strat.get("wallet"),
                "mandate": strat.get("mandate"),
                "dex": dex,
                "source": "reconstructed",      # provenance tag — v2 telemetry flips this per-trade
                "exit_reason": _exit_reason_for(asset, ratchet_records),
                "price_now": price_now,
                "price_since_exit_pct": since_pct,
                "if_held_delta_usd": if_held,   # CONTEXT, not verdict
                "exit_vs_hold": verdict,        # engine verdict of the exit vs holding-to-now
            })
            trades.append(t)
    trades.sort(key=lambda t: _num(t.get("close_time")) or 0, reverse=True)
    return trades


# ──────────────────────────────────────────────────────────────── timing summary (PROCESS-framed counts)
def _timing_summary(trades):
    """Process-framed COUNTS — the aggregate the narrator LEADS with, so it can't cherry-pick the few
    reversals. NO $/week, NO forward projections anywhere — only realized totals + engine counterfactuals.

    if_all_reclosed_now_total = the honest counterfactual aggregate: the sum of every trade's
    if_held_delta_usd (what the whole book WOULD have added, net, if every position had stayed open to
    now). Reported as context alongside realized_pnl_total, never as a verdict or a projection."""
    trade_count = len(trades)
    beat = sum(1 for t in trades if t.get("exit_vs_hold") == "beat")
    worse = sum(1 for t in trades if t.get("exit_vs_hold") == "worse")
    flat = sum(1 for t in trades if t.get("exit_vs_hold") == "flat")
    unknown = sum(1 for t in trades if t.get("exit_vs_hold") == "unknown")
    realized_total = round(sum(_num(t.get("realized_pnl")) or 0.0 for t in trades), 2)
    if_deltas = [_num(t.get("if_held_delta_usd")) for t in trades]
    if_deltas = [d for d in if_deltas if d is not None]
    if_all = round(sum(if_deltas), 2) if if_deltas else None

    by_class = {}
    for t in trades:
        cls = "equity/index" if t.get("dex") == "xyz" else "crypto"
        b = by_class.setdefault(cls, {"trade_count": 0, "exits_beat_holding": 0, "exits_worse": 0,
                                      "realized_pnl_total": 0.0})
        b["trade_count"] += 1
        if t.get("exit_vs_hold") == "beat":
            b["exits_beat_holding"] += 1
        elif t.get("exit_vs_hold") == "worse":
            b["exits_worse"] += 1
        b["realized_pnl_total"] = round(b["realized_pnl_total"] + (_num(t.get("realized_pnl")) or 0.0), 2)

    return {
        "trade_count": trade_count,
        "exits_beat_holding": beat,
        "exits_worse": worse,
        "exits_flat": flat,
        "exits_unknown": unknown,           # price missing → couldn't compare (honest sourcing)
        "realized_pnl_total": realized_total,
        "if_all_reclosed_now_total": if_all,   # counterfactual aggregate — CONTEXT, NEVER a projection
        "by_asset_class": by_class,
    }


# ──────────────────────────────────────────────────────────────── book vs market (leaderboard_get_markets)
def _extract_movers(markets):
    """Extract the top movers from a leaderboard_get_markets response. Per the hyperfeed-markets guide the
    `markets` array has one entry per token+dex+direction; each entry carries `token`, `dex`, `direction`,
    `pct_of_top_traders_gain` (0-100), `token_price_change_pct_15m/_1h/_4h`, `is_dominant_direction`,
    `trader_count`. We keep ONE entry per token+dex (the dominant direction) and rank by the biggest
    absolute price move (4h → 1h → 15m, whichever is present) — that's 'what moved.'"""
    rows = markets if isinstance(markets, list) else _field(markets, "markets", "data", default=[])
    if not isinstance(rows, list):
        return []
    best = {}   # (token, dex) → chosen entry
    for m in rows:
        if not isinstance(m, dict):
            continue
        token = _field(m, "token", "coin", "asset")
        if not token:
            continue
        dex = _field(m, "dex", default="") or ""
        key = (str(token), str(dex))
        dominant = bool(_field(m, "is_dominant_direction", default=False))
        # prefer the dominant-direction entry; else keep the first seen (both-direction handling per guide)
        if key not in best or (dominant and not best[key][1]):
            best[key] = (m, dominant)
    movers = []
    for (token, dex), (m, _dom) in best.items():
        # 'what moved' = the biggest price move over the window (4h preferred, then 1h, then 15m)
        pct = None
        for k in ("token_price_change_pct_4h", "token_price_change_pct_1h", "token_price_change_pct_15m"):
            v = _num(m.get(k))
            if v is not None:
                pct = v
                break
        asset_class = "equity/index" if (dex == "xyz" or str(token).startswith("xyz:")) else "crypto"
        movers.append({
            "asset": token,
            "asset_class": asset_class,
            "dex": dex,
            "pct": round(pct, 2) if pct is not None else None,
            "direction": _field(m, "direction", default=None),
            "smart_money_pct": round(_num(m.get("pct_of_top_traders_gain")), 2)
                if _num(m.get("pct_of_top_traders_gain")) is not None else None,
            "trader_count": _field(m, "trader_count", "traderCount", default=None),
        })
    # rank by biggest absolute move (that's "what moved"); Nones sink to the bottom
    movers.sort(key=lambda x: abs(x["pct"]) if x["pct"] is not None else -1, reverse=True)
    return movers[:TOP_MOVERS_CAP]


def book_vs_market(client, trades, strategies, meta, want_market):
    """The 'what did I miss this week' gap. ONE leaderboard_get_markets call → top movers, crossed against
    the assets the user actually held/traded this window. gaps = movers the book had NO exposure to.
    Fail-open: on any error → empty structure + a meta.warnings note (never crashes, never invents)."""
    empty = {"top_movers": [], "participation": [], "gaps": [], "window": None}
    if not want_market:
        return empty
    try:
        raw = _ok(client.mcp_call("leaderboard_get_markets", limit=100, timeout=20))
    except Exception as e:  # noqa — fail-open
        meta.setdefault("warnings", []).append(f"leaderboard_get_markets failed: {e}; book-vs-market skipped")
        return empty
    if raw is None:
        meta.setdefault("warnings", []).append("leaderboard_get_markets returned no data; book-vs-market skipped")
        return empty
    movers = _extract_movers(raw)
    window = _field(raw, "window", default=None) if isinstance(raw, dict) else None

    # the assets the book touched this window: closed trades + any strategy the trade came from. Normalize
    # xyz: prefixes so a mover "TSLA"/dex "xyz" matches a book asset "xyz:TSLA".
    def _norm(a):
        return str(a or "").upper().replace("XYZ:", "")
    held = {}   # norm asset → the side the book was on
    for t in trades:
        held[_norm(t.get("asset"))] = t.get("direction")

    participation, gaps = [], []
    for mv in movers:
        na = _norm(mv["asset"])
        was_held = na in held
        side = held.get(na)
        # aligned = the book's side agreed with the move (long into an up move / short into a down move)
        aligned = None
        if was_held and mv["pct"] is not None and side is not None:
            aligned = (side == "long" and mv["pct"] > 0) or (side == "short" and mv["pct"] < 0)
        participation.append({
            "asset": mv["asset"], "asset_class": mv["asset_class"], "pct": mv["pct"],
            "held": was_held, "side": side, "aligned": aligned,
        })
        if not was_held:
            gaps.append({"asset": mv["asset"], "asset_class": mv["asset_class"], "pct": mv["pct"],
                         "smart_money_pct": mv.get("smart_money_pct")})
    return {"top_movers": movers, "participation": participation, "gaps": gaps, "window": window}


# ──────────────────────────────────────────────────────────────── per-strategy read (judged vs mandate)
def _strategy_reads(trades, strategies):
    """Per strategy: {label, mandate, closed_trade_count, realized_pnl, on_mandate_note}. Realized PnL is
    EVIDENCE for the mandate verdict, not the headline — the narrator judges each strategy against its OWN
    mandate (guardrail 5: don't grade a deliberate book against a momentum benchmark)."""
    by_wallet = {}
    for t in trades:
        w = str(t.get("strategy_wallet") or "").lower()
        b = by_wallet.setdefault(w, {"count": 0, "pnl": 0.0})
        b["count"] += 1
        b["pnl"] = round(b["pnl"] + (_num(t.get("realized_pnl")) or 0.0), 2)
    out = []
    for s in strategies:
        w = str(s.get("wallet") or "").lower()
        agg = by_wallet.get(w, {"count": 0, "pnl": 0.0})
        mandate = s.get("mandate")
        if mandate is None:
            note = "no mandate on file (runtime registry absent) — judge against the trades, not a benchmark"
        elif agg["count"] == 0:
            note = "no closed trades in this window — often by design (waiting for its signal), not a defect"
        else:
            note = "judge these closed trades against THIS mandate, not last window's winners"
        out.append({
            "label": s.get("label"),
            "wallet": s.get("wallet"),
            "mandate": mandate,
            "dsl": s.get("dsl"),                 # the levers a fix routes to
            "closed_trade_count": agg["count"],
            "realized_pnl": agg["pnl"],
            "on_mandate_note": note,
        })
    return out


# ──────────────────────────────────────────────────────────────── orchestration
def _window_bounds(window_days, now_ms=None):
    now_ms = now_ms if now_ms is not None else int(time.time() * 1000)
    since_ms = now_ms - int(window_days * 86400 * 1000)
    return since_ms, now_ms


def run(client, window_days=WINDOW_DEFAULT_DAYS, last_n=None, want_market=True, now_ms=None):
    """Orchestrate the review. Everything read-guarded + fail-open: partial data → valid JSON +
    meta.warnings/meta.degraded. `last_n` (a trade-count cap) coexists with the window — 'last N trades'
    still respects the window as the outer bound."""
    meta = {"warnings": [], "sources": ["discovery_get_trader_history", "ratchet_stop_list",
                                        "market_get_asset_data", "leaderboard_get_markets"], "degraded": None}
    since_ms, until_ms = _window_bounds(window_days, now_ms=now_ms)
    label = f"last {last_n} closed trades" if last_n else f"last ~{window_days}d"
    window = {"from": since_ms, "to": until_ms, "label": label, "window_days": window_days,
              "last_n": last_n}
    meta["window"] = window

    strategies = fetch_strategies(client, meta)
    trades = _collect_trades(client, strategies, meta, since_ms, until_ms, last_n, want_market)
    timing = _timing_summary(trades)
    bvm = book_vs_market(client, trades, strategies, meta, want_market)
    strat_reads = _strategy_reads(trades, strategies)

    meta["strategy_count"] = len(strategies)
    meta["trade_count"] = len(trades)
    if not strategies:
        meta["degraded"] = "no strategies — check the token is USER-scoped"
    elif not trades:
        meta["degraded"] = "no closed trades in the window (or trade history unavailable)"

    return {
        "window": window,
        "trades": trades,                 # per closed trade, process-input fields attached
        "timing_summary": timing,         # PROCESS-framed counts — LEAD with these
        "book_vs_market": bvm,            # the honest 'what did I miss' gap
        "strategies": strat_reads,        # each judged vs its OWN mandate
        "meta": meta,
    }


# ──────────────────────────────────────────────────────────────── CLI
def _dry(client):
    out = {}
    for label, tool, kw in (("strategy_list", "strategy_list", {"status": ["ACTIVE"]}),
                            ("leaderboard_get_markets", "leaderboard_get_markets", {"limit": 100})):
        try:
            out[label] = client.mcp_call(tool, timeout=20, **kw)
        except Exception as e:  # noqa
            out[label] = {"error": str(e)}
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="senpi improve-trades engine (retrospective review + coaching)")
    ap.add_argument("--window", type=float, default=WINDOW_DEFAULT_DAYS,
                    help="review window in days (default ~7)")
    ap.add_argument("--last", type=int, default=None,
                    help="cap to the last N closed trades per wallet (still within the window)")
    ap.add_argument("--no-market", action="store_true",
                    help="skip the current-price + book-vs-market pull")
    ap.add_argument("--fixture", help="offline: path to a recorded MCP-response map (tests only)")
    ap.add_argument("--dry", action="store_true", help="dump raw MCP responses for schema debugging")
    args = ap.parse_args(argv)

    if args.fixture:
        try:
            with open(args.fixture) as f:
                client = _FixtureClient(json.load(f))
        except Exception as e:  # noqa
            print(json.dumps({"trades": [], "meta": {"error": f"fixture load failed: {e}"}}))
            return 1
    else:
        try:
            client = _get_client()
        except Exception as e:  # noqa
            print(json.dumps({"trades": [], "meta": {"error": f"mcp client init failed: {e}"}}))
            return 1

    if args.dry:
        print(json.dumps(_dry(client), ensure_ascii=False, indent=2, default=str))
        return 0

    try:
        result = run(client, window_days=args.window, last_n=args.last, want_market=not args.no_market)
    except Exception as e:  # noqa
        print(json.dumps({"trades": [], "meta": {"error": f"engine failure: {e}"}}))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
