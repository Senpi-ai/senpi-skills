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
import concurrent.futures
import datetime
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
CLOSED_HISTORY_PULL = 100    # closed positions to pull per wallet before the window filter
WINDOW_DEFAULT_DAYS = 7      # the default review window
TOP_MOVERS_CAP = 12          # cap the book-vs-market movers surfaced
HL_INFO_URL = "https://api.hyperliquid.xyz/info"   # public, no-auth — durable closed-trade recovery
HL_FILLS_CAP = 2000          # userFills returns the most recent N fills; ample for any review window

# The runtime registers every deployed strategy in installed_runtimes.json in the state dir — the
# UNIVERSAL source of a strategy's mandate (the runtime.yaml `description`) + its DSL ladder. Reused
# verbatim in spirit from senpi-portfolio (the extractions there are already correct).
STATE_DIR_ENV = "SENPI_STATE_DIR"
REGISTRY_FILENAME = "installed_runtimes.json"

# ── Telemetry (the runtime event log) — ENRICHES discovery trades, never becomes the trade list ──
# Onchain data → discovery (the trade list, prices, realized PnL, fees, timing, direction). Runtime/agent
# events → telemetry (the per-strategy on-disk event ring, read via `openclaw senpi events`). Telemetry
# fills each discovery trade's EXIT REASON and, as a standalone stream, the blocked/rejected signal cohort
# ("what did I miss"). It NEVER reconstructs a trade or re-derives a price/PnL — discovery OWNS those.
EVENTS_FIXTURE_ENV = "SENPI_EVENTS_FIXTURE"   # offline test hook: JSON {"<runtime_id>": [entries…]}
EVENTS_PULL = 500                             # events to pull per runtime before matching (recent ring)
# `openclaw senpi events` spawnSyncs a SECOND `openclaw gateway call` process, so this timer wraps two CLI
# boots + the read — a HEALTHY fetch on a loaded host can take several seconds (it exceeded 8s during the
# incident). Keep it generous; the current-book-only guard already bounds worst-case wall to ~ceil(N/8)×this.
EVENTS_CALL_TIMEOUT_S = 8                      # per-runtime event shell-out timeout (wraps double CLI boot + read)
EXIT_MATCH_WINDOW_MS = 120000.0               # ±2 min asset+time fallback when there's no order_id
_EXIT_EVENT_NAMES = ("dsl.closed", "position.closed")
_MISSED_RESULTS = ("rejected", "blocked")     # signal.outcome results that never became a trade

# ── telemetry-derived quick-action aggregations (all computed from the SAME fetched events + trades[]) ──
# The 6 telemetry quick actions ("shaken out too early", "what did my limits block", "where am I leaking",
# "fees maker vs taker", "why is [strategy] losing") reuse the events already pulled during enrichment — no
# re-fetch. Every aggregation is fail-open: no events → empty aggregate, never a crash.
#
# The DSL terminal enum (the telemetry `close_reason`, from references/event-log.md). A trade's exit lands
# in ONE of these; the ratchet fallback's SL_TRIGGERED/MANUAL_CLOSE/LIQUIDATED/ADL and the honest UNKNOWN
# also bucket here (whatever `exit_reason.terminal` holds). PREMATURE = the early/shaken-out cohort.
_PREMATURE_TERMINALS = ("trailing_floor", "weak_peak", "max_retrace")   # the "shaken out too early" bucket
_PREMATURE_TIER_MAX = 1        # a low tier_index (<=1) locked with a small roe reads as a premature lock too
_PREMATURE_ROE_MAX = 5.0       # "small roe" ceiling for the low-tier premature heuristic (high-water ROE %)
# leak events — protection gaps + failed orders + risk halts, scanned from the SAME entries stream.
_LEAK_ORDER_FAILED = "order.failed"
_LEAK_PROTECTION = ("dsl.sl_sync_failed", "dsl.handoff_failed")   # DSL couldn't sync/hand off the stop
_LEAK_PAUSED = "runtime.paused"                                  # a risk halt (with its reason)
_LEAK_SAMPLE_CAP = 5           # samples kept per leak category (counts are exact; samples are illustrative)
_FILL_EVENT_NAME = "order.filled"

# CURRENT book = strategies still in play. Only these get a per-strategy VERDICT (mandate/DSL/on_mandate).
# Everything else (CLOSED, INACTIVE, ARCHIVED, …) is HISTORY: its trades stay in trades[] for the timing
# review (attributed by label), but it NEVER gets a "consolidate/kill/fix" verdict and its absent mandate
# is EXPECTED (deregistered because closed), not a bug. See SKILL.md "current vs closed" rule.
CURRENT_STATUSES = ("ACTIVE", "PAUSED")


def _is_current(status):
    """True when a strategy status is part of the CURRENT book (active/paused). Any other status
    (CLOSED, INACTIVE, …) → historical. Case/space tolerant; a missing status defaults to current
    (an ACTIVE-by-default strategy row that omitted the field)."""
    s = str(status or "ACTIVE").strip().upper()
    return s in CURRENT_STATUSES


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


# ── VENDORED, byte-identical in senpi-portfolio/scripts/portfolio.py and
# ── senpi-improve-trades/scripts/review.py — skills install standalone, so neither may import the other.
# ── senpi-portfolio/tests/test_name_reader_parity.py fails the moment the two copies drift.
def _first_written(d, *names, default=None):
    """The first of `names` whose value someone actually WROTE, as a stripped string.

    A boundary reader, so it coerces: `_field` hands back whatever the payload held, and a dict or a
    number landing in a name field renders as one. A container is never a name; a scalar is stringified.

    The strip is the load-bearing part. `_field` already skips a present-but-NULL key, so the null case
    survives on its own — this exists so that silence at one leg can never answer for the legs behind it
    NO MATTER the shape it arrives in, and so the two vendored copies answer identically."""
    if isinstance(d, dict):
        for n in names:
            v = d.get(n)
            if v is None or isinstance(v, (dict, list, tuple, set, bool)):
                continue                      # a container or a flag never names anything
            v = str(v).strip()
            if v:
                return v
    return default
# ── end vendored block


# ── VENDORED external-wallets reader, byte-identical in senpi-portfolio/scripts/portfolio.py,
# ── senpi-improve-trades/scripts/review.py and quant-desk/scripts/addresses.py — skills install
# ── standalone, so none may import another. senpi-portfolio/tests/test_name_reader_parity.py fails
# ── the moment the copies drift.
EXTERNAL_OK = "ok"
EXTERNAL_UNAVAILABLE = "unavailable"


def _external_wallets(me):
    """(status, wallets) from a `user_get_me` payload, outer `data` already stripped.

    The keys live inside `user`: `external_wallets_status` ("ok" | "unavailable") and, only when ok,
    `external_wallets` [{address, label, added_at, access}]. status is "ok" or "unavailable";
    wallets is a list only when status is "ok", else None. An ABSENT status key (an MCP older than
    saved wallets) is "unavailable", never [] — unknown is never empty, so this never reads a
    missing key with a default. `access` is the MCP's read-only line, carried verbatim."""
    user = me.get("user") if isinstance(me, dict) and isinstance(me.get("user"), dict) else me
    if not isinstance(user, dict) or user.get("external_wallets_status") != EXTERNAL_OK:
        return EXTERNAL_UNAVAILABLE, None
    rows = user.get("external_wallets")
    if not isinstance(rows, list):
        return EXTERNAL_UNAVAILABLE, None
    wallets = []
    for w in rows:
        if isinstance(w, dict) and isinstance(w.get("address"), str) and w["address"].strip():
            wallets.append({"address": w["address"].strip().lower(), "label": w.get("label"),
                            "added_at": w.get("added_at"), "access": w.get("access")})
    return EXTERNAL_OK, wallets
# ── end external-wallets reader


# ── VENDORED Senpi main-wallet reader, byte-identical in senpi-portfolio/scripts/portfolio.py,
# ── senpi-improve-trades/scripts/review.py and quant-desk/scripts/addresses.py — skills install
# ── standalone, so none may import another. senpi-portfolio/tests/test_name_reader_parity.py fails
# ── the moment the copies drift: every skill values the main wallet with ONE rule, so their managed
# ── subtotals reconcile.
MAIN_WALLET_LABEL = "Senpi main wallet"
MAIN_WALLET_STABLES = ("USDC", "USDC.E", "USDT")


def _main_wallet_address(me):
    """The Senpi main (embedded) wallet's address from a `user_get_me` payload (outer `data` already
    stripped) — the first wallet whose `walletType` is "embedded". None when the payload names none."""
    if not isinstance(me, dict):
        return None
    user = me.get("user") if isinstance(me.get("user"), dict) else {}
    wallets = me.get("wallets") or user.get("wallets") or []
    for w in wallets if isinstance(wallets, list) else []:
        if not isinstance(w, dict):
            continue
        kind = w.get("walletType") if w.get("walletType") is not None else w.get("type")
        if str(kind if kind is not None else "").lower() == "embedded":
            return w.get("walletAddress") if w.get("walletAddress") is not None else w.get("address")
    return None


def _main_wallet_value(p):
    """The main wallet's idle cash from ONE `account_get_portfolio` payload (forceFetch, outer `data`
    stripped): perps USDC (`total_in_hyperliquid`) + Hyperliquid spot USDC + EVM stablecoins. None when
    the payload isn't a dict (a failed read — unknown, never $0); a dict reads its fields, 0 when absent.

    GetPortfolioV3 nests the fields under `portfolio`; the idle field is `total_in_hyperliquid` (the old
    `total_usdc_in_hyperliquid` is a harmless fallback); spot is NOT inside it (omitting it under-reports
    the idle by exactly the spot balance); a token row's USD value of exactly 0 is the API's
    zero-as-missing sentinel, so it falls back to `formattedBalance` × `tokenPriceInUSD` (a 0 price → 1)."""
    if not isinstance(p, dict):
        return None
    if isinstance(p.get("portfolio"), dict):
        p = p["portfolio"]

    def num(d, *keys, default=0.0):
        for k in keys:
            if isinstance(d, dict) and d.get(k) is not None:
                try:
                    return float(d[k])
                except (TypeError, ValueError):
                    continue
        return default

    out = {"idle_hl_usdc": num(p, "total_in_hyperliquid", "total_usdc_in_hyperliquid"),
           "spot_usd": num(p, "total_spot_usd_in_hyperliquid"), "evm_usdc": []}
    evm = 0.0
    for tb in p.get("token_balances") if isinstance(p.get("token_balances"), list) else []:
        if not isinstance(tb, dict):
            continue
        sym = tb.get("symbol") if tb.get("symbol") is not None else tb.get("tokenSymbol")
        if str(sym if sym is not None else "").upper() not in MAIN_WALLET_STABLES:
            continue
        amt = num(tb, "usdValue", "usd_value", "amountUsd", "balanceUsd", "balanceInUSD", "amount")
        if amt == 0.0:
            amt = num(tb, "formattedBalance", "amount") * (num(tb, "tokenPriceInUSD", default=1.0) or 1.0)
        chain = next((tb[k] for k in ("chain", "network", "chainName") if tb.get(k) is not None), "EVM")
        if amt:
            out["evm_usdc"].append({"chain": chain, "usd": round(amt, 2)})
            evm += amt
    out["idle_total"] = round(out["idle_hl_usdc"] + out["spot_usd"] + evm, 2)
    return out
# ── end main-wallet reader


def _strategy_label(s):
    """What to CALL a strategy — the label every trade, event and rollup is attributed by, and the KEY
    the `by_strategy` rollups bucket on.

    `strategyName` first: it is the strategy's own name (`<id>-<instance>` for a package deploy), so it is
    the only field that tells the `long` sleeve from the `short` one. `tradingStrategyName` is NOT a second
    name — `strategy_list` sets it to `strategyMetadata.skillName` verbatim, so it is the PACKAGE id,
    identical across every instance of a package. Bucketing by it merged two sleeves into one
    `dsl_close_reason_mix.by_strategy` entry — and since the DSL ladder is PER INSTANCE, that merged bucket
    routed a "fix" at two different ladders. It stays as the fallback because `strategyName` is nullable by
    mechanism (no name input on `strategy_create`, optional on `strategy_create_custom_strategy`).

    Two live wallets can still share a label — sleeves of an UNNAMED strategy both fall back to the package
    id, and a user-named `cub` collides with an unnamed strategy from package `cub`. That is why the row
    also carries `skill_name` + `group`: the label is what a bucket is called, never the proof of what is
    one strategy.

    Chain-identical to senpi-portfolio's `name` and senpi-strategy-ops' `strategy_name`; the READERS are
    not identical (ops' dispatches through a case-insensitive `dig` and does not strip) — see
    tests/test_name_reader_parity.py for which two copies are held byte-identical and why the third isn't."""
    return _first_written(s, "strategyName", "tradingStrategyName", "name", default="strategy")


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
    user-authored strategies, not just catalog templates. Read-guarded + fail-open: any problem → ({}, {},
    None). A meta.warnings note is added ONLY for a real parse error, not an absent registry file.

    Also captures each entry's runtime **`id`** → a `wallet_lower → runtime_id` map, the KEY the event-log
    CLI is addressed by (`openclaw senpi events --runtime <id>`). Telemetry enrichment (exit reasons +
    missed signals) needs this id per wallet; it's harvested here where we already hold each registry entry.
    Returns (profiles_map, runtime_id_map, source)."""
    state_dir = _resolve_state_dir()
    meta["state_dir"] = state_dir          # surfaced for debugging path issues
    path = os.path.join(state_dir, REGISTRY_FILENAME)
    if not os.path.isfile(path):          # absent registry is normal, not an error
        return {}, {}, None
    try:
        with open(path) as fh:
            raw = json.load(fh)
    except Exception as e:  # noqa — a corrupt registry is a real parse error worth surfacing
        meta.setdefault("warnings", []).append(
            f"runtime registry unreadable ({e}); mandates unavailable")
        return {}, {}, None
    entries = raw.get("runtimes", raw) if isinstance(raw, dict) else raw
    out = {}
    id_map = {}                            # wallet_lower → runtime id (the event-log CLI address)
    for entry in (entries if isinstance(entries, list) else []):
        if not isinstance(entry, dict):
            continue
        wallet = entry.get("wallet")
        if not wallet:
            continue
        rid = _field(entry, "id", "runtimeId", "runtime_id")   # the --runtime <id> for the event log
        if rid:
            id_map[str(wallet).lower()] = rid
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
    return out, id_map, "registry"


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
    enumeration misses exactly the trades a "review my last trades / what did I miss" is asking about. This
    is purely the TRADE-SOURCE set: the CLOSED rows exist so their trades land in trades[]. Downstream the
    per-strategy VERDICT is partitioned CURRENT-only (see _strategy_reads / _closed_strategy_rollup and the
    `status` field on each strategy) — a closed strategy is HISTORY, never a live-book verdict.
    Mandate + DSL come from the deployed runtime.yaml registry (universal), keyed by wallet — None for a
    closed strategy whose runtime was deregistered (the trade is still reviewed; exit attribution still
    comes from the ratchet record; the absent mandate is EXPECTED, not a bug). Fail-open: []."""
    try:
        sl = _ok(client.mcp_call("strategy_list", status=["ACTIVE", "PAUSED", "CLOSED"], timeout=20))
    except Exception as e:  # noqa
        meta.setdefault("warnings", []).append(f"strategy_list failed: {e}")
        return []
    rows = sl if isinstance(sl, list) else _field(sl, "strategies", "data", default=[])
    registry, runtime_ids, registry_src = load_runtime_registry(meta)   # wallet_lower → profile / runtime id
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
            "label": _strategy_label(s),
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
            # the runtime id the event-log CLI is addressed by (`openclaw senpi events --runtime <id>`);
            # None for a closed/deregistered strategy with no ring → telemetry enrichment skips it.
            "runtime_id": runtime_ids.get(str(wallet).lower()),
        })
    if not registry_src and strategies:
        meta.setdefault("warnings", []).append(
            "runtime registry absent — mandates + DSL levers unavailable; review runs on trades only")
    return strategies


# ──────────────────────────────────────────────────── saved wallets (read-only, traded by hand)
EXTERNAL = "external"          # review-set entry `kind` for a saved wallet
EXTERNAL_STATUS = "EXTERNAL"   # its `status` — never ACTIVE/PAUSED, so no Senpi-only read touches it
MANUAL_TRADE = "MANUAL_TRADE"  # exit_reason.terminal for a saved wallet's closed trade
EXTERNAL_SOURCE = "external_wallet"
EXTERNAL_STATE_TIMEOUT_S = 25  # moxie answers within ~6.5 s; the MCP's own moxie timeout is 20 s — wait past it
USER_GET_ME_TIMEOUT_S = 22     # user_get_me now waits on moxie's saved-wallets read (MCP 20 s timeout)


def _is_external(entry):
    return isinstance(entry, dict) and entry.get("kind") == EXTERNAL


def _senpi_only(entries):
    return [e for e in entries if not _is_external(e)]


def _short_addr(addr):
    a = str(addr or "")
    return f"{a[:6]}…{a[-4:]}" if len(a) > 10 else a


def fetch_external_review(client, meta):
    """The user's SAVED wallets as review-set entries — the ones they added in Your wallets and trade
    by hand. Read-only: Senpi can analyze them and cannot place, change or cancel orders on them.

    Each entry carries `kind: "external"`, `status: "EXTERNAL"` and None for every Senpi-strategy
    field (strategy_id / runtime_id / mandate / dsl), so the ratchet and telemetry reads skip it and no
    DSL or strategy-tuning lever is ever routed at it. `state` is `account_get_external_wallets`' object
    verbatim (the single producer of its open positions + protection). Sets
    meta.external_wallets_status: "unavailable" (absent key on an older MCP, or a failed read) is
    unknown, never "no saved wallets"."""
    try:
        raw = client.mcp_call("user_get_me", timeout=USER_GET_ME_TIMEOUT_S)
        if isinstance(raw, dict) and raw.get("success") is False:   # answered as a failure, not raised
            meta["user_get_me_failed"] = True
        me = _ok(raw) or {}
    except Exception as e:  # noqa — unknown, never empty
        meta.setdefault("warnings", []).append(f"user_get_me failed: {e}; saved wallets unavailable")
        meta["user_get_me_failed"] = True                     # the main wallet is then unknown, never absent
        me = {}
    status, wallets = _external_wallets(me)
    meta["external_wallets_status"] = status
    meta["main_wallet_address"] = _main_wallet_address(me)   # the book's Senpi main wallet row
    if status != EXTERNAL_OK or not wallets:
        return []
    states = None
    try:
        resp = _ok(client.mcp_call("account_get_external_wallets", timeout=EXTERNAL_STATE_TIMEOUT_S)) or {}
        rows = _field(resp, "external_wallets", default=None) if isinstance(resp, dict) else None
        if not isinstance(rows, list):
            raise ValueError("no external_wallets list in the response")
        states = {str(_field(r, "address", default="")).lower(): r.get("state")
                  for r in rows if isinstance(r, dict)}
    except Exception as e:  # noqa — closed trades still come from fills; only the open book is unknown
        meta.setdefault("warnings", []).append(
            f"account_get_external_wallets failed: {e}; open positions on saved wallets unknown")
    out = []
    for w in wallets:
        st = states.get(w["address"]) if states is not None else None
        st = st if isinstance(st, dict) else None
        out.append({
            "kind": EXTERNAL,
            "label": w.get("label") or _short_addr(w["address"]),
            "wallet": w["address"],
            "access": w.get("access"),
            "strategy_id": None, "skill_name": None, "status": EXTERNAL_STATUS, "group": None,
            "mandate": None, "dsl": None, "runtime_id": None,
            "state": st,
            "state_read": "unavailable" if st is None else ("error" if st.get("readError") else "ok"),
        })
    return out


MAIN_WALLET_TIMEOUT_S = 25     # account_get_portfolio with forceFetch — the read senpi-portfolio makes


def fetch_main_wallet(client, address, meta):
    """The Senpi main wallet as the book reads it: its idle cash from ONE `account_get_portfolio`
    (forceFetch) valued by the vendored main-wallet reader — the same number senpi-portfolio calls
    "idle in your Senpi main wallet", so the managed subtotals reconcile. A failed read is
    `value_usd: None` (unknown, never $0) and the row is named in the total's excludes. None (no row)
    only when nothing says there is one: user_get_me read fine, named no embedded wallet, and the
    portfolio read failed."""
    try:
        p = _ok(client.mcp_call("account_get_portfolio", forceFetch=True, strategyStatus="ALL",
                                timeout=MAIN_WALLET_TIMEOUT_S))
    except Exception as e:  # noqa — unknown, never $0
        meta.setdefault("warnings", []).append(f"account_get_portfolio failed: {e}; Senpi main wallet value unknown")
        p = None
    v = _main_wallet_value(p)
    if v is None and address is None and not meta.get("user_get_me_failed"):
        return None
    return {"address": address, "value_usd": v["idle_total"] if v else None, "detail": v}


def fetch_review_set(client, meta):
    """Everything the review covers: the Senpi strategies (all statuses) followed by the saved
    wallets. Senpi-only reads filter with `_senpi_only`; saved rows carry `kind: "external"`."""
    return fetch_strategies(client, meta) + fetch_external_review(client, meta)


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
                                 strategy_wallet_address=wallet, status="ALL", timeout=10))
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


# ─────────────────────────────────────────── telemetry: the runtime event log (ENRICHES discovery trades)
# The rule (from the runtime team): onchain data → discovery; runtime/agent events → telemetry. Discovery
# OWNS the trade list + every onchain fact (asset, entry/exit px, realized PnL, fees, timing, direction,
# closedOrderId). Telemetry ENRICHES those discovery trades with runtime-side facts discovery can't have:
# the EXIT REASON (`dsl.closed` / `position.closed` close_reason + tier + roe) and — as a standalone
# stream — the blocked/rejected `signal.outcome` cohort ("what did I miss"). It never re-derives a
# price/PnL and never becomes the trade list. Every read is guarded and FAIL-OPEN to discovery.
def _iso8601(ms):
    """A `--since` value the event-log CLI accepts (ISO 8601 UTC). None → None (CLI then defaults)."""
    n = _num(ms)
    if n is None:
        return None
    try:
        dt = datetime.datetime.fromtimestamp(n / 1000.0, tz=datetime.timezone.utc)
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    except (ValueError, OverflowError, OSError):
        return None


def _fetch_events(runtime_id, since_ms, meta):
    """Read a runtime's on-disk event ring → a list of event dicts ({name, ts, attrs, …}). FAIL-OPEN:
    missing `openclaw` / non-zero exit / `unknown method: senpi.getEvents` (older build that lacks the RPC)
    / parse error → [] plus a ONE-TIME `meta.warnings` note; NEVER raises. The whole point is that
    telemetry absence degrades enrichment only — discovery still lists the trades.

    Mockable offline: if $SENPI_EVENTS_FIXTURE points at a JSON file `{"<runtime_id>": [entries…]}`, read
    that instead of shelling out (tests use this — NO subprocess in tests)."""
    # Short-circuit: once ANY runtime reported no CLI / `unknown method`, the whole build/host lacks the
    # event RPC — every further shell-out would just spawn a process to fail the same way (the 3-min
    # latency on a pre-event-log build). Skip them all after the first such failure.
    if not runtime_id or meta.get("_telemetry_dead"):
        return []
    fixture = os.environ.get(EVENTS_FIXTURE_ENV)
    if fixture:                              # offline path — no subprocess
        try:
            with open(fixture) as fh:
                data = json.load(fh)
            entries = data.get(str(runtime_id), []) if isinstance(data, dict) else []
            return [e for e in entries if isinstance(e, dict)]
        except Exception as e:  # noqa — a bad fixture is fail-open too
            _note_telemetry_unavailable(meta, f"events fixture unreadable ({e})")
            return []
    cmd = ["openclaw", "senpi", "events", "--runtime", str(runtime_id), "--json", "-l", str(EVENTS_PULL)]
    since_iso = _iso8601(since_ms)
    if since_iso:
        cmd += ["--since", since_iso]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=EVENTS_CALL_TIMEOUT_S)
    except FileNotFoundError:                # no `openclaw` on PATH (not a runtime host)
        meta["_telemetry_dead"] = True       # no CLI at all → every runtime fails; stop shelling out
        _note_telemetry_unavailable(meta, "openclaw CLI not found; exit reasons from ratchet fallback only")
        return []
    except subprocess.TimeoutExpired:        # this fetch was slow (double CLI boot + read under host load).
        # Fail-open for THIS strategy only — NO count-based circuit breaker: the parallel fan-out gives each
        # worker a private meta, so a cross-strategy timeout counter can't accumulate mid-fan-out anyway. The
        # real bound on total cost is the current-book-only guard below (closed strategies are never probed).
        _note_telemetry_unavailable(meta, "event-log read timed out")
        return []
    except Exception as e:  # noqa — other OS error → fail-open
        _note_telemetry_unavailable(meta, f"event-log read failed ({e})")
        return []
    if proc.returncode != 0:
        err = (proc.stderr or "")[:200]
        # older runtime build without the RPC → the CLI reports `unknown method: senpi.getEvents`
        if "unknown method" in err.lower() or "getevents" in err.lower():
            meta["_telemetry_dead"] = True    # build lacks the event RPC → every runtime fails; stop
            _note_telemetry_unavailable(meta, "runtime build predates event log (unknown method); "
                                              "exit reasons from ratchet fallback only")
        else:
            _note_telemetry_unavailable(meta, f"event-log read exit {proc.returncode} ({err.strip()})")
        return []
    try:
        parsed = json.loads(proc.stdout or "{}")
    except Exception as e:  # noqa — malformed JSON → fail-open
        _note_telemetry_unavailable(meta, f"event-log JSON parse failed ({e})")
        return []
    if not isinstance(parsed, dict) or parsed.get("ok") is False:
        _note_telemetry_unavailable(meta, "event-log returned not-ok; skipping enrichment")
        return []
    entries = parsed.get("entries")
    return [e for e in entries if isinstance(e, dict)] if isinstance(entries, list) else []


def _note_telemetry_unavailable(meta, msg):
    """One-time telemetry warning + mark meta so the source rollup can report unavailable/partial."""
    meta.setdefault("_telemetry_warned", False)
    if not meta["_telemetry_warned"]:
        meta.setdefault("warnings", []).append(f"telemetry: {msg}")
        meta["_telemetry_warned"] = True


def _attr(ev, *keys, default=None):
    """Read a dotted `senpi.*` attribute from an event's `attrs` map (fail-soft)."""
    attrs = ev.get("attrs") if isinstance(ev, dict) else None
    if isinstance(attrs, dict):
        for k in keys:
            if k in attrs and attrs[k] is not None:
                return attrs[k]
    return default


def _index_exit_events(events):
    """Build a matcher over a runtime's EXIT events (`dsl.closed`, `position.closed`) so a discovery trade
    can find how it exited. Two lookup lanes, matching the shapes in references/event-log.md:
      by_order_id: `senpi.order.id` → event   (exact; `position.closed` carries it, `dsl.closed` does not)
      by_asset:    UPPER(asset) → [(ts_ms, event)]   (the ±2-min asset+time fallback)
    Returns (by_order_id, by_asset). The event's attrs (close_reason / tier_index / current_roe) are read
    at match time, so we keep the raw event here."""
    by_order_id, by_asset = {}, {}
    for ev in events:
        name = str(ev.get("name") or "")
        if name not in _EXIT_EVENT_NAMES:
            continue
        oid = _attr(ev, "senpi.order.id")
        if oid:
            by_order_id[str(oid)] = ev
        asset = _attr(ev, "senpi.asset", "senpi.signal.asset")
        ts = _num(ev.get("ts"))
        if asset is not None and ts is not None:
            by_asset.setdefault(str(asset).upper(), []).append((ts, ev))
    return by_order_id, by_asset


def _exit_reason_from_event(ev):
    """Read a telemetry exit event into the exit_reason contract. Handles BOTH exit shapes:
      dsl.closed      → `senpi.dsl.close_reason`  (+ `senpi.dsl.tier_index`, `senpi.dsl.current_roe`)
      position.closed → `senpi.position.close_reason` (+ `senpi.position.roe`)
    `source:"telemetry"` marks it native (vs the reconstructed ratchet fallback)."""
    terminal = _attr(ev, "senpi.dsl.close_reason", "senpi.position.close_reason")
    tier_index = _attr(ev, "senpi.dsl.tier_index")
    roe = _attr(ev, "senpi.dsl.current_roe", "senpi.position.roe")
    return {
        "terminal": terminal,
        "tier_index": _num(tier_index) if tier_index is not None else None,
        "high_water_roe": _num(roe) if roe is not None else None,
        "source": "telemetry",
    }


def _match_exit_event(trade, by_order_id, by_asset):
    """Find the telemetry exit event for one discovery trade. Match priority (per the spec):
      1. EXACT order id — discovery `closed_order_id` == event `senpi.order.id`.
      2. else same ASSET + close_time within ±EXIT_MATCH_WINDOW_MS (~2 min), nearest in time.
    Returns the matched event dict or None (→ leave exit_reason as the honest ratchet/UNKNOWN fallback)."""
    oid = trade.get("closed_order_id")
    if oid and str(oid) in by_order_id:
        return by_order_id[str(oid)]
    asset = str(trade.get("asset") or "").upper()
    close_ms = _num(trade.get("close_time"))
    if not asset or close_ms is None:
        return None
    best, best_dist = None, None
    for ts, ev in by_asset.get(asset, []):
        dist = abs(ts - close_ms)
        if dist <= EXIT_MATCH_WINDOW_MS and (best_dist is None or dist < best_dist):
            best, best_dist = ev, dist
    return best


def _missed_signals_from_events(events, strategy_label):
    """The native 'what did I miss': `signal.outcome` events whose result is rejected/blocked — signals
    the runtime evaluated but that NEVER became a trade, with the granular reason_code. Discovery can't
    see these (they left no onchain trace); telemetry is the only source. One flat list, standalone from
    the discovery trade list (it never mixes into trades[])."""
    out = []
    for ev in events:
        if str(ev.get("name") or "") != "signal.outcome":
            continue
        result = str(_attr(ev, "senpi.outcome.result") or "").lower()
        if result not in _MISSED_RESULTS:
            continue
        score = _attr(ev, "senpi.signal.score")
        out.append({
            "asset": _attr(ev, "senpi.signal.asset", "senpi.asset"),
            "direction": _attr(ev, "senpi.signal.direction"),
            "score": _num(score) if score is not None else None,
            "result": result,
            "reason_code": _attr(ev, "senpi.outcome.reason_code"),
            "ts": _num(ev.get("ts")),
            "strategy_label": strategy_label,
        })
    return out


def _scan_leak_and_fill_events(events, strategy_label, leaks_acc, fills_acc):
    """Extend the SAME entries stream scan (no re-fetch) to harvest the leak + execution-quality events —
    'where am I leaking' and 'fees / maker vs taker'. Mutates the two accumulators in place:

      leaks_acc — a per-category rollup {order_failed, protection_gaps, risk_halts}, each {count, samples[]}:
        order.failed                          → a rejected/errored order (senpi.order.reason)  → $ never entered
        dsl.sl_sync_failed / dsl.handoff_failed → the DSL couldn't sync/hand off the stop      → a naked leg
        runtime.paused                        → a risk halt (senpi.pause.reason)               → trading stopped
      fills_acc — a maker/taker tally from `order.filled` (senpi.order.execution_as_maker → fee tier).

    Counts are EXACT; samples are capped (illustrative, not a ledger). Fail-soft on any odd event shape.
    NOTE (future authoritative fees): the maker/taker split is the *rate* signal; the authoritative fee $
    lives in the ledger via `order_id → execution_get_closed_position_details({closedOrderId})`. That is a
    per-order hook wired later — do NOT call it per-trade here (N calls, rate-limit risk); this stays a
    telemetry-only rate read."""
    for ev in events:
        name = str(ev.get("name") or "")
        if name == _LEAK_ORDER_FAILED:
            cat = leaks_acc["order_failed"]
            cat["count"] += 1
            if len(cat["samples"]) < _LEAK_SAMPLE_CAP:
                cat["samples"].append({
                    "asset": _attr(ev, "senpi.asset", "senpi.signal.asset"),
                    "reason": _attr(ev, "senpi.order.reason", "senpi.order.error_name"),
                    "ts": _num(ev.get("ts")), "strategy_label": strategy_label,
                })
        elif name in _LEAK_PROTECTION:
            cat = leaks_acc["protection_gaps"]
            cat["count"] += 1
            if len(cat["samples"]) < _LEAK_SAMPLE_CAP:
                cat["samples"].append({
                    "asset": _attr(ev, "senpi.asset", "senpi.signal.asset"),
                    "event": name,   # which protection step failed (sl_sync vs handoff)
                    "ts": _num(ev.get("ts")), "strategy_label": strategy_label,
                })
        elif name == _LEAK_PAUSED:
            cat = leaks_acc["risk_halts"]
            cat["count"] += 1
            if len(cat["samples"]) < _LEAK_SAMPLE_CAP:
                cat["samples"].append({
                    "reason": _attr(ev, "senpi.pause.reason", "senpi.runtime.pause_reason", "senpi.reason"),
                    "ts": _num(ev.get("ts")), "strategy_label": strategy_label,
                })
        elif name == _FILL_EVENT_NAME:
            as_maker = _attr(ev, "senpi.order.execution_as_maker")
            if as_maker is True:
                fills_acc["maker"] += 1
            elif as_maker is False:
                fills_acc["taker"] += 1
            else:
                fills_acc["unknown"] += 1   # fill event without the flag → don't guess the fee tier


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


def _direction(rec):
    """Side of a CLOSED trade, read only from a side label on the row. The real `discovery_get_trader_history`
    row labels the side in `type` ("Close Long" / "Close Short") and carries an UNSIGNED at-close `szi` —
    positive on a closed short too — so the sign of the size says nothing about the side. The label is on
    every row the feed returns. A label in `dir` / `side` / `direction` / `positionSide` / `type` containing
    "long" or "short" (any case) decides it, and nothing else is read: not the size's sign, not realized PnL
    against the price move, not a buy / sell side (on a closed row that can be the closing fill, which is the
    opposite side). No label → None (unknown)."""
    for name in ("dir", "side", "direction", "positionSide", "type"):
        label = str(_field(rec, name, default="") or "").strip().lower()
        if "short" in label:
            return "short"
        if "long" in label:
            return "long"
    return None


# ───────────────────── on-chain fill recovery (durable across strategy_close) ─────────────────────
HL_RETRY_BACKOFF_S = (1.0, 2.0)   # sleeps between the 3 tries of a rate-limited (429) / 5xx read
HL_RETRY_BUDGET_S = 10.0          # total retry sleep per review, across every thread — never stalls longer
_hl_retry = {"slept": 0.0, "lock": threading.Lock()}


def _reset_hl_retry_budget():
    with _hl_retry["lock"]:
        _hl_retry["slept"] = 0.0


def _take_hl_retry_sleep(s):
    """Reserve `s` seconds of the review's retry budget; False when it's spent (then fail open)."""
    with _hl_retry["lock"]:
        if _hl_retry["slept"] + s > HL_RETRY_BUDGET_S:
            return False
        _hl_retry["slept"] += s
        return True


def _hl_info(payload, meta, client=None, timeout=12):
    """POST the Hyperliquid Info API (public, no auth) — the same transport the DSL scripts use. Recovers a
    CLOSED strategy's trades that Senpi's discovery index has dropped: HL keys fills by wallet ADDRESS, so
    they survive a `strategy_close` (which only clears Senpi's own record). Offline/fixture-aware for tests
    (`_FixtureClient` serves a recorded `hl::<type>::<wallet>` entry). Fails OPEN → None.

    Hyperliquid rate-limits with HTTP 429 and a `null` body — that is a busy IP, not an answer, so a 429 or
    5xx is retried (3 tries, HL_RETRY_BACKOFF_S apart, within the review's HL_RETRY_BUDGET_S) before the
    read fails open. Any other non-200 is a failed read at once."""
    if client is not None and hasattr(client, "_r"):          # _FixtureClient — serve recorded HL response
        u = str(payload.get("user", "")).lower()
        key = f"hl::{payload.get('type')}::{u}"
        # a recorded [] is an ANSWER (no fills), not a miss — `or` would turn it into a failed read
        return client._r[key] if key in client._r else client._r.get(f"hl::{payload.get('type')}")
    status = None
    for attempt in range(len(HL_RETRY_BACKOFF_S) + 1):
        try:
            p = subprocess.run(
                ["curl", "-s", "-m", str(timeout), "-w", "\n%{http_code}", "-X", "POST", HL_INFO_URL,
                 "-H", "Content-Type: application/json", "-d", json.dumps(payload)],
                capture_output=True, text=True, timeout=timeout + 3)
        except Exception as e:  # noqa
            meta.setdefault("warnings", []).append(f"hl_info {payload.get('type')} failed: {e}")
            return None
        body, _, code = (p.stdout or "").rpartition("\n")
        status = int(code) if code.strip().isdigit() else None
        if p.returncode == 0 and status == 200:
            try:
                return json.loads(body) if body.strip() else None
            except ValueError as e:
                meta.setdefault("warnings", []).append(f"hl_info {payload.get('type')} failed: {e}")
                return None
        retryable = p.returncode == 0 and status is not None and (status == 429 or 500 <= status < 600)
        if not retryable or attempt >= len(HL_RETRY_BACKOFF_S):
            break
        wait = HL_RETRY_BACKOFF_S[attempt]
        if not _take_hl_retry_sleep(wait):
            break
        time.sleep(wait)
    meta.setdefault("warnings", []).append(
        f"hl_info {payload.get('type')} {str(payload.get('user', ''))[:8]} failed "
        f"(HTTP {status if status is not None else 'no response'}); read as unknown")
    return None


def _reconstruct_closed_from_fills(fills, since_ms, until_ms, cap):
    """Rebuild round-trip CLOSED trades from raw HL fills — the durable, close-independent source. HL gives
    per-fill `dir` ('Open/Close Long/Short'), px, sz, closedPnl, fee, time, oid. Walk fills per coin
    oldest→newest, FIFO-match each Close against prior Opens, and emit one trade per closed chunk with a
    size-weighted entry price. `realized_pnl` = HL's own closedPnl (authoritative — correct even when the
    matching open predates the window). Same trade shape as discovery, tagged source='onchain_fills'."""
    if not isinstance(fills, list):
        return []
    import collections
    fl = sorted((f for f in fills if isinstance(f, dict)), key=lambda f: _num(f.get("time")) or 0)
    lots = collections.defaultdict(collections.deque)   # coin -> deque of open lots {px, sz, time}
    trades = []
    for f in fl:
        coin = f.get("coin")
        d = str(f.get("dir") or "")
        sz = abs(_num(f.get("sz")) or 0.0)
        px = _num(f.get("px"))
        t = _ms(f.get("time"))
        if not coin or sz <= 0:
            continue
        if d.startswith("Open"):
            # fee_per_sz lets a later close claim this open leg's share. Without it a round trip
            # reports only the closing fill's fee — on the AVAX trade $77.07 against a true $99.92.
            lots[coin].append({"px": px, "sz": sz, "time": t,
                               "fee_per_sz": ((_num(f.get("fee")) or 0.0) / sz) if sz > 0 else 0.0})
        elif d.startswith("Close"):
            side = "long" if "Long" in d else ("short" if "Short" in d else None)
            remaining, entry_notional, matched, open_time = sz, 0.0, 0.0, t
            _close_fee = _num(f.get("fee")) or 0.0
            _open_fee_consumed = 0.0
            q = lots[coin]
            while remaining > 1e-9 and q:                # FIFO-match the closed size against open lots
                lot = q[0]
                take = min(remaining, lot["sz"])
                entry_notional += take * (lot["px"] or 0.0)
                _open_fee_consumed += take * (lot.get("fee_per_sz") or 0.0)
                matched += take
                open_time = lot["time"] or open_time
                lot["sz"] -= take
                remaining -= take
                if lot["sz"] <= 1e-9:
                    q.popleft()
            trades.append({
                "asset": coin,
                "direction": side,
                "size": sz,
                "leverage": None,                        # not carried on HL fills
                "entry_px": (entry_notional / matched) if matched > 0 else None,
                "exit_px": px,
                "realized_pnl": round(_num(f.get("closedPnl")) or 0.0, 2),   # HL's own realized — authoritative
                "margin_used": None,
                "open_time": open_time,
                "close_time": t,
                "closed_order_id": f.get("oid"),
                # `fee` ALREADY INCLUDES `builderFee` — do not add them. Shown across two wallets on
                # DIFFERENT loyalty tiers: one at builder 5.00 bps had fee 9.32 taker / 6.44 maker,
                # one at builder 4.00 bps had 8.32 / 5.44. Subtract the builder leg and both give the
                # same exchange rate (4.32 / 1.44), and `fee` differs by exactly the builder delta.
                # `builderFee` is a breakdown line, not a second charge. Summing them overstated one
                # AVAX round trip $99.92 -> $153.53.
                # NOTE the builder rate is NOT a constant — it falls with the user's points tier
                # (0.05% Bronze down to 0.025% Legend). Never hardcode it; `user_get_senpi_points`
                # returns the user's own rate.
                "fee": round(_close_fee + _open_fee_consumed, 4),
                "source": "onchain_fills",
            })
    out = []
    for tr in trades:
        ct = tr["close_time"]
        if since_ms is not None and ct is not None and ct < since_ms:
            continue
        if until_ms is not None and ct is not None and ct > until_ms:
            continue
        out.append(tr)
    out.sort(key=lambda t: _num(t.get("close_time")) or 0, reverse=True)
    return out[:cap] if cap else out


def fetch_closed_trades(client, wallet, since_ms, until_ms, cap, meta, strict=False):
    """Read-guarded closed-trade list for one strategy wallet, filtered to the review window.

    PRIMARY: `discovery_get_trader_history` (closedPositions[] round-trips — works for CURRENT strategies).
    FALLBACK — the closed-strategy trap: `strategy_close` clears Senpi's discovery index, so a CLOSED
    strategy returns EMPTY here even though it traded. The fills still exist ON-CHAIN (HL keys them by
    wallet ADDRESS, not by Senpi's strategy record), so on an empty discovery result we recover the real
    round-trips from HL `userFills` and tag them source='onchain_fills'. `realized_pnl` then comes from HL's
    own closedPnl — so a closed book is never misread as "no trades" or "drained to $0" (that $0 is the
    withdrawal on close). Empty HL fills too ⇒ genuinely no trades. Fails OPEN → [].

    `strict=True` (saved wallets, where discovery is usually empty and fills are the normal path):
    a FAILED fills read returns None and records the wallet in meta.closed_trades_unknown — unknown is
    never "no trades". Any wallet whose fills read hit HL's HL_FILLS_CAP rows is recorded in
    meta.fills_capped: coverage is partial, so its totals read as "at least".
    """
    try:
        h = _ok(client.mcp_call("discovery_get_trader_history", trader_address=wallet,
                                sort_by="CLOSED_TIME", sort_direction="DESC",
                                limit=CLOSED_HISTORY_PULL, timeout=12))
    except Exception as e:  # noqa
        meta.setdefault("warnings", []).append(f"trader_history {wallet[:8]} failed: {e}")
        h = None
    rows = []
    if h is not None:
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
        entry_px = _num(_field(p, "entryPx", "entry_px"))
        exit_px = _num(_field(p, "exitPx", "exit_px"))
        pnl = round(_f(p, "realizedPnl", "realized_pnl", default=0.0), 2)
        trades.append({
            "asset": _field(p, "coin", "coinDisplayName", "asset"),
            "direction": _direction(p),   # the row's side label; never the size's sign or PnL vs the move
            "size": abs(szi),
            "leverage": _f(lev, "value", default=None) if isinstance(lev, dict) else _num(lev),
            "entry_px": entry_px,
            "exit_px": exit_px,
            "realized_pnl": pnl,
            # `totalFees` = totalHyperliquidFees + totalBuilderFees — discovery SPLITS the two where
            # HL's raw `fee` is already inclusive (a sample row: 0.096208 + 0.168641 = 0.264849). So
            # totalFees is the true round-trip total, the equivalent of that inclusive `fee`. This
            # holds at any loyalty tier; it is a sum of two reported fields, not a rate assumption.
            # WITHOUT this the headline stays GROSS for every CURRENT strategy, which is the path
            # the AVAX review ran on — it reported +$410.14 where the net was +$310.23.
            "fee": _f(p, "totalFees", "total_fees", default=None),
            "margin_used": _f(p, "marginUsed", "margin_used", default=None),
            "open_time": _ms(_field(p, "openTime", "open_time")),
            "close_time": close_ms,
            "closed_order_id": _field(p, "closedOrderId", "closed_order_id"),
            "source": "discovery",
        })
    if trades:
        trades.sort(key=lambda t: _num(t.get("close_time")) or 0, reverse=True)
        return trades[:cap] if cap else trades

    # DISCOVERY EMPTY → recover on-chain. This is the closed-strategy trap: the trades exist, just not in
    # Senpi's index. Empty HL fills too ⇒ genuinely no trades (a brand-new book) → [].
    fills = _hl_info({"type": "userFills", "user": wallet}, meta, client)
    if isinstance(fills, list) and len(fills) >= HL_FILLS_CAP:
        meta.setdefault("fills_capped", []).append(wallet)
    if strict and not isinstance(fills, list):
        meta.setdefault("closed_trades_unknown", []).append(wallet)
        return None
    recon = _reconstruct_closed_from_fills(fills, since_ms, until_ms, cap)
    if recon:
        meta.setdefault("onchain_recovered_wallets", []).append(wallet)
        meta["closed_trade_source"] = "onchain_fills"
    return recon


# ──────────────────────────────────────────────────────────────── current price (market_get_asset_data)
def _price_now(client, asset, dex, meta):
    """CURRENT mark price for one asset (lifted from portfolio.py's market_get_asset_data extraction —
    reads markPx from the context block). Only CURRENT price is needed for v1 (no historical candles).
    Read-guarded → None on any failure."""
    kw = dict(asset=asset, candle_intervals=[], include_order_book=False, include_funding=False, timeout=8)
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


# ──────────────────────────────────────────────── open book (unrealized PnL — the TOTAL-ledger read)
_CLEARINGHOUSE_ATTEMPTS = 2   # bounded retries on the open-book read — transient blips only
_CLEARINGHOUSE_BACKOFF_S = 0.4


def _is_transient(exc):
    """Retry the open-book read ONLY on a transient transport/5xx/429/timeout blip — never on a definitive
    answer. An MCPError carrying an HTTP 4xx (other than 429), a JSON-RPC error, a tool isError, or an
    app-level {success:false} is the server SAYING something real; retrying just repeats it (and could hide a
    genuine 'no'). A raw transport error (socket timeout / connection reset / DNS) is a blip worth one more
    try. Retries only REDUCE the frequency of a `None` read — they never remove it, so a still-failed read
    stays UNKNOWN (never a fabricated 0) and partial coverage stays flagged."""
    from mcp_client import MCPError
    if isinstance(exc, MCPError):
        m = re.search(r"HTTP (\d{3})", str(exc))
        return bool(m) and (int(m.group(1)) == 429 or 500 <= int(m.group(1)) < 600)
    return True


def _account_value(ch):
    """A strategy wallet's USD value from the SAME strategy_get_clearinghouse_state read the open book
    already makes — senpi-portfolio's formula: main and xyz are two VIEWS of one wallet, `withdrawable`
    is the shared idle mirrored in both (counted once), value = shared idle + each view's equity above it.
    None (never 0) when the main view carries no `marginSummary.accountValue`: unknown is never zero."""
    if not isinstance(ch, dict):
        return None
    av, wd = {}, {}
    for dex in ("main", "xyz"):
        d = ch.get(dex) if isinstance(ch.get(dex), dict) else {}
        ms = _field(d, "marginSummary", "margin_summary", default=None)
        av[dex] = _num(_field(ms, "accountValue", "account_value")) if isinstance(ms, dict) else None
        wd[dex] = _num(d.get("withdrawable")) or 0.0
    if av["main"] is None:
        return None
    idle = max(wd["main"], wd["xyz"])
    deployed = sum(max(0.0, (av[dex] or 0.0) - idle) for dex in ("main", "xyz"))
    return round(idle + deployed, 2)


def fetch_open_positions(client, wallet, meta, extra=None):
    """Open positions + unrealized PnL for ONE current strategy wallet (strategy_get_clearinghouse_state,
    main+xyz). This is what makes the review a TOTAL ledger (realized closed trades + unrealized open) rather
    than realized-only — closing the biggest distortion: a book RIDING open winners looks like a loser on
    realized PnL alone, and a realized-only read biases against hold-strategies. Read-guarded + fail-open with
    a bounded retry on transient blips (transport/5xx/429/timeout): a still-unreadable read → (None, []) so
    unrealized reads UNKNOWN (never a fabricated 0); a clean read with no open positions → (0.0, []) — a REAL
    zero. Returns (unrealized_pnl_total | None, positions[])."""
    ch, err, attempts = None, None, 0
    for attempt in range(1, _CLEARINGHOUSE_ATTEMPTS + 1):
        attempts = attempt
        try:
            ch = _ok(client.mcp_call("strategy_get_clearinghouse_state", strategy_wallet=wallet, timeout=12))
            err = None
            break
        except Exception as e:  # noqa — fail-open: unrealized becomes UNKNOWN, never guessed 0
            err = e
            if attempt < _CLEARINGHOUSE_ATTEMPTS and _is_transient(e):
                time.sleep(_CLEARINGHOUSE_BACKOFF_S * attempt)
                continue
            break
    if err is not None:
        meta.setdefault("warnings", []).append(
            f"clearinghouse {str(wallet)[:8]} failed after {attempts} attempt(s): {err}; "
            f"unrealized PnL unavailable for it")
        return None, []
    if not isinstance(ch, dict):
        return None, []
    if extra is not None:
        extra["account_value"] = _account_value(ch)   # the book row's value — same read, no new call
    positions, unrealized = [], 0.0
    for section in ("main", "xyz"):
        s = ch.get(section) if isinstance(ch.get(section), dict) else {}
        for ap in (s.get("assetPositions") or []):
            pos = ap.get("position", ap) if isinstance(ap, dict) else {}
            if not isinstance(pos, dict):
                continue
            szi = _num(pos.get("szi"))
            if not szi:
                continue
            up = _num(_field(pos, "unrealizedPnl", "unrealized_pnl")) or 0.0
            unrealized += up
            roe = _num(_field(pos, "returnOnEquity", "return_on_equity"))
            lev = pos.get("leverage")
            positions.append({
                "asset": _field(pos, "coin", "asset"),
                "direction": "long" if szi > 0 else "short",
                "size": abs(szi),
                "entry_px": _num(_field(pos, "entryPx", "entry_px")),
                "unrealized_pnl": round(up, 2),
                "return_on_equity_pct": round(roe * 100, 2) if roe is not None else None,
                "position_value": _num(_field(pos, "positionValue", "position_value")),
                "leverage": _f(lev, "value", default=None) if isinstance(lev, dict) else _num(lev),
            })
    return round(unrealized, 2), positions


def fetch_open_book(client, strategies, meta):
    """wallet_lower → {unrealized_pnl, positions[]} for the CURRENT (active/paused) strategies only — the live
    book whose UNREALIZED PnL completes the total-ledger picture (closed strategies have no open positions).
    Parallel clearinghouse fetches (dedup by wallet), each fail-open. A wallet whose read failed is kept with
    `unrealized_pnl: None` (UNKNOWN) — the caller must NOT treat that as 0. {} when there are no current
    wallets."""
    wallets, seen = [], set()
    for s in strategies:
        if not _is_current(s.get("status")):
            continue
        w = s.get("wallet")
        wl = str(w or "").lower()
        if w and wl not in seen:
            seen.add(wl)
            wallets.append(w)
    if not wallets:
        return {}

    def _worker(w):
        priv, extra = {}, {}
        unreal, positions = fetch_open_positions(client, w, priv, extra)
        return str(w).lower(), unreal, positions, priv, extra.get("account_value")

    out = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(wallets))) as ex:
        for wl, unreal, positions, priv, value in ex.map(_worker, wallets):
            out[wl] = {"unrealized_pnl": unreal, "positions": positions, "account_value": value}
            if priv.get("warnings"):
                meta.setdefault("warnings", []).extend(priv["warnings"])
    return out


# ──────────────────────────────────────────────────────────────── the counterfactual math (per trade)
def _if_held(trade, price_now):
    """The honest 'if I'd held to now' counterfactual for ONE closed trade — CONTEXT, never the verdict.

    since_exit_pct = (price_now - exit_px)/exit_px  (raw price move since the exit)
    if_held_delta_usd = notional × since_exit_pct, DIRECTION-ADJUSTED — a short GAINS when price falls,
      so a short's delta flips the sign. This is the extra $ the position WOULD have made (or lost) if it
      had stayed open from the exit to now, on top of the realized PnL.
    exit_vs_hold ∈ {exit_ahead, held_higher, flat}: NEUTRAL context, never a grade. held_higher = holding
      to now would be higher (this-window trend; ignores the risk the exit avoided); exit_ahead = holding
      would have given gains back. Returns (since_exit_pct, if_held_delta_usd, exit_vs_hold); None → "unknown"."""
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
    # An unknown side has no sign to adjust by: no dollar figure and no verdict, never a guess either way.
    direction = trade.get("direction")
    if direction not in ("long", "short"):
        return since_exit_pct, None, "unknown"
    signed = -raw_move if direction == "short" else raw_move
    if_held_delta = round(notional * signed, 2)
    # exit_vs_hold — NEUTRAL context, never a grade. Positive delta → holding-to-now would be higher
    # (this-window trend; says nothing about exit quality, and ignores the risk the exit avoided);
    # negative → the exit got out ahead of a reversal; ~0 → flat. NEVER read held_higher as "premature".
    eps = max(1.0, 0.001 * (notional or 0.0))     # $1 or 0.1% of notional, whichever larger — noise floor
    if if_held_delta > eps:
        verdict = "held_higher"   # holding to now would be higher — NOT "you exited too early" (hindsight)
    elif if_held_delta < -eps:
        verdict = "exit_ahead"    # holding would have given gains back → the exit got out ahead
    else:
        verdict = "flat"
    return since_exit_pct, if_held_delta, verdict


# ──────────────────────────────────────────────────────────────── the source boundary (telemetry-ready)
def _merge_meta(dst, src):
    """Fold a per-strategy worker's private `meta` back into the shared `meta` in the MAIN thread only —
    so no worker ever mutates shared state. Warnings concatenate in strategy order (deterministic); the
    telemetry sticky flags OR together (once dead / warned anywhere → dead / warned overall)."""
    w = src.get("warnings")
    if w:
        dst.setdefault("warnings", []).extend(w)
    if src.get("_telemetry_dead"):
        dst["_telemetry_dead"] = True
    if src.get("_telemetry_warned"):
        dst["_telemetry_warned"] = True
    for k in ("closed_trades_unknown", "fills_capped"):
        if src.get(k):
            dst.setdefault(k, []).extend(src[k])


def _collect_one_strategy(client, strat, meta, since_ms, until_ms, cap, enrich_exit=True):
    """Phase-1 worker — fully processes ONE strategy on a thread and returns its contribution, WITHOUT the
    price/`_if_held` step (deferred to phase 2). Runs against a PRIVATE `meta` (merged back in the main
    thread) so threads never write shared state. Wrapped fail-open by the caller — one strategy's failure
    contributes empty, never sinks the run.

    `enrich_exit` gates the TELEMETRY work (the slow, latency-bearing part): when True (the `all` / composed
    path) each trade's EXIT REASON is filled from the runtime event log (or the ratchet fallback), and the
    missed_signals + leak/fill streams are harvested from the SAME events. When False (the fast `timing`
    step) NO events and NO ratchet are fetched — every trade lands with a placeholder `exit_reason` terminal
    UNKNOWN (source "unknown") and `source` "reconstructed"; the `telemetry` step fills these in later. This
    is what keeps the headline timing slice off the telemetry critical path.

    Returns a dict: {trades (no price fields yet), missed_signals, leaks, fills, meta}. Each collected trade
    already carries its strategy tags + telemetry/ratchet exit_reason + `source`; only price_now /
    price_since_exit_pct / if_held_delta_usd / exit_vs_hold remain for phase 2."""
    out_trades, missed_signals = [], []
    leaks = {"order_failed": {"count": 0, "samples": []},
             "protection_gaps": {"count": 0, "samples": []},
             "risk_halts": {"count": 0, "samples": []}}
    fills = {"maker": 0, "taker": 0, "unknown": 0}
    if _is_external(strat):
        # a wallet the user trades by hand: no runtime, no ratchet, no telemetry ring — never read them
        # (each would add a degraded-attribution warning or a NOT_A_STRATEGY_WALLET error). Every exit is
        # a manual trade; no DSL lever exists to route a fix at.
        closed = fetch_closed_trades(client, strat["wallet"], since_ms, until_ms, cap, meta, strict=True) or []
        for t in closed:
            asset = t.get("asset")
            t.update({
                "strategy_label": strat.get("label"),
                "strategy_wallet": strat.get("wallet"),
                "strategy_status": EXTERNAL_STATUS,
                "wallet_kind": EXTERNAL,
                "mandate": None,
                "dex": "xyz" if str(asset).startswith("xyz:") else "main",
                "source": EXTERNAL_SOURCE,
                "exit_reason": {"terminal": MANUAL_TRADE, "tier_reached": None, "high_water_roe": None,
                                "source": EXTERNAL_SOURCE},
            })
            out_trades.append(t)
        return {"trades": out_trades, "missed_signals": missed_signals,
                "leaks": leaks, "fills": fills, "meta": meta}
    closed = fetch_closed_trades(client, strat["wallet"], since_ms, until_ms, cap, meta)
    # telemetry ring for this runtime (enrichment only; guarded + fail-open to []). SKIPPED on the fast
    # timing path (enrich_exit=False). ALSO skipped for non-current strategies: only ACTIVE/PAUSED runtimes
    # have a live on-disk ring — a CLOSED strategy's ring is torn down with its runtime. Probing it isn't a
    # hang (the gateway returns an immediate NOT_FOUND); the cost is that EVERY `openclaw senpi events`
    # spawnSyncs a SECOND `openclaw gateway call` process — two Node CLI boots per fetch. Fanning dozens of
    # those process pairs across the 8-thread pool STARVES the CPU, pushing even live-ring reads past the
    # timeout — which is why the pre-fix review reported "telemetry unavailable" for the live strategies too.
    # Not probing the closed runtimes at all is the real win. Skipping costs nothing: the trade list still
    # comes from discovery/on-chain, exit_reason falls to the ratchet/UNKNOWN fallback below, and the durable
    # central event log is the recovery path for a CLOSED strategy's exit reasons (not the ephemeral ring).
    events = (_fetch_events(strat.get("runtime_id"), since_ms, meta)
              if (enrich_exit and _is_current(strat.get("status"))) else [])
    if events:
        missed_signals.extend(_missed_signals_from_events(events, strat.get("label")))
        # scan the SAME entries once for leaks (failed orders / protection gaps / risk halts) + fills.
        _scan_leak_and_fill_events(events, strat.get("label"), leaks, fills)
    if closed:
        by_order_id, by_asset = _index_exit_events(events) if enrich_exit else ({}, {})
        # ratchet is part of exit attribution (the SECONDARY fallback) — also deferred off the timing path.
        ratchet_records = _load_ratchet_records(client, strat, meta) if enrich_exit else {}
        for t in closed:
            asset = t.get("asset")
            dex = "xyz" if str(asset).startswith("xyz:") else "main"
            # EXIT REASON — telemetry (native) wins; ratchet is the reconstructed fallback; else UNKNOWN.
            # Telemetry only ever writes exit_reason — asset/px/pnl/direction/timing stay discovery's.
            ev = _match_exit_event(t, by_order_id, by_asset) if enrich_exit else None
            if ev is not None:
                exit_reason = _exit_reason_from_event(ev)
            elif enrich_exit:
                exit_reason = _exit_reason_for(asset, ratchet_records)
                # tag the reconstructed fallback so the source rollup can separate ratchet from unknown
                exit_reason["source"] = "ratchet" if exit_reason.get("terminal") != "UNKNOWN" else "unknown"
            else:
                # fast timing path — exit mechanism not resolved yet; the telemetry step fills it in.
                exit_reason = {"terminal": "UNKNOWN", "tier_reached": None,
                               "high_water_roe": None, "source": "unknown"}
            t.update({
                "strategy_label": strat.get("label"),
                "strategy_wallet": strat.get("wallet"),
                # the strategy's status — so a trade on a CLOSED/INACTIVE strategy reads as HISTORY, not a
                # live-book verdict. CURRENT (ACTIVE/PAUSED) trades feed the per-strategy read; a closed
                # strategy's trades stay in the timing review (attributed by label) but never get a verdict.
                "strategy_status": strat.get("status"),
                "mandate": strat.get("mandate"),
                "dex": dex,
                # provenance of the TRADE ROW: discovery always owns the onchain facts; the string reflects
                # where the exit_reason came from (telemetry-enriched vs reconstructed-only).
                "source": "telemetry" if ev is not None else "reconstructed",
                "exit_reason": exit_reason,
            })
            out_trades.append(t)
    return {"trades": out_trades, "missed_signals": missed_signals,
            "leaks": leaks, "fills": fills, "meta": meta}


def _collect_trades(client, strategies, meta, since_ms, until_ms, cap, want_market, enrich_exit=True):
    """THE SOURCE BOUNDARY — onchain data → discovery; runtime events → telemetry.

    DISCOVERY OWNS THE TRADE LIST + every onchain fact. `fetch_closed_trades` (discovery_get_trader_history)
    is the trade source, untouched — asset, entry/exit px, realized PnL, direction, timing, closedOrderId
    all come from there and are never re-derived. `market_get_asset_data` supplies the current price for
    the honest "if I'd held to now" counterfactual.

    TELEMETRY (the runtime event log) ENRICHES those discovery trades: it fills each trade's EXIT REASON
    (`dsl.closed` / `position.closed` close_reason + tier + roe) and produces the standalone telemetry
    streams — `missed_signals[]` (blocked/rejected `signal.outcome` — 'what did I miss'), plus the leak +
    execution-quality rollups ('where am I leaking', 'fees maker vs taker'). ALL of these reuse the SAME
    per-runtime events fetched ONCE here (no re-fetch). Telemetry NEVER reconstructs a trade or re-derives a
    price/PnL. Exit-reason match priority: exact order_id → else asset+close_time within ±2min; no telemetry
    match → the ratchet record (SECONDARY fallback) → else UNKNOWN. Telemetry wins when present.

    PERFORMANCE — two parallel phases (output shape + values IDENTICAL to the old sequential form):
      Phase 1 — per-strategy fan-out on a ThreadPoolExecutor (max 8 workers). Each worker fully processes
        ONE strategy (fetch_closed_trades + _fetch_events → missed_signals + leak/fill deltas +
        _load_ratchet_records + the per-trade exit_reason match), but DEFERS the price/`_if_held` step. Each
        worker runs against a PRIVATE meta and returns local leak/fill deltas — no shared mutable state is
        written from threads. Results merge in the MAIN thread deterministically: strategies iterated in
        their original order, trades concatenated, leaks/fills summed per-strategy, warnings concatenated in
        order. Each worker is wrapped fail-open (one strategy's failure contributes empty, never crashes).
      Phase 2 — dedupe + parallelize the price fetches. The unique (asset, dex) set across all collected
        trades is priced ONCE each (small pool → {(asset,dex): price} cache), then `_if_held` is applied to
        every trade from the cache. This collapses the old per-trade sequential price calls (3× JPY → 1).
        Skipped entirely when want_market is False (price_now stays None, exit_vs_hold 'unknown').

    Returns (trades, missed_signals, leaks, fills). Fail-open per source — a missing source degrades that
    field/stream, not the whole trade; zero telemetry → discovery path is fully intact + empty aggregates."""
    trades, missed_signals = [], []
    # leak + execution-quality accumulators — SUMMED from each strategy's private deltas (no thread writes).
    leaks = {"order_failed": {"count": 0, "samples": []},
             "protection_gaps": {"count": 0, "samples": []},
             "risk_halts": {"count": 0, "samples": []}}
    fills = {"maker": 0, "taker": 0, "unknown": 0}

    def _worker(strat):
        # each worker gets a PRIVATE meta seeded with the sticky telemetry flag so it can still short-circuit.
        priv = {"_telemetry_dead": meta.get("_telemetry_dead", False)}
        try:
            return _collect_one_strategy(client, strat, priv, since_ms, until_ms, cap, enrich_exit=enrich_exit)
        except Exception as e:  # noqa — one strategy failing must not sink the run (fail-open to empty)
            priv.setdefault("warnings", []).append(f"strategy collect failed: {e}")
            return {"trades": [], "missed_signals": [],
                    "leaks": {"order_failed": {"count": 0, "samples": []},
                              "protection_gaps": {"count": 0, "samples": []},
                              "risk_halts": {"count": 0, "samples": []}},
                    "fills": {"maker": 0, "taker": 0, "unknown": 0}, "meta": priv}

    # ── Phase 1: per-strategy fan-out (parallel), then merge in original order (deterministic) ──
    if strategies:
        workers = min(8, len(strategies))
        _reset_hl_retry_budget()
        # saved wallets are READ first (their fills come only from Hyperliquid, so a rate-limited IP late in
        # a big book hit them hardest); the merge below still runs in the original strategy order
        first = sorted(range(len(strategies)), key=lambda i: 0 if _is_external(strategies[i]) else 1)
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
            done = dict(zip(first, ex.map(_worker, [strategies[i] for i in first])))
        results = [done[i] for i in range(len(strategies))]   # original order — deterministic merge
        for res in results:                                # iterate in original strategy order
            trades.extend(res["trades"])
            missed_signals.extend(res["missed_signals"])
            rl = res["leaks"]
            for cat in leaks:
                leaks[cat]["count"] += rl[cat]["count"]
                # keep the sample cap: fill from each strategy's samples until the category cap is reached
                for s in rl[cat]["samples"]:
                    if len(leaks[cat]["samples"]) < _LEAK_SAMPLE_CAP:
                        leaks[cat]["samples"].append(s)
            rf = res["fills"]
            for k in fills:
                fills[k] += rf[k]
            _merge_meta(meta, res["meta"])

    # sort newest-first + apply the GLOBAL 'last N' bound BEFORE pricing, so we only price/report the trades
    # that survive the cap. `cap` (= last_n) is also applied per-wallet in fetch_closed_trades; this global
    # pass makes 'last 10' the 10 most-recent trades across the WHOLE book, not 10 per strategy (the pre-fix
    # behavior returned up to 10 × strategy_count — a churned book turned "last 10" into 140+ trades).
    trades.sort(key=lambda t: _num(t.get("close_time")) or 0, reverse=True)
    if cap:
        # per KIND: a saved wallet's manual trades must never crowd a Senpi row out of 'last N' (the
        # Senpi aggregates stay identical to the Senpi-only run); each kind keeps its own most-recent N.
        kept_s = [t for t in trades if t.get("wallet_kind") != EXTERNAL][:cap]
        kept_c = [t for t in trades if t.get("wallet_kind") == EXTERNAL][:cap]
        keep = {id(t) for t in kept_s + kept_c}
        trades = [t for t in trades if id(t) in keep]      # preserves the combined newest-first order

    # ── Phase 2: dedupe + parallelize the price fetches, then apply _if_held from the cache ──
    price_cache = {}
    if want_market and trades:
        keys = []
        seen = set()
        for t in trades:
            asset = t.get("asset")
            dex = "xyz" if str(asset).startswith("xyz:") else "main"
            key = (asset, dex)
            if key not in seen:
                seen.add(key)
                keys.append(key)
        if keys:
            pworkers = min(8, len(keys))

            def _price_worker(key):
                asset, dex = key
                try:
                    return key, _price_now(client, asset, dex, meta)
                except Exception:  # noqa — fail-open per asset → None (already _price_now's contract)
                    return key, None

            with concurrent.futures.ThreadPoolExecutor(max_workers=pworkers) as pex:
                for key, price in pex.map(_price_worker, keys):
                    price_cache[key] = price

    for t in trades:
        asset = t.get("asset")
        dex = "xyz" if str(asset).startswith("xyz:") else "main"
        price_now = price_cache.get((asset, dex)) if want_market else None
        since_pct, if_held, verdict = _if_held(t, price_now)
        t.update({
            "price_now": price_now,
            "price_since_exit_pct": since_pct,
            "if_held_delta_usd": if_held,   # CONTEXT, not verdict
            "exit_vs_hold": verdict,        # engine verdict of the exit vs holding-to-now
        })

    missed_signals.sort(key=lambda m: _num(m.get("ts")) or 0, reverse=True)
    return trades, missed_signals, leaks, fills


def _enrich_exit_and_streams(client, trades, strategies, meta, since_ms):
    """THE TELEMETRY STEP's engine core — enrich already-collected discovery `trades[]` (from the timing
    step) with their EXIT REASON + produce the standalone telemetry streams (missed_signals + leak/fill
    rollups), WITHOUT re-fetching discovery or re-pricing. This is the enrichment half of
    `_collect_one_strategy`, lifted so it can run as an isolated step over the persisted state (its per-runtime
    event shell-outs carry the latency that the fast timing slice deferred).

    Trades are grouped by strategy_wallet and each strategy's runtime event ring is fetched ONCE (same
    fan-out + deterministic merge as `_collect_trades`): the events fill each trade's `exit_reason`/`source`
    IN PLACE (telemetry-native wins → ratchet fallback → honest UNKNOWN) and, from the SAME entries, harvest
    `missed_signals[]` + the leak/fill accumulators. Onchain facts (asset/px/pnl/direction/timing) are NEVER
    touched — telemetry only ever writes exit_reason. Fail-open per strategy (one failing contributes empty).

    Returns (missed_signals, leaks, fills); `trades` are mutated in place. Produces the SAME values the `all`
    path folds into a composed run, so a timing→telemetry sequence reproduces `all`."""
    missed_signals = []
    leaks = {"order_failed": {"count": 0, "samples": []},
             "protection_gaps": {"count": 0, "samples": []},
             "risk_halts": {"count": 0, "samples": []}}
    fills = {"maker": 0, "taker": 0, "unknown": 0}
    # index the persisted trades by wallet so each strategy enriches only its own rows (the same partition
    # _collect_one_strategy had implicitly, now reconstructed from state).
    by_wallet = {}
    for t in trades:
        by_wallet.setdefault(str(t.get("strategy_wallet") or "").lower(), []).append(t)

    def _worker(strat):
        priv = {"_telemetry_dead": meta.get("_telemetry_dead", False)}
        res = {"missed_signals": [],
               "leaks": {"order_failed": {"count": 0, "samples": []},
                         "protection_gaps": {"count": 0, "samples": []},
                         "risk_halts": {"count": 0, "samples": []}},
               "fills": {"maker": 0, "taker": 0, "unknown": 0}, "meta": priv}
        if _is_external(strat):
            return res                    # manual trades: no ring, no ratchet — exit_reason stays MANUAL_TRADE
        try:
            # only current (active/paused) strategies have a live on-disk ring — never shell at a closed one
            # (its ring is torn down; probing it just burns two CLI-boot processes for an immediate NOT_FOUND,
            # and the fan-out of those starves the pool). Its trades still enrich via the ratchet fallback
            # below. See _collect_one_strategy for the full rationale.
            events = (_fetch_events(strat.get("runtime_id"), since_ms, priv)
                      if _is_current(strat.get("status")) else [])
            if events:
                res["missed_signals"].extend(_missed_signals_from_events(events, strat.get("label")))
                _scan_leak_and_fill_events(events, strat.get("label"), res["leaks"], res["fills"])
            strat_trades = by_wallet.get(str(strat.get("wallet") or "").lower(), [])
            if strat_trades:
                by_order_id, by_asset = _index_exit_events(events)
                ratchet_records = _load_ratchet_records(client, strat, priv)
                for t in strat_trades:
                    asset = t.get("asset")
                    ev = _match_exit_event(t, by_order_id, by_asset)
                    if ev is not None:
                        exit_reason = _exit_reason_from_event(ev)
                    else:
                        exit_reason = _exit_reason_for(asset, ratchet_records)
                        exit_reason["source"] = ("ratchet" if exit_reason.get("terminal") != "UNKNOWN"
                                                 else "unknown")
                    # telemetry ONLY ever writes exit_reason + the row's source tag — nothing onchain.
                    t["exit_reason"] = exit_reason
                    t["source"] = "telemetry" if ev is not None else "reconstructed"
        except Exception as e:  # noqa — one strategy failing must not sink the enrichment (fail-open)
            priv.setdefault("warnings", []).append(f"strategy enrich failed: {e}")
        return res

    if strategies:
        workers = min(8, len(strategies))
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
            results = list(ex.map(_worker, strategies))     # ex.map preserves input order
        for res in results:                                 # deterministic merge (original strategy order)
            missed_signals.extend(res["missed_signals"])
            rl = res["leaks"]
            for cat in leaks:
                leaks[cat]["count"] += rl[cat]["count"]
                for s in rl[cat]["samples"]:
                    if len(leaks[cat]["samples"]) < _LEAK_SAMPLE_CAP:
                        leaks[cat]["samples"].append(s)
            rf = res["fills"]
            for k in fills:
                fills[k] += rf[k]
            _merge_meta(meta, res["meta"])

    missed_signals.sort(key=lambda m: _num(m.get("ts")) or 0, reverse=True)
    return missed_signals, leaks, fills


# ──────────────────────────────────────────────────────────────── timing summary (PROCESS-framed counts)
def _timing_summary(trades):
    """Process-framed COUNTS — the aggregate the narrator LEADS with, so it can't cherry-pick the few
    reversals. NO $/week, NO forward projections anywhere — only realized totals + engine counterfactuals.

    if_all_reclosed_now_total = the honest counterfactual aggregate: the sum of every trade's
    if_held_delta_usd (what the whole book WOULD have added, net, if every position had stayed open to
    now). Reported as context alongside realized_pnl_total, never as a verdict or a projection."""
    trade_count = len(trades)
    ahead = sum(1 for t in trades if t.get("exit_vs_hold") == "exit_ahead")
    held_higher = sum(1 for t in trades if t.get("exit_vs_hold") == "held_higher")
    flat = sum(1 for t in trades if t.get("exit_vs_hold") == "flat")
    unknown = sum(1 for t in trades if t.get("exit_vs_hold") == "unknown")
    realized_total = round(sum(_num(t.get("realized_pnl")) or 0.0 for t in trades), 2)
    if_deltas = [_num(t.get("if_held_delta_usd")) for t in trades]
    if_deltas = [d for d in if_deltas if d is not None]
    if_all = round(sum(if_deltas), 2) if if_deltas else None

    by_class = {}
    for t in trades:
        cls = "equity/index" if t.get("dex") == "xyz" else "crypto"
        b = by_class.setdefault(cls, {"trade_count": 0, "exits_ahead": 0, "exits_held_higher": 0,
                                      "realized_pnl_total": 0.0})
        b["trade_count"] += 1
        if t.get("exit_vs_hold") == "exit_ahead":
            b["exits_ahead"] += 1
        elif t.get("exit_vs_hold") == "held_higher":
            b["exits_held_higher"] += 1
        b["realized_pnl_total"] = round(b["realized_pnl_total"] + (_num(t.get("realized_pnl")) or 0.0), 2)

    return {
        "trade_count": trade_count,
        "exits_ahead": ahead,                  # exit got out ahead of a reversal (holding would've given back)
        "exits_held_higher": held_higher,      # holding to now would be higher — NEUTRAL, NOT 'premature'
        "exits_flat": flat,
        "exits_unknown": unknown,              # price missing → couldn't compare (honest sourcing)
        "realized_pnl_total": realized_total,
        "if_all_reclosed_now_total": if_all,   # counterfactual aggregate — CONTEXT, NEVER a projection/target
        "by_asset_class": by_class,
    }


# ──────────────────────────────────── telemetry quick-action aggregations (reuse the fetched events/trades)
def _is_premature_exit(exit_reason):
    """The 'shaken out too early' heuristic for ONE closed trade's exit_reason. TRUE when either:
      (a) terminal ∈ {trailing_floor, weak_peak, max_retrace} — the retrace/floor/weak-peak family that
          cuts a still-working position, OR
      (b) a LOW tier locked (tier_index/tier_reached <= 1) with a SMALL high-water ROE (<= ~5%) — the
          profit-lock armed and trailed out almost immediately.
    Fail-soft: a missing/odd exit_reason → False (not premature; we don't invent an early exit)."""
    if not isinstance(exit_reason, dict):
        return False
    terminal = str(exit_reason.get("terminal") or "")
    if terminal == MANUAL_TRADE:
        return False                      # a saved wallet's exit is the user's own, never a DSL tier
    if terminal in _PREMATURE_TERMINALS:
        return True
    tier = _num(exit_reason.get("tier_index"))
    if tier is None:
        tier = _num(exit_reason.get("tier_reached"))
    roe = _num(exit_reason.get("high_water_roe"))
    if tier is not None and tier <= _PREMATURE_TIER_MAX and roe is not None and abs(roe) <= _PREMATURE_ROE_MAX:
        return True
    return False


def _dsl_close_reason_mix(trades):
    """'Am I getting shaken out too early? / how are my exits firing?' — a tally of every closed trade by its
    exit_reason.terminal (the telemetry close_reason, or the ratchet/UNKNOWN fallback), broken down OVERALL
    + by asset_class + by strategy_label, plus the premature-exit cohort (see _is_premature_exit). Routes to
    the DSL preset lever (widen phase1 retrace / retune a tier). Reuses trades[] — no re-fetch. Fail-open:
    no trades → zeroed structure."""
    def _blank():
        return {"by_terminal": {}, "trade_count": 0, "premature_exits": 0}

    def _tally(bucket, terminal, premature):
        bucket["trade_count"] += 1
        bucket["by_terminal"][terminal] = bucket["by_terminal"].get(terminal, 0) + 1
        if premature:
            bucket["premature_exits"] += 1

    overall = _blank()
    by_asset_class, by_strategy = {}, {}
    premature_samples = []
    for t in trades:
        er = t.get("exit_reason") or {}
        terminal = str(er.get("terminal") or "UNKNOWN") or "UNKNOWN"
        premature = _is_premature_exit(er)
        cls = "equity/index" if t.get("dex") == "xyz" else "crypto"
        label = t.get("strategy_label") or "unknown"
        _tally(overall, terminal, premature)
        _tally(by_asset_class.setdefault(cls, _blank()), terminal, premature)
        # every by_strategy bucket carries its strategy_label as the key → a per-strategy 'why is X losing' read
        _tally(by_strategy.setdefault(label, _blank()), terminal, premature)
        if premature and len(premature_samples) < _LEAK_SAMPLE_CAP:
            premature_samples.append({
                "asset": t.get("asset"), "strategy_label": label, "terminal": terminal,
                "tier_index": er.get("tier_index") if er.get("tier_index") is not None else er.get("tier_reached"),
                "high_water_roe": er.get("high_water_roe"), "realized_pnl": t.get("realized_pnl"),
            })
    return {
        "overall": overall,
        "by_asset_class": by_asset_class,
        "by_strategy": by_strategy,             # keyed by strategy_label → 'why is [strategy] losing' filter
        "premature_exit_samples": premature_samples,
        "premature_note": ("premature = terminal in {trailing_floor, weak_peak, max_retrace} OR a low tier "
                           "locked with a small high-water ROE → the DSL preset lever (phase1 retrace / a tier)"),
    }


def _blocked_summary(missed_signals):
    """'What did my own limits block? / what couldn't I take?' — tally missed_signals[] by reason_code
    (no_slots / no_margin / risk_gate_* / asset_banned / signal_not_ready / …), OVERALL + by strategy_label.
    Fix = add a slot / fund margin / loosen a risk gate. Reuses missed_signals[] (already telemetry-native) —
    no re-fetch. Fail-open: none → empty tallies + count 0."""
    by_reason, by_strategy = {}, {}
    for m in missed_signals:
        reason = str(m.get("reason_code") or "unknown") or "unknown"
        by_reason[reason] = by_reason.get(reason, 0) + 1
        label = m.get("strategy_label") or "unknown"
        strat = by_strategy.setdefault(label, {})
        strat[reason] = strat.get(reason, 0) + 1
    return {
        "total_blocked": len(missed_signals),
        "by_reason_code": by_reason,            # no_slots → add a slot; no_margin → fund; risk_gate_* → loosen
        "by_strategy": by_strategy,             # keyed by strategy_label → per-strategy blocked read
    }


def _execution_quality(fills):
    """'What am I paying in fees — maker vs taker?' — the maker/taker RATE from `order.filled`
    (senpi.order.execution_as_maker). Maker fills earn the rebate/lower tier; a taker-heavy book bleeds fees
    on turnover. Reuses the fills tally collected during the event scan — no re-fetch. Fail-open: no fills →
    zeroed counts + null ratio.

    NOTE: this is the fee-tier RATE signal only. The AUTHORITATIVE fee $ is the future ledger hook
    `order_id → execution_get_closed_position_details({closedOrderId})` (order.filled carries senpi.order.id);
    that per-order join is wired later and is intentionally NOT called per-trade here (rate-limit risk)."""
    maker = int(fills.get("maker", 0))
    taker = int(fills.get("taker", 0))
    unknown = int(fills.get("unknown", 0))
    known = maker + taker
    return {
        "maker_fills": maker,
        "taker_fills": taker,
        "unknown_fills": unknown,               # order.filled without the maker flag → not counted in the ratio
        "maker_ratio": round(maker / known, 4) if known else None,   # fraction of KNOWN fills that were maker
        "authoritative_fee_note": ("maker/taker RATE only; authoritative fee $ = future ledger join "
                                   "order_id → execution_get_closed_position_details (not called per-trade)"),
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
    if isinstance(raw, dict) and isinstance(raw.get("markets"), dict):
        raw = raw["markets"]   # live envelope: data.markets = {markets: [...], window, ...}
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
def _pnl_by_wallet(trades):
    """wallet_lower → {count, pnl} rollup of closed trades. Shared by the current-book read and the
    historical closed-strategy rollup so both attribute trades by the SAME wallet key."""
    by_wallet = {}
    for t in trades:
        w = str(t.get("strategy_wallet") or "").lower()
        b = by_wallet.setdefault(w, {"count": 0, "pnl": 0.0})
        b["count"] += 1
        b["pnl"] = round(b["pnl"] + (_num(t.get("realized_pnl")) or 0.0), 2)
    return by_wallet


def _strategy_reads(trades, strategies, open_book=None):
    """Per CURRENT strategy (ACTIVE/PAUSED ONLY): {label, wallet, status, mandate, dsl, closed_trade_count,
    realized_pnl, unrealized_pnl, total_pnl, open_position_count, open_positions, on_mandate_note}. The
    LIVE-BOOK verdict surface — closed strategies are excluded (history; see closed_strategy_rollup). Judge
    each on TOTAL PnL (realized closed + unrealized open) against its OWN mandate — NOT realized alone, which
    penalizes hold-strategies and misreads a book riding open winners. `unrealized_pnl` is None when the open
    book couldn't be read (UNKNOWN, never a fake 0) → `total_pnl` stays None. A CURRENT strategy with no
    mandate → note it plainly ("look it up"), NEVER a bug."""
    by_wallet = _pnl_by_wallet(trades)
    open_book = open_book or {}
    out = []
    for s in strategies:
        if not _is_current(s.get("status")):
            continue                          # closed/historical → not a live-book verdict; see rollup
        w = str(s.get("wallet") or "").lower()
        agg = by_wallet.get(w, {"count": 0, "pnl": 0.0})
        ob = open_book.get(w) or {}
        unrealized = ob.get("unrealized_pnl")       # None → open book unreadable (UNKNOWN, never a fake 0)
        open_positions = ob.get("positions") or []
        realized = agg["pnl"]
        total = (round(realized + unrealized, 2)
                 if isinstance(unrealized, (int, float)) and not isinstance(unrealized, bool) else None)
        mandate = s.get("mandate")
        if mandate is None:
            note = ("mandate unavailable on this CURRENT strategy — look it up / check the runtime "
                    "registry; NOT a bug. Judge the trades, not a benchmark")
        elif agg["count"] == 0 and open_positions:
            note = (f"no CLOSED trades in this window, but {len(open_positions)} open position(s) held now — "
                    "judge it on UNREALIZED + mandate, not a realized blank")
        elif agg["count"] == 0:
            note = "no closed trades in this window — often by design (waiting for its signal), not a defect"
        else:
            note = "judge these closed trades against THIS mandate, not last window's winners"
        out.append({
            "label": s.get("label"),
            "wallet": s.get("wallet"),
            # WHICH ROWS ARE ONE STRATEGY. Sleeves are named apart (`cougar-long`/`cougar-short`) and carry
            # different mandates, so nothing in the LABEL says they are one book — these two fields are the
            # only thing that does, and "never merge two sleeves" is unactionable without them on the row.
            # `group` is the runtime.yaml's own key (authoritative, shared by every sleeve); `skill_name` is
            # the attribution stamp, which survives when the registry is unreadable and `group` is None.
            "group": s.get("group"),
            "skill_name": s.get("skill_name"),
            "status": s.get("status"),
            "mandate": mandate,
            "dsl": s.get("dsl"),                 # the levers a fix routes to
            "closed_trade_count": agg["count"],
            "realized_pnl": realized,
            "unrealized_pnl": unrealized,        # current open positions' unrealized — None = UNKNOWN read
            "total_pnl": total,                  # realized + unrealized; None when unrealized UNKNOWN
            "open_position_count": len(open_positions),
            "open_positions": open_positions,    # asset/direction/unrealized_pnl/return_on_equity_pct/entry/lev
            "on_mandate_note": note,
        })
    return out


def _closed_strategy_rollup(trades, strategies):
    """Per NON-CURRENT strategy (CLOSED, INACTIVE, …): a MINIMAL HISTORICAL rollup ONLY —
    {label, wallet_short, status, trade_count, realized_pnl}. Deliberately NO mandate / dsl / verdict /
    on_mandate_note fields: a closed strategy is deregistered (its runtime.yaml gone by design), so it must
    NEVER be judged, consolidated, killed, or fixed, and its absent mandate is EXPECTED, not a bug. Its
    trades still live in trades[] for the timing review, attributed by label + status."""
    by_wallet = _pnl_by_wallet(trades)
    out = []
    for s in strategies:
        if _is_current(s.get("status")) or _is_external(s):
            continue                          # current → live-book read; saved → external_wallets[]
        w = str(s.get("wallet") or "").lower()
        agg = by_wallet.get(w, {"count": 0, "pnl": 0.0})
        wallet = s.get("wallet")
        out.append({
            "label": s.get("label"),
            "wallet_short": (str(wallet)[:10] if wallet else None),
            "status": s.get("status"),
            "trade_count": agg["count"],
            "realized_pnl": agg["pnl"],
        })
    return out


def _external_reads(trades, entries, meta):
    """Per SAVED wallet: its own closed-trade read, never folded into the Senpi aggregates
    (pnl_summary / strategies / dsl_close_reason_mix stay Senpi-only). Unknown is never empty: a wallet
    in meta.closed_trades_unknown reads closed_trade_count / realized_pnl / timing_summary None, never 0;
    `fills_capped` means HL returned its 2000-fill ceiling, so the totals are "at least". `open_positions`
    is `state.positions` verbatim (protection + stopOrders included) — None when the state couldn't load."""
    unknown = {str(w).lower() for w in (meta.get("closed_trades_unknown") or [])}
    capped = {str(w).lower() for w in (meta.get("fills_capped") or [])}
    out = []
    for e in entries:
        if not _is_external(e):
            continue
        w = str(e.get("wallet") or "").lower()
        rows = [t for t in trades
                if t.get("wallet_kind") == EXTERNAL and str(t.get("strategy_wallet") or "").lower() == w]
        unk = w in unknown
        fees = [x for x in (_num(t.get("fee")) for t in rows) if x is not None]
        st = e.get("state") if isinstance(e.get("state"), dict) else None
        out.append({
            "label": e.get("label"),
            "address": e.get("wallet"),
            "access": e.get("access"),
            "closed_trades_unknown": unk,
            "fills_capped": w in capped,
            "closed_trade_count": None if unk else len(rows),
            "realized_pnl": None if unk else round(sum(_num(t.get("realized_pnl")) or 0.0 for t in rows), 2),
            "fees": None if (unk or not fees) else round(sum(fees), 2),
            "timing_summary": None if unk else _timing_summary(rows),
            "state_read": e.get("state_read"),
            "read_error": st.get("readError") if st else None,
            "open_positions": st.get("positions") if st else None,
        })
    return out


# ──────────────────────────────────────────── telemetry source rollup (meta: how enrichment did)
def _exit_reason_source_counts(trades):
    """Tally where each trade's exit_reason came from: telemetry (native event log), ratchet (the
    reconstructed fallback attributed a terminal), or unknown (neither could confirm the mechanism)."""
    counts = {"telemetry": 0, "ratchet": 0, "unknown": 0}
    for t in trades:
        src = (t.get("exit_reason") or {}).get("source")
        if src in counts:
            counts[src] += 1
        else:
            counts["unknown"] += 1
    return counts


def _telemetry_source(source_counts, telemetry_warned):
    """meta.telemetry_source ∈ available / partial / unavailable. `available` = every enrichment read
    succeeded (no fail-open warning) AND at least one trade was telemetry-enriched. `unavailable` = a
    fail-open fired and nothing was enriched (older build / no ring / no openclaw). `partial` = mixed:
    some telemetry landed but a read also failed, or reads succeeded but only some trades matched."""
    tele = source_counts.get("telemetry", 0)
    if telemetry_warned:
        return "partial" if tele else "unavailable"
    return "available" if tele else "unavailable"


# ──────────────────────────────────────────────── total-ledger PnL + the 'undetermined ≠ all-clear' signal
def _pnl_summary(realized_total, strat_reads, fees_total=None):
    """The TOTAL-ledger headline the narrator LEADS with — realized (closed trades) + unrealized (current
    open positions) + total — PLUS the current-vs-closed realized split (so the narrator QUOTES it and never
    re-derives a wrong closed-book figure). `realized_total` is ALL closed trades; current-book realized = the
    current strategies' realized sum; closed-book = the remainder (reconciles by construction). `unrealized`
    sums only the current strategies whose open book was READABLE → None when none were, so `total` stays an
    honest UNKNOWN rather than collapsing to a realized-only headline."""
    realized = round(_num(realized_total) or 0.0, 2)
    # `realized` is HL closedPnl = GROSS. One measured AVAX round trip: +$410.14 gross, $99.92 fees,
    # +$310.23 net — a 32% overstatement if the narrator leads with gross.
    fees = round(_num(fees_total), 2) if fees_total is not None else None
    realized_net = round(realized - fees, 2) if fees is not None else None
    current_realized = round(sum(_num(s.get("realized_pnl")) or 0.0 for s in strat_reads), 2)
    closed_realized = round(realized - current_realized, 2)
    known = [u for u in (s.get("unrealized_pnl") for s in strat_reads)
             if isinstance(u, (int, float)) and not isinstance(u, bool)]
    unrealized = round(sum(known), 2) if known else None
    total = round(realized + unrealized, 2) if unrealized is not None else None
    # PARTIAL coverage: some current wallets were readable, some were NOT — so `unrealized` (and therefore
    # `total`) sums only the readable ones: a FLOOR, not a complete number. Flag it so the narrator says
    # "at least $X (N of M wallets readable)" and never presents a partial sum as the finished total. (0
    # readable is the all-UNKNOWN case: unrealized/total = None above, not a floor.)
    partial = unrealized is not None and len(known) < len(strat_reads)
    return {
        "realized": realized,
        "realized_by_book": {"current": current_realized, "closed": closed_realized},
        "unrealized": unrealized,                 # None → no current open book readable (UNKNOWN, not 0)
        "unrealized_coverage": {"read": len(known), "current_strategies": len(strat_reads)},
        "unrealized_partial": partial,            # True → unrealized/total are a FLOOR (some wallets UNKNOWN)
        "total": total,                           # realized + unrealized; None when UNKNOWN; a FLOOR when partial
        "fees": fees,                             # exchange + builder fee on the closed legs; None = not computed
        "realized_net": realized_net,             # realized MINUS fees — what actually landed
        "note": ("TOTAL = realized (closed trades) + unrealized (current open positions). LEAD with TOTAL, "
                 "not realized alone. unrealized None = the open book couldn't be read (UNKNOWN, not 0). "
                 "unrealized_partial True = only some current wallets read, so unrealized/total are a FLOOR "
                 "('at least $X, N of M wallets readable') — never present them as complete. `realized` and "
                 "`total` are GROSS of fees — quote `realized_net` for the closed book, and say 'gross' when "
                 "`fees` is None. There is deliberately no total_net: the OPEN leg has not paid exit fees yet."),
    }


def _exit_attribution_coverage(source_counts, trade_count):
    """How many closed trades have an ATTRIBUTED exit mechanism (telemetry-native or ratchet fallback) vs
    UNKNOWN. attributed == 0 → there is NO basis for ANY exit-mechanism or DSL-calibration claim."""
    tele = int(source_counts.get("telemetry", 0))
    ratchet = int(source_counts.get("ratchet", 0))
    return {"attributed": tele + ratchet, "total": int(trade_count),
            "telemetry": tele, "ratchet": ratchet, "unknown": int(source_counts.get("unknown", 0))}


def _telemetry_availability(coverage, telemetry_source):
    """The ONE signal the narrator keys off for 'undetermined ≠ all-clear'. status:
      no_trades    — nothing closed this window
      undetermined — telemetry unavailable AND 0 exits attributed → exit quality / leaks / blocked /
                     protection / fees are UNKNOWN ('couldn't check'), NEVER 'none/all-clear', and NO
                     exit-calibration diagnosis
      partial      — some enrichment landed, some didn't
      available    — telemetry read and every closed trade is attributed
    `streams_computed` False → the leaks/blocked/execution_quality/dsl_close_reason_mix ZEROS are fail-open
    placeholders (telemetry down), NOT genuine 'no leaks / no gaps'."""
    total = coverage.get("total", 0)
    attributed = coverage.get("attributed", 0)
    if total == 0:
        status = "no_trades"
    elif telemetry_source == "unavailable" and attributed == 0:
        status = "undetermined"
    elif telemetry_source == "available" and attributed == total:
        status = "available"
    else:
        status = "partial"
    computed = telemetry_source != "unavailable"
    note = ("telemetry unavailable — exit quality, leaks, blocked signals, protection gaps and fees are "
            "UNDETERMINED (couldn't check), NOT zero/none; do NOT diagnose exit calibration"
            if not computed else "telemetry read — the exit-quality / leak / fee streams are computed")
    return {"status": status, "telemetry_source": telemetry_source,
            "exit_attribution": coverage, "streams_computed": computed, "note": note}


def _stamp_external_meta(meta, entries, trades):
    """The saved-wallet counts `_book_state` routes on, kept apart from the Senpi counts."""
    meta["external_wallet_count"] = sum(1 for e in entries if _is_external(e))
    meta["external_trade_count"] = sum(1 for t in trades if t.get("wallet_kind") == EXTERNAL)
    meta["external_unknown_count"] = len({str(w).lower() for w in meta.get("closed_trades_unknown") or []})
    meta.setdefault("external_wallets_status", "unavailable")


def _degraded(senpi, entries, trades, meta):
    """meta.degraded — Senpi-strategy wording only when there are no saved wallets to review."""
    if not senpi and not any(_is_external(e) for e in entries):
        if meta.get("external_wallets_status", "ok") != EXTERNAL_OK:
            return "no Senpi strategies, and the saved wallets couldn't be loaded — unknown, not an empty book"
        return ("strategy list unreadable — check the token is USER-scoped"
                if any("strategy_list failed" in str(w) for w in (meta.get("warnings") or []))
                else "no strategies deployed yet (not a fault — see meta.book_state)")
    if not trades:
        return "no closed trades in the window (or trade history unavailable)"
    return None


# ──────────────────────────────── one book: every wallet first-class (rows by value, one total)
# Origin is an attribute on the row (`kind`), never a rank or a section break. The Senpi aggregates above
# stay Senpi-only and untouched; this layer sits on top of them, and its managed subtotal QUOTES them.
KIND_MANAGED = "managed"        # a CURRENT Senpi strategy wallet — Senpi trades it
KIND_READ_ONLY = "read_only"    # a saved wallet — the user trades it by hand; Senpi only reads it
MIN_COMPARE_CLOSES = 8          # guardrail 7's sample gate (~8 closes), reused: below it a wallet isn't compared
MAX_COMPARE_IN_LINE = 4         # the comparison line names at most this many wallets (largest first)
HL_FILLS_CEILING_NOTE = "Hyperliquid's 2,000-fill ceiling"


def _usd(x):
    return f"-${abs(x):,.2f}" if x < 0 else f"${x:,.2f}"


def _plural(n, word):
    return f"{n} {word}" + ("" if n == 1 else "s")


def _join_or(names):
    names = list(names)
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " or " + names[-1]


def _wallet_timing(ts):
    """The comparison metric, from one wallet's own `_timing_summary`: closes measurable against today's
    price (exit_ahead + held_higher + flat — `unknown` has no price) and how many got out ahead."""
    if not isinstance(ts, dict):
        return None
    ahead = int(ts.get("exits_ahead") or 0)
    m = ahead + int(ts.get("exits_held_higher") or 0) + int(ts.get("exits_flat") or 0)
    return {"measurable_closes": m, "exits_ahead": ahead,
            "exits_ahead_share": round(ahead / m, 2) if m else None}


def _row_sort_key(r):
    """Value desc; ties by label (casefold) then address; a wallet whose value couldn't load LAST."""
    v = r.get("value_usd")
    return (v is None, -(v or 0.0), str(r.get("label") or "").casefold(), str(r.get("wallet") or "").lower())


def _fee_sum(trades):
    fees = [x for x in (_num(t.get("fee")) for t in trades) if x is not None]
    return round(sum(fees), 2) if fees else None


# ── VENDORED book group key — byte-identical in senpi-portfolio, senpi-improve-trades and quant-desk;
# pinned by senpi-portfolio/tests/test_name_reader_parity.py. Edit all three or none.
def _book_group_key(strat):
    """The book's key for a strategy's wallets — one Senpi strategy is ONE row, with all its wallets.

    The invariant it rests on: one package = one strategy. The deploy verb stamps `skillName = <package
    id>` on every wallet it creates and names each `<id>-<instance>` (bare `<id>` for one instance); a
    re-run ADOPTS the live wallet of that name instead of funding a second, and refuses a wallet stamped
    by another package. So a package's instances are its wallets, and they share this key. A fork is a
    new id, so a new row. A wallet created outside the deploy path has no stamp: nothing says which
    wallets are one strategy, so it is its own row. A duplicate wallet (a deploy race) carries the same
    stamp and lands on the SAME row as an extra wallet — visible in its wallet count, never a second
    strategy.

    The SAME key in every step and every skill: portfolio's `money` step reads no runtime registry (the
    fast slice), so there is no `profile.group` there — grouping by it made one strategy two rows, then
    one. `skill_name` (from strategy_list) is on every read."""
    if strat.get("skill_name"):
        return str(strat["skill_name"])
    return str(strat.get("wallet") or id(strat))
# ── end vendored book group key


def _managed_rows(strat_reads, senpi_trades, open_book, capped):
    """One row per CURRENT Senpi STRATEGY, with all its wallets (`_book_group_key` — the key
    senpi-portfolio's book and the quant desk's list use, so all three list the same rows). Closed
    strategies are history (`closed_strategies[]`, no wallet to hold). Count / realized / fees are the
    per-wallet `strategies[]` numbers summed; value sums the wallets whose clearinghouse read loaded — one
    that didn't is counted in `wallets_couldnt_load`, never as $0. `strategies[]` stays per instance:
    each instance has its own runtime.yaml, so its verdict and its fix stay its own."""
    order, buckets = [], {}
    for s in strat_reads:
        try:
            key = _book_group_key(s)
        except Exception:  # noqa — a malformed row must not sink the list
            key = str(s.get("wallet") or id(s))
        if key not in buckets:
            buckets[key] = []
            order.append(key)
        buckets[key].append(s)
    rows = []
    for key in order:
        insts, wallets, every_trade = buckets[key], [], []
        for s in insts:
            w = str(s.get("wallet") or "").lower()
            ob = open_book.get(w) or {}
            read = ob.get("unrealized_pnl") is not None          # the open-book read succeeded
            mine = [t for t in senpi_trades if str(t.get("strategy_wallet") or "").lower() == w]
            every_trade += mine
            wallets.append({"label": s.get("label"), "wallet": s.get("wallet"), "status": s.get("status"),
                            "value_usd": ob.get("account_value") if read else None,
                            "open_position_count": s.get("open_position_count") if read else None,
                            "closed_trade_count": s.get("closed_trade_count"),
                            "realized_pnl": s.get("realized_pnl"), "fees": _fee_sum(mine),
                            "trades_capped": w in capped})
        known = [x["value_usd"] for x in wallets if x["value_usd"] is not None]
        value = round(sum(known), 2) if known else None
        opened = [x["open_position_count"] for x in wallets if x["open_position_count"] is not None]
        counts = [x["closed_trade_count"] for x in wallets if x["closed_trade_count"] is not None]
        realized = [x["realized_pnl"] for x in wallets if x["realized_pnl"] is not None]
        statuses = sorted({str(x["status"]) for x in wallets if x["status"]})
        one = len(wallets) == 1
        rows.append({
            # a packaged strategy is called by its package id (as senpi-portfolio's book does); a wallet with
            # no stamp keeps its own name
            "label": key if all(key != str(s.get("wallet")) for s in insts) else insts[0].get("label"),
            "kind": KIND_MANAGED,
            "wallet": wallets[0]["wallet"] if one else None,   # a strategy of several wallets has no one address
            "status": statuses[0] if len(statuses) == 1 else ("MIXED" if statuses else None),
            "strategy_group": key, "wallet_count": len(wallets), "wallets_loaded": len(known),
            "wallets_couldnt_load": len(wallets) - len(known), "strategy_wallets": wallets,
            "value_usd": value,
            "value_read": "unavailable" if value is None else ("ok" if len(known) == len(wallets) else "partial"),
            "closed_trade_count": sum(counts) if counts else None,
            "realized_pnl": round(sum(realized), 2) if realized else None,
            "fees": _sum_known(*[x["fees"] for x in wallets]), "trades_unknown": False,
            "trades_capped": any(x["trades_capped"] for x in wallets),
            "open_position_count": sum(opened) if opened else None,
            "protection": {"kind": "runtime_exit"},           # each wallet's exit is the runtime's — never "live stops"
            "timing": _wallet_timing(_timing_summary(every_trade)),
        })
    return rows


def _main_wallet_row(main):
    """The Senpi main wallet — managed money (Senpi moves it), valued by its own idle cash. It holds
    cash, never a strategy's trades: no closed trades, no positions, no exit. Unknown value is None."""
    v = (main or {}).get("value_usd")
    return {"label": MAIN_WALLET_LABEL, "kind": KIND_MANAGED, "origin": "main_wallet",
            "wallet": (main or {}).get("address"), "status": None, "holds": "cash",
            "value_usd": v, "value_read": "ok" if v is not None else "unavailable",
            "closed_trade_count": None, "realized_pnl": None, "fees": None, "trades_unknown": False,
            "trades_capped": False, "open_position_count": None, "protection": None, "timing": None,
            "not_applicable": ["trades", "open_positions", "protection"]}


def _read_only_rows(saved_reads, entries):
    """One row per saved wallet, from `external_wallets[]` plus its verbatim `state`. Value is
    `state.totalValueUsd` (already unified-account aware); an unread state is None, never 0."""
    states = {str(e.get("wallet") or "").lower(): e.get("state") for e in entries if _is_external(e)}
    rows = []
    for r in saved_reads or []:
        st = states.get(str(r.get("address") or "").lower())
        st = st if isinstance(st, dict) else None
        ok = st is not None and r.get("state_read") == "ok"
        value = _num(st.get("totalValueUsd")) if ok else None
        no_activity = bool(ok and value in (None, 0.0) and st.get("role") == "MISSING")
        if no_activity:
            value = 0.0                                       # role MISSING: a real zero, not an unread state
        unpriced = st.get("unpricedCoins") if ok and isinstance(st.get("unpricedCoins"), list) else []
        pos = r.get("open_positions")
        prot = None
        if isinstance(pos, list):
            prot = {"kind": "live_stops", "FULL": 0, "PARTIAL": 0, "NONE": 0}
            for p in pos:
                lvl = p.get("protection") if isinstance(p, dict) else None
                if lvl in prot:
                    prot[lvl] += 1
        rows.append({
            "label": r.get("label"), "kind": KIND_READ_ONLY, "wallet": r.get("address"),
            "value_usd": value,
            "value_read": "ok" if value is not None else ("error" if r.get("state_read") == "error" else "unavailable"),
            "no_hl_activity": no_activity,
            "closed_trade_count": r.get("closed_trade_count"), "realized_pnl": r.get("realized_pnl"),
            "fees": r.get("fees"), "trades_unknown": bool(r.get("closed_trades_unknown")),
            "trades_capped": bool(r.get("fills_capped")),
            "open_position_count": len(pos) if isinstance(pos, list) else None,
            "protection": prot,                               # the exchange's live stops — never "runtime exit"
            "unpriced_coins": list(unpriced),
            "access": r.get("access"),                        # quote verbatim
            "timing": _wallet_timing(r.get("timing_summary")),
        })
    return rows


def _book_comparison(rows):
    """ONE cross-wallet comparison over the rows, deterministic: the share of closes measurable against
    today's price where the later move went against the closed position (the exit got out ahead). It is
    the one timing metric the engine has for BOTH kinds — peak give-back needs a high-water mark, which
    only telemetry carries, and only for Senpi trades. Emitted only when 2+ wallets have
    MIN_COMPARE_CLOSES measurable closes; a wallet whose trades couldn't be read, or that hit the fill
    ceiling, is named in the line, never silently dropped or compared as if complete."""
    eligible, not_compared = [], []
    for r in rows:
        if r.get("origin") == "main_wallet":
            continue                                          # holds cash, never trades: nothing to compare
        name = r.get("display_label") or r.get("label")
        if r.get("trades_unknown"):
            not_compared.append({"label": name, "why": "its trades couldn't be read"})
            continue
        t = r.get("timing") or {}
        m = int(t.get("measurable_closes") or 0)
        if m < MIN_COMPARE_CLOSES:
            not_compared.append({"label": name, "why": f"{m} measurable closes, under {MIN_COMPARE_CLOSES}"})
            continue
        eligible.append({"label": name, "kind": r.get("kind"), "measurable_closes": m,
                         "exits_ahead": int(t.get("exits_ahead") or 0),
                         "exits_ahead_share": t.get("exits_ahead_share"),
                         "trades_capped": bool(r.get("trades_capped"))})
    out = {"metric": ("exits_ahead_share — of the closes measurable against today's price, the share where "
                      "the price then moved against the closed position. Hindsight CONTEXT, never a grade "
                      "(guardrail 2); a Senpi row's exits are its strategy's, a read-only row's the user's."),
           "min_closes": MIN_COMPARE_CLOSES, "wallets": eligible, "not_compared": not_compared}
    if len(eligible) < 2:
        out["line"] = None
        out["reason"] = (f"fewer than 2 wallets have {MIN_COMPARE_CLOSES}+ closes measurable against today's "
                         "price — no comparison this run; never compare the wallets by eye")
        return out
    named = eligible[:MAX_COMPARE_IN_LINE]
    parts = []
    for i, w in enumerate(named):
        pct = int(round(100.0 * w["exits_ahead"] / w["measurable_closes"]))
        head = f"{w['label']} got out ahead of the later move on " if i == 0 else f"{w['label']} on "
        parts.append(f"{head}{w['exits_ahead']} of {w['measurable_closes']} ({pct}%)")
    line = "On closes measurable against today's price, " + ", ".join(parts)
    if len(eligible) > len(named):
        line += f", and {_plural(len(eligible) - len(named), 'more wallet')}"
    line += "."
    for w in named:
        if w["trades_capped"]:
            line += f" {w['label']}'s closes are only its most recent ({HL_FILLS_CEILING_NOTE})."
    for n in not_compared:
        if n["why"] == "its trades couldn't be read":
            line += f" {n['label']} isn't compared: its trades couldn't be read."
    out["line"] = line
    out["reason"] = None
    return out


def _couldnt_load(rows):
    """The wallets whose value is left out of a total, by name: a one-wallet row by its own label, a
    strategy of several wallets by the instance that didn't load (its other wallets are in the total)."""
    out = []
    for r in rows:
        if int(r.get("wallet_count") or 1) > 1:
            out += [w.get("label") for w in r.get("strategy_wallets") or [] if w.get("value_usd") is None]
        elif r["value_usd"] is None:
            out.append(r["display_label"])
    return out


def _subtotal(rows, kind, state, realized=None, fees=None):
    mine = [r for r in rows if r["kind"] == kind]
    if state != "ok":
        return {"wallet_count": None, "wallets_loaded": None, "value_usd": None, "couldnt_load": [],
                "realized_pnl": None, "fees": None, "state": state}
    known = [r["value_usd"] for r in mine if r["value_usd"] is not None]
    # WALLETS, not rows: a strategy row carries all its wallets (`wallet_count` / `wallets_loaded`)
    return {"wallet_count": sum(int(r.get("wallet_count") or 1) for r in mine),
            # the line says "N of M wallets" when fewer loaded
            "wallets_loaded": sum(int(r.get("wallets_loaded", 1 if r["value_usd"] is not None else 0))
                                  for r in mine),
            "value_usd": round(sum(known), 2) if (known or not mine) else None,
            "couldnt_load": _couldnt_load(mine),
            "realized_pnl": realized, "fees": fees, "state": state}


def _sum_known(*xs):
    known = [x for x in xs if x is not None]
    return round(sum(known), 2) if known else None


def _book_total(rows, pnl_summary, managed_state, read_only_state):
    """ONE book total with managed and read-only subtotals beside it. The managed subtotal QUOTES the
    Senpi aggregates (pnl_summary.realized / .fees) — those numbers never move. Unknown is never 0: a
    wallet that couldn't load is left out and named, and so is an unreadable half of the book."""
    man = _subtotal(rows, KIND_MANAGED, managed_state,
                    realized=(pnl_summary or {}).get("realized"), fees=(pnl_summary or {}).get("fees"))
    if managed_state == "ok":
        man["realized_pnl_closed_strategies"] = ((pnl_summary or {}).get("realized_by_book") or {}).get("closed")
    ro_rows = [r for r in rows if r["kind"] == KIND_READ_ONLY]
    ro_known = [r for r in ro_rows if not r["trades_unknown"]]
    ro = _subtotal(rows, KIND_READ_ONLY, read_only_state,
                   realized=(round(sum(r["realized_pnl"] or 0.0 for r in ro_known), 2)
                             if (ro_known or not ro_rows) else None),
                   fees=_sum_known(*[r["fees"] for r in ro_known]))
    if read_only_state == "ok":
        ro["unpriced_coins"] = [c for r in ro_rows for c in r["unpriced_coins"]]
        ro["trades_unknown"] = [r["display_label"] for r in ro_rows if r["trades_unknown"]]
        ro["trades_capped"] = [r["display_label"] for r in ro_rows if r["trades_capped"]]
    value = _sum_known(man["value_usd"], ro["value_usd"])
    realized = _sum_known(man["realized_pnl"], ro["realized_pnl"])
    fees = (round(man["fees"] + ro["fees"], 2)
            if man["fees"] is not None and ro["fees"] is not None else None)
    excludes = {"couldnt_load": _couldnt_load(rows),
                "unpriced_coins": ro.get("unpriced_coins") or [],
                "unreadable": ([] if managed_state == "ok" else ["your Senpi strategies"])
                + ([] if read_only_state == "ok" else ["your saved wallets"])}

    def _side(sub, word, unread, tail=""):
        if sub["state"] != "ok":
            return f"{word} unknown ({unread})"
        if not sub["wallet_count"]:
            return f"{word} none"
        if sub["value_usd"] is None:
            return f"{word} unknown"
        n, m = sub["wallets_loaded"], sub["wallet_count"]
        count = f"{n} of {_plural(m, 'wallet')}" if n < m else _plural(m, 'wallet')
        return f"{word} {_usd(sub['value_usd'])} ({count}{tail})"

    m_txt = _side(man, "managed by Senpi", "your Senpi strategies couldn't be read")
    # no saved wallets (read fine, none added): no read-only clause — same as senpi-portfolio's book line
    r_txt = (None if read_only_state == "ok" and not ro["wallet_count"] else
             _side(ro, "read-only", "your saved wallets couldn't be loaded",
                   ", traded by hand — Senpi can't deploy it"))
    line = (f"Book value {_usd(value) if value is not None else 'unknown'}: {m_txt}"
            + (f" · {r_txt}." if r_txt else "."))
    if excludes["couldnt_load"]:
        n = len(excludes["couldnt_load"])
        line += (f" The total excludes {_plural(n, 'wallet')} that couldn't load "
                 f"({', '.join(excludes['couldnt_load'])}).")
    if excludes["unpriced_coins"]:
        line += f" The total excludes {', '.join(excludes['unpriced_coins'])} (no USD price)."
    for half in excludes["unreadable"]:
        line += f" The total excludes {half} (couldn't be read)."
    if realized is not None:
        whole = man["realized_pnl"] is not None and ro["realized_pnl"] is not None
        line += (f" Realized in the window, gross of fees: {_usd(realized)} across "
                 f"{'the book' if whole else 'the wallets that could be read'} — ")
        line += (f"managed by Senpi {_usd(man['realized_pnl'])}" if man["realized_pnl"] is not None
                 else "managed by Senpi unknown")
        closed = man.get("realized_pnl_closed_strategies")
        if closed:
            line += f" (incl. {_usd(closed)} from closed strategies)"
        if r_txt:
            line += (f" · read-only {_usd(ro['realized_pnl'])}" if ro["realized_pnl"] is not None
                     else " · read-only unknown")
        line += "."
        if ro.get("trades_capped"):
            line += (f" Read-only realized is at least that: {', '.join(ro['trades_capped'])} hit "
                     f"{HL_FILLS_CEILING_NOTE}.")
        if ro.get("trades_unknown"):
            line += (f" Realized excludes {', '.join(ro['trades_unknown'])} (trades couldn't be read).")
    return {"value_usd": value, "realized_pnl": realized, "fees": fees,
            "managed": man, "read_only": ro, "excludes": excludes, "line": line,
            "note": ("Quote `line` verbatim as the ONE book total — never sum the wallets yourself and never "
                     "build a 'Combined' row. The managed subtotal IS the Senpi numbers (pnl_summary); the "
                     "book total is not Senpi performance, and the read-only subtotal is never deployable "
                     "(idle / rebalance / strategy CTAs count managed money only).")}


def _deep_dive_worthy(r):
    """A row the deep-dive question offers: something to go deeper on — a non-zero value, an open
    position, a value that couldn't load (unknown is never "nothing there"), or trades to review (closed
    in the window, or unreadable). A read $0 with no position and no trades is a row of the list, never
    an option of the question (same rule as senpi-portfolio, plus trades: this is a trade review)."""
    if r.get("origin") == "main_wallet":
        return False                                          # a trade review: the main wallet has no trades
    v = r.get("value_usd")
    return (v is None or v != 0 or bool(r.get("open_position_count")) or bool(r.get("closed_trade_count"))
            or bool(r.get("trades_unknown")))


def _book(strat_reads, senpi_trades, open_book, saved_reads, entries, pnl_summary, meta, main=None):
    """The unified rows layer: every wallet the user has — the Senpi main wallet (`main`, from
    `fetch_main_wallet`), each CURRENT Senpi strategy and each saved wallet — in ONE list ordered by
    value (kind is a column), one book total with managed and read-only subtotals, one comparison line,
    and the deep-dive question. Present it first, then the detail. The managed subtotal is the main
    wallet + the strategies, the same money senpi-portfolio's `managed_usd` counts."""
    capped = {str(w).lower() for w in (meta.get("fills_capped") or [])}
    managed_state = ("unreadable" if any("strategy_list failed" in str(w) for w in (meta.get("warnings") or []))
                     else "ok")
    read_only_state = "ok" if saved_reads is not None else "unavailable"
    rows = (([_main_wallet_row(main)] if main else [])
            + _managed_rows(strat_reads, senpi_trades, open_book or {}, capped)
            + _read_only_rows(saved_reads, entries))
    rows.sort(key=_row_sort_key)
    seen = {}
    for r in rows:
        k = str(r.get("label") or "")
        seen[k] = seen.get(k, 0) + 1
    for r in rows:
        dup = seen.get(str(r.get("label") or ""), 0) > 1
        tell = _short_addr(r.get("wallet")) if r.get("wallet") else _plural(r.get("wallet_count") or 0, "wallet")
        r["display_label"] = f"{r.get('label')} ({tell})" if dup else r.get("label")
    # the question names only wallets with something to review (`_deep_dive_worthy`), in the rows' order
    names = [r["display_label"] for r in rows if _deep_dive_worthy(r)]
    return {
        "rows": rows,
        "total": _book_total(rows, pnl_summary, managed_state, read_only_state),
        "comparison": _book_comparison(rows),
        "deep_dive": ({"question": f"Which wallet do you want me to go deeper on: {_join_or(names)}?",
                       "order": names} if len(names) > 1 else None),
        "note": ("ONE list, in this order (value desc; couldn't-load last) — never re-section by origin; "
                 "`kind` is a column. Then the detail: the Senpi aggregates (pnl_summary leads them) and "
                 "external_wallets[]."),
    }


# ──────────────────────────────────────────────────────────────── shared state file (resumable steps)
# The step subcommands (timing → strategies → telemetry → market) are FAST, resumable slices that persist
# their work to a shared JSON state file so a later step never re-fetches what an earlier one already pulled.
# The agent runs them in sequence and NARRATES between — no single call carries the whole multi-minute review
# (which trips the exec timeout and makes the agent bail to raw MCP, losing every guardrail). Each step is
# idempotent + fail-open: a missing/corrupt state file → recompute (self-heal); every step also works
# STANDALONE (just slower). `all` writes the same state but prints the full composed dict (byte-identical to
# the pre-steps output). State default: <tempdir>/senpi-improve-trades/state-<window>d[-lastN].json.
STATE_SUBDIR = "senpi-improve-trades"


def _default_state_path(window_days, last_n):
    """Default shared-state path, keyed by the review window so distinct windows don't clobber each other:
    <tempdir>/senpi-improve-trades/state-<window>d[-lastN].json. Uses tempfile.gettempdir() (never $HOME)."""
    wd = window_days
    wd = int(wd) if float(wd).is_integer() else wd
    name = f"state-{wd}d" + (f"-last{last_n}" if last_n else "") + ".json"
    return os.path.join(tempfile.gettempdir(), STATE_SUBDIR, name)


def _load_state(path):
    """Read the shared state JSON. Never raises — a missing/corrupt/unreadable file → {} (fail-open: the
    step then recomputes its prerequisites and self-heals)."""
    if not path or not os.path.isfile(path):
        return {}
    try:
        with open(path) as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa — corrupt/unreadable state is fail-open → recompute
        return {}


def _save_state(path, state):
    """Merge-write the shared state JSON (best-effort; a write failure never sinks the step — the slice was
    already printed to stdout). Creates the parent dir. Atomic-ish via a temp file + replace."""
    if not path:
        return
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(state, fh)
        os.replace(tmp, path)
    except Exception:  # noqa — persistence is best-effort; the printed slice is the contract
        pass


def _fresh_meta():
    """A meta skeleton seeded like run()'s — the same `sources` list + fail-open scaffolding, so every step's
    meta reads consistently whether it ran standalone or off state."""
    return {"warnings": [], "sources": ["discovery_get_trader_history", "ratchet_stop_list",
                                        "market_get_asset_data", "leaderboard_get_markets",
                                        "openclaw senpi events"], "degraded": None}


def _window_for(window_days, last_n, now_ms=None):
    """The review window dict (identical shape to run()'s), plus the (since_ms, until_ms) bounds."""
    since_ms, until_ms = _window_bounds(window_days, now_ms=now_ms)
    label = f"last {last_n} closed trades" if last_n else f"last ~{window_days}d"
    window = {"from": since_ms, "to": until_ms, "label": label, "window_days": window_days, "last_n": last_n}
    return window, since_ms, until_ms


def _ensure_trades_in_state(client, state, window_days, last_n, want_market, now_ms=None):
    """Self-heal: return (strategies, trades, window, since_ms) — from the state file when the timing step
    already ran, else recompute the discovery trade fetch + prices right here (so EVERY step works
    standalone). The recompute is the timing step's engine core (enrich_exit=False → no telemetry latency).
    Also rebuilds the meta warnings the fetch produced. Merges its work back into state for the next step."""
    window, since_ms, until_ms = _window_for(window_days, last_n, now_ms=now_ms)
    strategies = state.get("strategies")
    trades = state.get("trades")
    if isinstance(strategies, list) and isinstance(trades, list):
        return strategies, trades, state.get("window", window), since_ms
    # state absent/partial → recompute the timing slice (discovery + prices, no telemetry/ratchet).
    meta = _fresh_meta()
    strategies = fetch_review_set(client, meta)
    trades, _ms2, _lk, _fl = _collect_trades(
        client, strategies, meta, since_ms, until_ms, last_n, want_market, enrich_exit=False)
    state["strategies"] = strategies
    state["trades"] = trades
    state["window"] = window
    for k in ("external_wallets_status", "closed_trades_unknown", "fills_capped", "main_wallet_address",
              "user_get_me_failed"):
        if k in meta:
            state[k] = meta[k]
    state.setdefault("meta_warnings", [])
    state["meta_warnings"] = meta.get("warnings", [])
    state["registry_source"] = meta.get("registry_source")
    return strategies, trades, window, since_ms


# ──────────────────────────────────────────────────────────── step subcommands (fast, resumable, standalone)
def step_timing(client, window_days=WINDOW_DEFAULT_DAYS, last_n=None, want_market=True,
                state_path=None, now_ms=None):
    """STEP 1 `timing` — the bottleneck-but-headline slice the agent NARRATES FIRST. Fetch strategies +
    closed trades (discovery, the parallelized path) + dedup/parallel prices → `trades[]` (exit_reason still
    UNKNOWN here — telemetry fills it later) + `timing_summary` + `window`. Persists trades + the strategies
    list to state so `strategies`/`telemetry`/`market` don't re-fetch. Fast: NO telemetry, NO ratchet."""
    if state_path is None:
        state_path = _default_state_path(window_days, last_n)
    state = _load_state(state_path)
    meta = _fresh_meta()
    window, since_ms, until_ms = _window_for(window_days, last_n, now_ms=now_ms)
    meta["window"] = window
    strategies = fetch_review_set(client, meta)
    trades, _ms2, _lk, _fl = _collect_trades(
        client, strategies, meta, since_ms, until_ms, last_n, want_market, enrich_exit=False)
    senpi_trades = [t for t in trades if t.get("wallet_kind") != EXTERNAL]
    timing = _timing_summary(senpi_trades)
    meta["trade_count"] = len(senpi_trades)
    _stamp_external_meta(meta, strategies, trades)
    meta.pop("_telemetry_warned", None)
    meta.pop("_telemetry_dead", None)
    degraded = _degraded(_senpi_only(strategies), strategies, trades, meta)
    if degraded:
        meta["degraded"] = degraded
    # persist the raw fetch for downstream steps (strategies carries mandate/dsl/runtime_id; trades carries
    # the discovery facts + placeholder exit_reason the telemetry step will overwrite).
    state["window"] = window
    state["strategies"] = strategies
    state["trades"] = trades
    state["timing_summary"] = timing
    state["meta_warnings"] = meta.get("warnings", [])
    state["registry_source"] = meta.get("registry_source")
    for k in ("external_wallets_status", "closed_trades_unknown", "fills_capped", "main_wallet_address",
              "user_get_me_failed"):
        if k in meta:
            state[k] = meta[k]
    saved = (_external_reads(trades, strategies, meta)
             if meta.get("external_wallets_status") == EXTERNAL_OK else None)   # None, never [], when unloaded
    state["external_wallets"] = saved
    _save_state(state_path, state)
    return {"window": window, "trades": trades, "timing_summary": timing,
            "external_wallets": saved, "meta": meta}


def step_strategies(client, window_days=WINDOW_DEFAULT_DAYS, last_n=None, want_market=True,
                    state_path=None, now_ms=None):
    """STEP 2 `strategies` — the per-strategy read (CURRENT-book verdict surface). Reads state (or self-heals
    the trade fetch when state is absent): `strategies[]` (mandate/DSL from the registry + realized-PnL
    rollup + `dsl_close_reason_mix` from whatever exit_reason is in state) + `closed_strategies[]` + the meta
    current/closed counts. Cheap — no network beyond the self-heal fetch."""
    if state_path is None:
        state_path = _default_state_path(window_days, last_n)
    state = _load_state(state_path)
    meta = _fresh_meta()
    strategies, trades, window, _since = _ensure_trades_in_state(
        client, state, window_days, last_n, want_market, now_ms=now_ms)
    meta["warnings"] = list(state.get("meta_warnings", []))
    meta["window"] = window
    for k in ("external_wallets_status", "closed_trades_unknown", "fills_capped", "main_wallet_address",
              "user_get_me_failed"):
        if k in state:
            meta[k] = state[k]
    senpi = _senpi_only(strategies)
    senpi_trades = [t for t in trades if t.get("wallet_kind") != EXTERNAL]
    open_book = fetch_open_book(client, senpi, meta)   # unrealized PnL for current wallets (total ledger)
    main = fetch_main_wallet(client, meta.get("main_wallet_address"), meta)
    strat_reads = _strategy_reads(senpi_trades, senpi, open_book)
    closed_reads = _closed_strategy_rollup(senpi_trades, senpi)
    realized_total = round(sum(_num(t.get("realized_pnl")) or 0.0 for t in senpi_trades), 2)
    # None (not 0.0) when no row carried a fee, so the narrator says 'gross' instead of implying net.
    _fees = [x for x in (_num(t.get("fee")) for t in senpi_trades) if x is not None]
    fees_total = round(sum(_fees), 2) if _fees else None
    pnl_summary = _pnl_summary(realized_total, strat_reads, fees_total)
    dsl_mix = _dsl_close_reason_mix(senpi_trades)   # from whatever exit_reason is in state (UNKNOWN until telemetry)
    saved = (_external_reads(trades, strategies, meta)
             if meta.get("external_wallets_status") == EXTERNAL_OK else None)   # None, never [], when unloaded
    book = _book(strat_reads, senpi_trades, open_book, saved, strategies, pnl_summary, meta, main=main)
    current_count = sum(1 for s in senpi if _is_current(s.get("status")))
    closed_count = len(senpi) - current_count
    meta["strategy_count"] = len(senpi)
    meta["current_strategy_count"] = current_count
    meta["closed_strategy_count"] = closed_count
    meta["trade_count"] = len(senpi_trades)
    _stamp_external_meta(meta, strategies, trades)
    degraded = _degraded(senpi, strategies, trades, meta) if not senpi else None
    if degraded:
        meta["degraded"] = degraded
    state["strategies_read"] = strat_reads
    state["closed_strategies"] = closed_reads
    state["pnl_summary"] = pnl_summary
    state["dsl_close_reason_mix"] = dsl_mix
    state["book"] = book
    _save_state(state_path, state)
    return {"book": book, "strategies": strat_reads, "closed_strategies": closed_reads,
            "pnl_summary": pnl_summary, "dsl_close_reason_mix": dsl_mix, "meta": meta}


def step_telemetry(client, window_days=WINDOW_DEFAULT_DAYS, last_n=None, want_market=True,
                   state_path=None, now_ms=None):
    """STEP 3 `telemetry` — the per-runtime event shell-outs, ISOLATED so their latency never blocks the fast
    slices. Enriches the state trades' `exit_reason` (telemetry-native → ratchet → UNKNOWN) IN PLACE, and
    produces `missed_signals[]`, `blocked_summary`, `leaks`, `execution_quality`, plus a REFRESHED
    `dsl_close_reason_mix` (now that exit reasons are filled) + the meta telemetry rollup. Keeps the
    `_telemetry_dead` short-circuit. Self-heals the trade fetch if state is absent."""
    if state_path is None:
        state_path = _default_state_path(window_days, last_n)
    state = _load_state(state_path)
    meta = _fresh_meta()
    strategies, trades, window, since_ms = _ensure_trades_in_state(
        client, state, window_days, last_n, want_market, now_ms=now_ms)
    meta["warnings"] = list(state.get("meta_warnings", []))
    meta["window"] = window
    missed_signals, leaks, fills = _enrich_exit_and_streams(client, trades, strategies, meta, since_ms)
    senpi_trades = [t for t in trades if t.get("wallet_kind") != EXTERNAL]
    blocked = _blocked_summary(missed_signals)
    exec_quality = _execution_quality(fills)
    dsl_mix = _dsl_close_reason_mix(senpi_trades)    # REFRESH — exit reasons are now filled in
    src_counts = _exit_reason_source_counts(senpi_trades)
    meta["exit_reason_source_counts"] = src_counts
    meta["telemetry_source"] = _telemetry_source(src_counts, meta.get("_telemetry_warned", False))
    coverage = _exit_attribution_coverage(src_counts, len(senpi_trades))
    meta["exit_attribution_coverage"] = coverage
    telemetry_availability = _telemetry_availability(coverage, meta["telemetry_source"])
    meta["missed_signal_count"] = len(missed_signals)
    meta["leak_counts"] = {k: v["count"] for k, v in leaks.items()}
    meta["trade_count"] = len(senpi_trades)
    meta.pop("_telemetry_warned", None)
    meta.pop("_telemetry_dead", None)
    # persist the enriched trades (exit_reason now filled) + the streams for `all`-parity re-reads.
    state["trades"] = trades
    state["missed_signals"] = missed_signals
    state["blocked_summary"] = blocked
    state["leaks"] = leaks
    state["execution_quality"] = exec_quality
    state["dsl_close_reason_mix"] = dsl_mix
    state["telemetry_availability"] = telemetry_availability
    state["meta_warnings"] = meta.get("warnings", [])
    _save_state(state_path, state)
    return {"trades": trades, "missed_signals": missed_signals, "blocked_summary": blocked,
            "leaks": leaks, "execution_quality": exec_quality, "dsl_close_reason_mix": dsl_mix,
            "telemetry_availability": telemetry_availability, "meta": meta}


def step_market(client, window_days=WINDOW_DEFAULT_DAYS, last_n=None, want_market=True,
                state_path=None, now_ms=None):
    """STEP 4 `market` — `book_vs_market` (leaderboard movers × the assets the book held). Reads the held
    set from the state trades (self-heals the trade fetch if absent). Skipped-to-empty when --no-market."""
    if state_path is None:
        state_path = _default_state_path(window_days, last_n)
    state = _load_state(state_path)
    meta = _fresh_meta()
    strategies, trades, window, _since = _ensure_trades_in_state(
        client, state, window_days, last_n, want_market, now_ms=now_ms)
    meta["warnings"] = list(state.get("meta_warnings", []))
    meta["window"] = window
    bvm = book_vs_market(client, trades, strategies, meta, want_market)
    state["book_vs_market"] = bvm
    state["meta_warnings"] = meta.get("warnings", [])
    _save_state(state_path, state)
    return {"book_vs_market": bvm, "meta": meta}


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
                                        "market_get_asset_data", "leaderboard_get_markets",
                                        "openclaw senpi events"], "degraded": None}
    since_ms, until_ms = _window_bounds(window_days, now_ms=now_ms)
    label = f"last {last_n} closed trades" if last_n else f"last ~{window_days}d"
    window = {"from": since_ms, "to": until_ms, "label": label, "window_days": window_days,
              "last_n": last_n}
    meta["window"] = window

    # ALL statuses are enumerated (so a churned book's CLOSED trades stay in trades[]) — but the per-strategy
    # verdict is CURRENT-only. Closed/historical strategies get a minimal rollup, never a verdict.
    strategies = fetch_review_set(client, meta)        # Senpi strategies + saved wallets
    senpi = _senpi_only(strategies)
    # OPEN BOOK — current strategies' unrealized PnL (strategy_get_clearinghouse_state), so the review is a
    # TOTAL ledger (realized closed + unrealized open), not realized-only. Fail-open per wallet → None (UNKNOWN).
    # Saved wallets' open book is their `state` (account_get_external_wallets), read in fetch_review_set.
    open_book = fetch_open_book(client, senpi, meta)
    main = fetch_main_wallet(client, meta.get("main_wallet_address"), meta)   # the book's Senpi main wallet row
    # DISCOVERY owns trades[] (onchain facts); TELEMETRY enriches exit_reason + yields the standalone streams
    # (missed_signals + the leak/fill rollups), all from ONE per-runtime event fetch (no re-fetch downstream).
    trades, missed_signals, leaks, fills = _collect_trades(
        client, strategies, meta, since_ms, until_ms, last_n, want_market)
    # Senpi aggregates stay Senpi-only; saved wallets get their own read (external_wallets[]).
    senpi_trades = [t for t in trades if t.get("wallet_kind") != EXTERNAL]
    timing = _timing_summary(senpi_trades)
    # telemetry-derived quick-action aggregations — ALL reuse the already-fetched events + existing trades[].
    dsl_mix = _dsl_close_reason_mix(senpi_trades)               # 'shaken out too early / how exits fire'
    blocked = _blocked_summary(missed_signals)                  # 'what did my own limits block'
    exec_quality = _execution_quality(fills)                    # 'fees — maker vs taker'
    bvm = book_vs_market(client, trades, strategies, meta, want_market)
    strat_reads = _strategy_reads(senpi_trades, senpi, open_book)   # CURRENT book — now with unrealized/total/open
    closed_reads = _closed_strategy_rollup(senpi_trades, senpi) # HISTORY: closed/inactive, rollup only
    pnl_summary = _pnl_summary(timing["realized_pnl_total"], strat_reads)   # realized + unrealized = TOTAL ledger
    # read-only wallets the user trades by hand; None (never []) when they couldn't be loaded
    saved = (_external_reads(trades, strategies, meta)
             if meta.get("external_wallets_status") == EXTERNAL_OK else None)
    # ONE book on top of the Senpi-only aggregates: every wallet by value, one total with subtotals.
    book = _book(strat_reads, senpi_trades, open_book, saved, strategies, pnl_summary, meta, main=main)

    current_count = sum(1 for s in senpi if _is_current(s.get("status")))
    closed_count = len(senpi) - current_count
    meta["strategy_count"] = len(senpi)                        # every enumerated strategy (all statuses)
    meta["current_strategy_count"] = current_count            # the LIVE book — the "how many wallets" number
    meta["closed_strategy_count"] = closed_count              # churned/closed redeployments — HISTORY
    meta["trade_count"] = len(senpi_trades)
    _stamp_external_meta(meta, strategies, trades)
    # telemetry rollup — how enrichment did (never affects the discovery trade list, only enrichment).
    src_counts = _exit_reason_source_counts(senpi_trades)
    meta["exit_reason_source_counts"] = src_counts             # telemetry / ratchet / unknown
    meta["telemetry_source"] = _telemetry_source(src_counts, meta.get("_telemetry_warned", False))
    coverage = _exit_attribution_coverage(src_counts, len(senpi_trades))
    meta["exit_attribution_coverage"] = coverage               # attributed vs UNKNOWN — 0 attributed → no calibration claim
    telemetry_availability = _telemetry_availability(coverage, meta["telemetry_source"])
    meta["missed_signal_count"] = len(missed_signals)
    meta["leak_counts"] = {k: v["count"] for k, v in leaks.items()}   # quick meta glance at the leak tallies
    meta.pop("_telemetry_warned", None)                        # internal flag — not part of the contract
    degraded = _degraded(senpi, strategies, trades, meta)
    if degraded:
        meta["degraded"] = degraded

    return {
        "window": window,
        "book": book,                     # ONE list by value + one total w/ subtotals — PRESENT FIRST, then the detail
        "trades": trades,                 # DISCOVERY-owned onchain facts + telemetry-enriched exit_reason
        "timing_summary": timing,         # PROCESS-framed counts — LEAD with these
        "dsl_close_reason_mix": dsl_mix,  # 'shaken out too early / how exits fire' → DSL preset lever
        "book_vs_market": bvm,            # the honest 'what did I miss' gap (movers the book didn't hold)
        "missed_signals": missed_signals, # TELEMETRY-native 'what did I miss' — blocked/rejected signals
        "blocked_summary": blocked,       # 'what did my own limits block' → slot / margin / risk-gate lever
        "leaks": leaks,                   # 'where am I leaking' — failed orders, protection gaps, risk halts
        "execution_quality": exec_quality, # 'fees — maker vs taker' (+ future authoritative-fee ledger hook)
        "pnl_summary": pnl_summary,       # TOTAL ledger: realized + unrealized (+ current/closed split) — LEAD with this
        "strategies": strat_reads,        # CURRENT book only — each judged vs its OWN mandate (+ unrealized/total/open)
        "closed_strategies": closed_reads, # HISTORY — minimal rollup, NO verdict/mandate (never consolidate)
        "telemetry_availability": telemetry_availability,   # undetermined ≠ all-clear signal for the narrator
        "external_wallets": saved,   # READ-ONLY wallets traded by hand — own read, never in the Senpi totals
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


_STEPS = ("timing", "strategies", "telemetry", "market", "all")
_STEP_FNS = {"timing": step_timing, "strategies": step_strategies,
             "telemetry": step_telemetry, "market": step_market}


def _all_and_persist(client, window_days, last_n, want_market, state_path, now_ms=None):
    """`all` = the composed one-shot. Runs the UNCHANGED `run()` (its output is byte-identical to the
    pre-steps engine) and ALSO writes the shared state file (same shape the steps build) so an `all` run can
    seed a later narrow step. The state write never alters the printed dict."""
    result = run(client, window_days=window_days, last_n=last_n, want_market=want_market, now_ms=now_ms)
    if state_path is None:
        state_path = _default_state_path(window_days, last_n)
    state = {
        "window": result.get("window"),
        "book": result.get("book"),
        "strategies": None,   # `all` doesn't retain the raw strategy list; steps self-heal by re-fetching
        "trades": result.get("trades"),
        "timing_summary": result.get("timing_summary"),
        "dsl_close_reason_mix": result.get("dsl_close_reason_mix"),
        "book_vs_market": result.get("book_vs_market"),
        "missed_signals": result.get("missed_signals"),
        "blocked_summary": result.get("blocked_summary"),
        "leaks": result.get("leaks"),
        "execution_quality": result.get("execution_quality"),
        "strategies_read": result.get("strategies"),
        "closed_strategies": result.get("closed_strategies"),
        "pnl_summary": result.get("pnl_summary"),
        "telemetry_availability": result.get("telemetry_availability"),
        "external_wallets": result.get("external_wallets"),
        "meta_warnings": (result.get("meta") or {}).get("warnings", []),
    }
    _save_state(state_path, state)
    return result


# ──────────────────────────────────────────────────────── stdout slimming (context-cost control)
# review.py's stdout IS the model's context on the next turn. The narrator writes from the AGGREGATES
# (pnl_summary / timing_summary / strategies / dsl_close_reason_mix) — never the raw per-trade rows. On
# a big multi-strategy account the full `trades[]` is 40-60k tokens of prefill it doesn't need, which is
# most of the model time and blows the delivery timeout (the samurai-pro >60s tail hits power users
# hardest). So the STDOUT payload carries a top-N OUTLIER SAMPLE + counts; the on-disk state file keeps
# the COMPLETE arrays for the stepped path. `--full` restores everything (debug / "the whole ledger").
STDOUT_TRADES_SAMPLE = 12
STDOUT_MISSED_SAMPLE = 10
_TRADE_STDOUT_FIELDS = ("asset", "direction", "strategy_label", "realized_pnl", "close_time",
                        "exit_vs_hold", "price_since_exit_pct", "if_held_delta_usd", "exit_reason",
                        "wallet_kind")


def _slim_trade(t):
    return {k: t.get(k) for k in _TRADE_STDOUT_FIELDS if k in t} if isinstance(t, dict) else t


def _sample_trades(trades):
    """The outliers a coach actually calls out — the biggest realized moves AND the biggest hold-to-now
    deltas ('biggest miss') — deduped, newest-first, trimmed to the narratable fields. NOT all N rows."""
    if not isinstance(trades, list) or len(trades) <= STDOUT_TRADES_SAMPLE:
        return [_slim_trade(t) for t in (trades or [])]
    by_pnl = sorted(trades, key=lambda t: abs(_num(t.get("realized_pnl")) or 0), reverse=True)
    by_hold = sorted(trades, key=lambda t: abs(_num(t.get("if_held_delta_usd")) or 0), reverse=True)
    picked, seen = [], set()
    for t in by_pnl[:STDOUT_TRADES_SAMPLE] + by_hold[:STDOUT_TRADES_SAMPLE]:
        if id(t) not in seen:
            seen.add(id(t))
            picked.append(t)
    picked.sort(key=lambda t: _num(t.get("close_time")) or 0, reverse=True)
    return [_slim_trade(t) for t in picked[:STDOUT_TRADES_SAMPLE]]


# ── book_state — the deterministic "what should happen next" signal ──────────
# A user can reach this skill with an empty account. That is not a fault, and it is not the same
# situation as a deployed strategy that hasn't fired — those need OPPOSITE answers:
#
#   no_strategies         nothing deployed  -> market-pulse + strategy-discover (find a fit for THIS market)
#   strategies_no_trades  deployed, idle    -> diagnose THAT strategy. NEVER pitch another one here.
#   has_trades            normal review
#
# Telling someone whose funded strategy is silently blocked to "go find a strategy" is the worst possible
# answer, so the two are separated in the engine rather than left to narration.
_BOOK_STATES = ("no_strategies", "strategies_no_trades", "external_no_trades", "has_trades", "unknown")


def _book_state(strategy_count, trade_count, list_failed, external_count=0, external_trade_count=0,
                external_status="ok", external_unknown_count=0):
    """(state, next_action) — what the narrator should do next. `list_failed` distinguishes a genuine
    empty book from an unreadable one (a token/scope problem), which must never read as 'no strategies'.
    `trade_count` is Senpi trades only; saved wallets (traded by hand) count separately, and a user
    who has them is never pitched a strategy. `external_status` "unavailable" is unknown, not none, and a
    saved wallet whose fills couldn't be read (`external_unknown_count`) is unknown, never "no trades"."""
    if list_failed:
        return "unknown", ("strategy list unreadable — this is a TOKEN/SCOPE problem, not an empty book; "
                           "say the read failed, never 'you have no strategies'")
    if not strategy_count:
        if external_count and external_trade_count:
            return "has_trades", ("lead with external_wallets[] — manual trades, read-only; the Senpi "
                                  "aggregates (pnl_summary / timing_summary / leaks) are Senpi-only and empty "
                                  "here. For leaks or 'what did I miss' on these wallets run quant-desk. No "
                                  "Senpi strategy to tune: never pitch one.")
        if external_count and external_unknown_count:
            return "unknown", ("couldn't read the trade history on the saved wallet(s) — say so, never "
                               "'no trades'; pitch nothing")
        if external_count:
            return "external_no_trades", ("the saved wallet(s) closed no trades in this window — say "
                                          "that in one line and offer a longer --window. They trade it by "
                                          "hand: do NOT pitch a strategy.")
        if external_status != "ok":
            return "unknown", ("no Senpi strategies, and the saved wallets couldn't be loaded — say you "
                               "couldn't load them, never 'nothing to review', and pitch nothing until "
                               "they load")
        return "no_strategies", ("nothing deployed yet — there is genuinely nothing to review. Pivot: read "
                                 "the market (senpi-market-pulse), then shortlist strategies that fit it "
                                 "(senpi-strategy-discover). Do NOT manufacture a review.")
    unloaded = ("" if external_status == "ok" else
                " Your saved wallets couldn't be loaded — say so; never 'you have none'.")
    if not trade_count and not external_trade_count:
        return "strategies_no_trades", ("deployed but nothing has traded yet — diagnose the strategy they "
                                        "ALREADY have. Do NOT pitch another strategy." + unloaded)
    return "has_trades", "normal review" + unloaded


def _slim_for_context(result, full=False):
    """Trim the STDOUT payload (what enters the model's context) — NEVER the on-disk state. Replaces the
    raw `trades` / `missed_signals` arrays with a top-N sample + explicit counts; every AGGREGATE is left
    untouched (the narration reads those). `full=True` returns the result unchanged."""
    if not isinstance(result, dict):
        return result
    # book_state on EVERY output path (one-shot and every step) — the narrator must never have to infer
    # "is this an empty book, an idle book, or a real review?" from absent arrays.
    meta = result.get("meta") if isinstance(result.get("meta"), dict) else None
    if meta is not None and "book_state" not in meta:
        warns = meta.get("warnings") or []
        list_failed = any("strategy_list failed" in str(w) for w in warns)
        n_strat = meta.get("strategy_count")
        if n_strat is not None:
            state, nxt = _book_state(n_strat, meta.get("trade_count") or 0, list_failed,
                                     meta.get("external_wallet_count") or 0,
                                     meta.get("external_trade_count") or 0,
                                     meta.get("external_wallets_status", "ok"),
                                     meta.get("external_unknown_count") or 0)
            meta["book_state"] = state
            meta["next_action"] = nxt
    if full:
        return result
    out = dict(result)
    trades = out.get("trades")
    if isinstance(trades, list):
        sampled = _sample_trades(trades)                      # key kept; field-trimmed rows
        out["trades"] = sampled
        if len(trades) > len(sampled):                        # only when we actually dropped rows
            out["trades_sample"] = {
                "shown": len(sampled), "total": len(trades),
                "note": "OUTLIER SAMPLE only (biggest realized + biggest hold-to-now) — the aggregates "
                        "(timing_summary / pnl_summary / dsl_close_reason_mix) cover ALL trades. Full "
                        "ledger: re-run with --full. Never imply the sample is the whole book."}
    ms = out.get("missed_signals")
    if isinstance(ms, list) and len(ms) > STDOUT_MISSED_SAMPLE:
        out["missed_signals"] = ms[:STDOUT_MISSED_SAMPLE]
        out["missed_signals_sample"] = {"shown": STDOUT_MISSED_SAMPLE, "total": len(ms)}
    return out


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    # optional leading positional STEP (timing|strategies|telemetry|market|all); default `all` = the
    # composed one-shot (unchanged output + shape). Parsed before argparse so the flags stay shared.
    step = "all"
    if argv and not argv[0].startswith("-"):
        cand = argv[0]
        if cand not in _STEPS:
            print(json.dumps({"trades": [], "meta": {"error": f"unknown step {cand!r}; "
                                                     f"expected one of {', '.join(_STEPS)}"}}))
            return 1
        step, argv = cand, argv[1:]

    ap = argparse.ArgumentParser(
        description="senpi improve-trades engine (retrospective review + coaching). Optional leading STEP: "
                    "timing|strategies|telemetry|market|all (default all = the composed one-shot). Steps "
                    "share a state file so later steps don't re-fetch.")
    ap.add_argument("--window", type=float, default=WINDOW_DEFAULT_DAYS,
                    help="review window in days (default ~7)")
    ap.add_argument("--last", type=int, default=None,
                    help="cap to the last N closed trades per wallet (still within the window)")
    ap.add_argument("--no-market", action="store_true",
                    help="skip the current-price + book-vs-market pull")
    ap.add_argument("--state", default=None,
                    help="shared state file path (default <tempdir>/senpi-improve-trades/state-<window>d.json)")
    ap.add_argument("--fixture", help="offline: path to a recorded MCP-response map (tests only)")
    ap.add_argument("--dry", action="store_true", help="dump raw MCP responses for schema debugging")
    ap.add_argument("--full", action="store_true",
                    help="emit the COMPLETE trades/missed_signals arrays; default is a top-N outlier "
                         "sample to keep the model-context payload small (aggregates are always complete)")
    # `step` was already peeled off argv above; feed the remainder (flags only).
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

    want_market = not args.no_market
    try:
        if step == "all":
            result = _all_and_persist(client, args.window, args.last, want_market, args.state)
        else:
            fn = _STEP_FNS[step]
            result = fn(client, window_days=args.window, last_n=args.last,
                        want_market=want_market, state_path=args.state)
    except Exception as e:  # noqa
        print(json.dumps({"trades": [], "meta": {"error": f"engine failure: {e}"}}))
        return 1
    print(json.dumps(_slim_for_context(result, args.full), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
