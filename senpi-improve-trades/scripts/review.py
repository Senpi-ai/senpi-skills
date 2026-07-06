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

TWO KINDS OF REALIZED PROFIT — two discovery sources (never confuse them):
  • FULLY-CLOSED trades (the position is gone) → `discovery_get_trader_history` (closedPositions[]) — this
    is `trades[]`, the timing review.
  • REALIZED-TAKEN-ON-OPEN (profit ALREADY banked on a STILL-OPEN position: TP/SL fills, partial closes,
    size reductions) → `discovery_get_open_position_realized_pnl`. A partial close leaves the position OPEN,
    so it creates NO closedPositions[] entry — trader_history alone reports "no closed trades" even after the
    user took, say, 80% profit off two live positions. `fetch_partial_closes()` reads each open position (via
    strategy_get_clearinghouse_state) and asks discovery how much realized PnL was already taken on it, so the
    review SEES the profit-taking. Surfaced as the standalone `partial_closes[]` stream + meta flags.

⚠ All tools here are USER-scoped (your own account): needs a USER-scoped SENPI_AUTH_TOKEN.
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import argparse
import concurrent.futures
import datetime
import json
import os
import subprocess
import sys
import tempfile
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

# ── Telemetry (the runtime event log) — ENRICHES discovery trades, never becomes the trade list ──
# Onchain data → discovery (the trade list, prices, realized PnL, fees, timing, direction). Runtime/agent
# events → telemetry (the per-strategy on-disk event ring, read via `openclaw senpi events`). Telemetry
# fills each discovery trade's EXIT REASON and, as a standalone stream, the blocked/rejected signal cohort
# ("what did I miss"). It NEVER reconstructs a trade or re-derives a price/PnL — discovery OWNS those.
EVENTS_FIXTURE_ENV = "SENPI_EVENTS_FIXTURE"   # offline test hook: JSON {"<runtime_id>": [entries…]}
EVENTS_PULL = 500                             # events to pull per runtime before matching (recent ring)
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
            # the runtime id the event-log CLI is addressed by (`openclaw senpi events --runtime <id>`);
            # None for a closed/deregistered strategy with no ring → telemetry enrichment skips it.
            "runtime_id": runtime_ids.get(str(wallet).lower()),
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
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=8)
    except FileNotFoundError:                # no `openclaw` on PATH (not a runtime host)
        meta["_telemetry_dead"] = True       # no CLI at all → every runtime fails; stop shelling out
        _note_telemetry_unavailable(meta, "openclaw CLI not found; exit reasons from ratchet fallback only")
        return []
    except Exception as e:  # noqa — timeout / OS error → fail-open
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


def _direction(rec, szi, entry_px, exit_px, pnl):
    """Direction of a CLOSED trade, robustly. `szi` is unreliable on a fully-closed position (the
    at-close size is often 0), which read wrong/ambiguous. Order: (1) an explicit dir/side field;
    (2) a non-zero szi sign; (3) DERIVE from realized PnL vs the price move — a LONG books profit when
    price rises (pnl and (exit−entry) share a sign), a SHORT when price falls (opposite signs). Returns
    None only when nothing resolves it (e.g. a flat trade with no move)."""
    d = str(_field(rec, "dir", "side", "direction", "positionSide", default="") or "").strip().lower()
    if d in ("long", "buy", "b", "bid", "l"):
        return "long"
    if d in ("short", "sell", "a", "ask", "s"):
        return "short"
    if szi and szi != 0:
        return "long" if szi > 0 else "short"
    move = (exit_px - entry_px) if (entry_px is not None and exit_px is not None) else None
    if move and pnl:
        return "long" if ((move > 0) == (pnl > 0)) else "short"
    return None


def fetch_closed_trades(client, wallet, since_ms, until_ms, cap, meta):
    """Read-guarded closed-position ledger for one strategy wallet, filtered to the review window. Lifts
    portfolio.py's fetch_closed extraction (the real discovery_get_trader_history shape: closedPositions[]
    of coin, signed szi, string realizedPnl, Unix-ms openTime/closeTime, entryPx/exitPx). `since_ms`
    filters by closeTime; `cap` (optional) keeps only the last N by close time. Fails OPEN → []."""
    try:
        h = _ok(client.mcp_call("discovery_get_trader_history", trader_address=wallet,
                                sort_by="CLOSED_TIME", sort_direction="DESC",
                                limit=CLOSED_HISTORY_PULL, timeout=12))
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
        entry_px = _num(_field(p, "entryPx", "entry_px"))
        exit_px = _num(_field(p, "exitPx", "exit_px"))
        pnl = round(_f(p, "realizedPnl", "realized_pnl", default=0.0), 2)
        trades.append({
            "asset": _field(p, "coin", "coinDisplayName", "asset"),
            "direction": _direction(p, szi, entry_px, exit_px, pnl),   # robust: field → szi → pnl-vs-move
            "size": abs(szi),
            "leverage": _f(lev, "value", default=None) if isinstance(lev, dict) else _num(lev),
            "entry_px": entry_px,
            "exit_px": exit_px,
            "realized_pnl": pnl,
            "margin_used": _f(p, "marginUsed", "margin_used", default=None),
            "open_time": _ms(_field(p, "openTime", "open_time")),
            "close_time": close_ms,
            "closed_order_id": _field(p, "closedOrderId", "closed_order_id"),
        })
    trades.sort(key=lambda t: _num(t.get("close_time")) or 0, reverse=True)
    if cap:
        trades = trades[:cap]
    return trades


# ──────────────────────────────── partial closes — realized profit ALREADY TAKEN on a STILL-OPEN position
# The bug this fixes: a partial close / TP / SL fill / size reduction on a position that STAYS OPEN books
# realized PnL but creates NO closedPositions[] entry, so `fetch_closed_trades` (trader_history) reports
# "no closed trades" even after the user took real profit off live positions — and guardrail 9 then wrongly
# frames it as "brand-new, nothing to review." SOURCE SPLIT: the OPEN (wallet, coin) set comes from
# strategy_get_clearinghouse_state (the same main+xyz / assetPositions[] read senpi-portfolio uses); the
# realized-taken $ comes from ONE batched discovery_get_open_position_realized_pnl over all those pairs.
def _open_positions_for(client, strat, meta):
    """Read one strategy wallet's OPEN positions from strategy_get_clearinghouse_state → a list of
    {coin, dex, notional} (notional = current positionValue). Mirrors senpi-portfolio's extraction: both
    DEX views (main + xyz), assetPositions[], skip szi==0. Read-guarded + fail-open → [] + a meta.warnings
    note. Onchain-only; no PnL here — that's the discovery call in fetch_partial_closes."""
    wallet = strat.get("wallet")
    out = []
    try:
        ch = _ok(client.mcp_call("strategy_get_clearinghouse_state", strategy_wallet=wallet, timeout=20))
    except Exception as e:  # noqa — fail-open: this wallet contributes no open positions
        meta.setdefault("warnings", []).append(
            f"clearinghouse {str(wallet)[:8]} failed: {e}; partial-close read skipped for it")
        return out
    if not isinstance(ch, dict):
        return out
    for dex in ("main", "xyz"):
        d = _field(ch, dex, default={}) if isinstance(ch, dict) else {}
        if not isinstance(d, dict):
            continue
        for ap in (_field(d, "assetPositions", "asset_positions", default=[]) or []):
            pos = _field(ap, "position", default=ap) or {}
            if not isinstance(pos, dict):
                continue
            szi = _f(pos, "szi", "size", default=0.0)
            if szi == 0:
                continue                      # flat leg — not an open position
            coin = _field(pos, "coin", "asset")
            if not coin:
                continue
            out.append({
                "coin": coin,
                "dex": dex,
                "notional": round(abs(_f(pos, "positionValue", "position_value", default=0.0)), 2),
            })
    return out


def _parse_realized_pnl_rows(resp):
    """Normalize a discovery_get_open_position_realized_pnl response into a {(wallet_lower, COIN): {realized,
    fees}} map. Defensive over the real MCP envelope shapes: after _ok() the payload is a dict whose PnL list
    lives under `realized_pnl` (the tool's response key) or `realizedPnl` (the GraphQL key), or the response
    may be a bare list. Each row is {walletAddress, coin, realizedPnl, totalFees} with STRING numerics →
    parsed to float via _num (unknown/odd → skipped, never fabricated)."""
    rows = resp
    if isinstance(resp, dict):
        rows = _field(resp, "realized_pnl", "realizedPnl", "realized_pnls", default=[])
    if not isinstance(rows, list):
        return {}
    out = {}
    for r in rows:
        if not isinstance(r, dict):
            continue
        wallet = _field(r, "walletAddress", "wallet_address", "wallet")
        coin = _field(r, "coin", "asset")
        if not wallet or not coin:
            continue
        out[(str(wallet).lower(), str(coin).upper())] = {
            "realized": _num(_field(r, "realizedPnl", "realized_pnl")),
            "fees": _num(_field(r, "totalFees", "total_fees")),
        }
    return out


def fetch_partial_closes(client, strategies, meta):
    """Realized profit ALREADY TAKEN on STILL-OPEN positions — the stream the timing review is blind to
    (trader_history only lists FULLY-closed trades). For every CURRENT strategy (ACTIVE/PAUSED — the live
    book; a closed strategy has no open positions), read its OPEN (wallet, coin) pairs from the clearinghouse,
    then make ONE batched discovery_get_open_position_realized_pnl call over ALL those pairs. Returns a list
    (one entry per position with NON-ZERO realized taken):
        {asset, strategy_label, wallet, realized_taken, fees, remaining_notional, remaining_pct}
    remaining_notional = the still-open positionValue. remaining_pct (current value vs current + implied-
    closed) is only derivable when the realized taken is a positive profit AND we can approximate the closed
    notional — otherwise it's left null (never fabricated). Fail-open throughout: any read error → [] + a
    meta.warnings note; NEVER raises.

    Onchain-only + honest: telemetry never touches this (it's a discovery realized-PnL fact). The whole point
    is that the agent SEES the profit-taking on open positions without being asked."""
    out = []
    current = [s for s in (strategies or []) if _is_current(s.get("status"))]
    if not current:
        return out
    # ── gather the OPEN (wallet, coin) pairs across the live book (clearinghouse per wallet, fail-open) ──
    pairs = []                                 # [{wallet, coin, dex, notional, label}]
    for strat in current:
        wallet = strat.get("wallet")
        if not wallet:
            continue
        for op in _open_positions_for(client, strat, meta):
            pairs.append({"wallet": wallet, "coin": op["coin"], "dex": op.get("dex"),
                          "notional": op.get("notional"), "label": strat.get("label")})
    if not pairs:
        return out                             # no open positions → nothing could have been partially closed
    # ── ONE batched realized-PnL call over every open pair (discovery owns the realized-taken $) ──
    positions_arg = [{"walletAddress": p["wallet"], "coin": p["coin"]} for p in pairs]
    try:
        resp = _ok(client.mcp_call("discovery_get_open_position_realized_pnl",
                                   positions=positions_arg, timeout=20))
    except Exception as e:  # noqa — fail-open: the whole partial-close stream degrades to empty, never raises
        meta.setdefault("warnings", []).append(
            f"discovery_get_open_position_realized_pnl failed: {e}; partial-close detection skipped")
        return out
    pnl_map = _parse_realized_pnl_rows(resp)
    if not pnl_map:
        return out                             # no realized-taken rows → no partial closes to surface
    for p in pairs:
        rec = pnl_map.get((str(p["wallet"]).lower(), str(p["coin"]).upper()))
        if not rec:
            continue
        realized = rec.get("realized")
        if realized is None or realized == 0:
            continue                           # only surface positions with NON-ZERO realized taken
        remaining_notional = p.get("notional")
        # remaining_pct — current value vs (current + implied-closed). Only derivable for a positive realized
        # profit we can treat as a rough proxy for the closed notional; otherwise leave null (don't fabricate).
        remaining_pct = None
        if (remaining_notional is not None and realized is not None and realized > 0
                and (remaining_notional + realized) > 0):
            remaining_pct = round(remaining_notional / (remaining_notional + realized) * 100, 1)
        out.append({
            "asset": p["coin"],
            "strategy_label": p.get("label"),
            "wallet": p["wallet"],
            "realized_taken": round(realized, 2),
            "fees": round(rec["fees"], 2) if rec.get("fees") is not None else None,
            "remaining_notional": remaining_notional,
            "remaining_pct": remaining_pct,
        })
    # biggest realized-taken first — the profit-taking the narrator should lead with
    out.sort(key=lambda x: abs(x.get("realized_taken") or 0.0), reverse=True)
    return out


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
    # flip ONLY for an explicit short; long / unknown → no flip (don't mistreat a null direction as short)
    signed = -raw_move if trade.get("direction") == "short" else raw_move
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
    closed = fetch_closed_trades(client, strat["wallet"], since_ms, until_ms, cap, meta)
    # telemetry ring for this runtime (enrichment only; guarded + fail-open to []). Fetched even when
    # the wallet has no closed trades in-window, since its missed_signals + leaks still count. SKIPPED
    # entirely on the fast timing path (enrich_exit=False) so telemetry latency never blocks it.
    events = _fetch_events(strat.get("runtime_id"), since_ms, meta) if enrich_exit else []
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
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
            results = list(ex.map(_worker, strategies))   # ex.map preserves input order
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

    trades.sort(key=lambda t: _num(t.get("close_time")) or 0, reverse=True)
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
        try:
            events = _fetch_events(strat.get("runtime_id"), since_ms, priv)
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


def _strategy_reads(trades, strategies):
    """Per CURRENT strategy (ACTIVE/PAUSED ONLY): {label, wallet, status, mandate, dsl, closed_trade_count,
    realized_pnl, on_mandate_note}. This is the LIVE-BOOK verdict surface — closed strategies are excluded
    here (they're history; see closed_strategy_rollup). Realized PnL is EVIDENCE for the mandate verdict,
    not the headline — the narrator judges each strategy against its OWN mandate (guardrail 5: don't grade
    a deliberate book against a momentum benchmark). A CURRENT strategy with no mandate on file → note it
    plainly ("look it up / check the registry"), NEVER call it a bug."""
    by_wallet = _pnl_by_wallet(trades)
    out = []
    for s in strategies:
        if not _is_current(s.get("status")):
            continue                          # closed/historical → not a live-book verdict; see rollup
        w = str(s.get("wallet") or "").lower()
        agg = by_wallet.get(w, {"count": 0, "pnl": 0.0})
        mandate = s.get("mandate")
        if mandate is None:
            note = ("mandate unavailable on this CURRENT strategy — look it up / check the runtime "
                    "registry; NOT a bug. Judge the trades, not a benchmark")
        elif agg["count"] == 0:
            note = "no closed trades in this window — often by design (waiting for its signal), not a defect"
        else:
            note = "judge these closed trades against THIS mandate, not last window's winners"
        out.append({
            "label": s.get("label"),
            "wallet": s.get("wallet"),
            "status": s.get("status"),
            "mandate": mandate,
            "dsl": s.get("dsl"),                 # the levers a fix routes to
            "closed_trade_count": agg["count"],
            "realized_pnl": agg["pnl"],
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
        if _is_current(s.get("status")):
            continue                          # current → belongs in the live-book read, not history
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
    return {"warnings": [], "sources": ["discovery_get_trader_history", "discovery_get_open_position_realized_pnl",
                                        "strategy_get_clearinghouse_state", "ratchet_stop_list",
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
    strategies = fetch_strategies(client, meta)
    trades, _ms2, _lk, _fl = _collect_trades(
        client, strategies, meta, since_ms, until_ms, last_n, want_market, enrich_exit=False)
    state["strategies"] = strategies
    state["trades"] = trades
    state["window"] = window
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
    strategies = fetch_strategies(client, meta)
    trades, _ms2, _lk, _fl = _collect_trades(
        client, strategies, meta, since_ms, until_ms, last_n, want_market, enrich_exit=False)
    timing = _timing_summary(trades)
    meta["trade_count"] = len(trades)
    meta.pop("_telemetry_warned", None)
    meta.pop("_telemetry_dead", None)
    if not strategies:
        meta["degraded"] = "no strategies — check the token is USER-scoped"
    elif not trades:
        meta["degraded"] = "no closed trades in the window (or trade history unavailable)"
    # persist the raw fetch for downstream steps (strategies carries mandate/dsl/runtime_id; trades carries
    # the discovery facts + placeholder exit_reason the telemetry step will overwrite).
    state["window"] = window
    state["strategies"] = strategies
    state["trades"] = trades
    state["timing_summary"] = timing
    state["meta_warnings"] = meta.get("warnings", [])
    state["registry_source"] = meta.get("registry_source")
    _save_state(state_path, state)
    return {"window": window, "trades": trades, "timing_summary": timing, "meta": meta}


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
    strat_reads = _strategy_reads(trades, strategies)
    closed_reads = _closed_strategy_rollup(trades, strategies)
    dsl_mix = _dsl_close_reason_mix(trades)   # from whatever exit_reason is in state (UNKNOWN until telemetry)
    # PARTIAL CLOSES — realized profit ALREADY TAKEN on still-OPEN positions (the stream trader_history is
    # blind to). Read here (the CURRENT-book surface): clearinghouse open positions × discovery realized-PnL.
    partial_closes = fetch_partial_closes(client, strategies, meta)
    current_count = sum(1 for s in strategies if _is_current(s.get("status")))
    closed_count = len(strategies) - current_count
    meta["strategy_count"] = len(strategies)
    meta["current_strategy_count"] = current_count
    meta["closed_strategy_count"] = closed_count
    meta["trade_count"] = len(trades)
    meta["partial_close_count"] = len(partial_closes)
    meta["has_partial_closes"] = bool(partial_closes)   # the SKILL gates guardrail 9 on this
    if not strategies:
        meta["degraded"] = "no strategies — check the token is USER-scoped"
    state["strategies_read"] = strat_reads
    state["closed_strategies"] = closed_reads
    state["dsl_close_reason_mix"] = dsl_mix
    state["partial_closes"] = partial_closes
    _save_state(state_path, state)
    return {"strategies": strat_reads, "closed_strategies": closed_reads,
            "dsl_close_reason_mix": dsl_mix, "partial_closes": partial_closes, "meta": meta}


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
    blocked = _blocked_summary(missed_signals)
    exec_quality = _execution_quality(fills)
    dsl_mix = _dsl_close_reason_mix(trades)    # REFRESH — exit reasons are now filled in
    src_counts = _exit_reason_source_counts(trades)
    meta["exit_reason_source_counts"] = src_counts
    meta["telemetry_source"] = _telemetry_source(src_counts, meta.get("_telemetry_warned", False))
    meta["missed_signal_count"] = len(missed_signals)
    meta["leak_counts"] = {k: v["count"] for k, v in leaks.items()}
    meta["trade_count"] = len(trades)
    meta.pop("_telemetry_warned", None)
    meta.pop("_telemetry_dead", None)
    # persist the enriched trades (exit_reason now filled) + the streams for `all`-parity re-reads.
    state["trades"] = trades
    state["missed_signals"] = missed_signals
    state["blocked_summary"] = blocked
    state["leaks"] = leaks
    state["execution_quality"] = exec_quality
    state["dsl_close_reason_mix"] = dsl_mix
    state["meta_warnings"] = meta.get("warnings", [])
    _save_state(state_path, state)
    return {"trades": trades, "missed_signals": missed_signals, "blocked_summary": blocked,
            "leaks": leaks, "execution_quality": exec_quality, "dsl_close_reason_mix": dsl_mix, "meta": meta}


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
    meta = {"warnings": [], "sources": ["discovery_get_trader_history", "discovery_get_open_position_realized_pnl",
                                        "strategy_get_clearinghouse_state", "ratchet_stop_list",
                                        "market_get_asset_data", "leaderboard_get_markets",
                                        "openclaw senpi events"], "degraded": None}
    since_ms, until_ms = _window_bounds(window_days, now_ms=now_ms)
    label = f"last {last_n} closed trades" if last_n else f"last ~{window_days}d"
    window = {"from": since_ms, "to": until_ms, "label": label, "window_days": window_days,
              "last_n": last_n}
    meta["window"] = window

    # ALL statuses are enumerated (so a churned book's CLOSED trades stay in trades[]) — but the per-strategy
    # verdict is CURRENT-only. Closed/historical strategies get a minimal rollup, never a verdict.
    strategies = fetch_strategies(client, meta)
    # DISCOVERY owns trades[] (onchain facts); TELEMETRY enriches exit_reason + yields the standalone streams
    # (missed_signals + the leak/fill rollups), all from ONE per-runtime event fetch (no re-fetch downstream).
    trades, missed_signals, leaks, fills = _collect_trades(
        client, strategies, meta, since_ms, until_ms, last_n, want_market)
    timing = _timing_summary(trades)
    # telemetry-derived quick-action aggregations — ALL reuse the already-fetched events + existing trades[].
    dsl_mix = _dsl_close_reason_mix(trades)                     # 'shaken out too early / how exits fire'
    blocked = _blocked_summary(missed_signals)                  # 'what did my own limits block'
    exec_quality = _execution_quality(fills)                    # 'fees — maker vs taker'
    bvm = book_vs_market(client, trades, strategies, meta, want_market)
    strat_reads = _strategy_reads(trades, strategies)          # CURRENT book only (active/paused)
    closed_reads = _closed_strategy_rollup(trades, strategies) # HISTORY: closed/inactive, rollup only
    # PARTIAL CLOSES — realized profit ALREADY TAKEN on still-OPEN positions (TP/SL fills, size reductions).
    # trader_history only lists FULLY-closed trades, so this is the ONLY view of profit banked on live
    # positions. Clearinghouse open positions × ONE batched discovery realized-PnL call. Fail-open to [].
    partial_closes = fetch_partial_closes(client, strategies, meta)

    current_count = sum(1 for s in strategies if _is_current(s.get("status")))
    closed_count = len(strategies) - current_count
    meta["strategy_count"] = len(strategies)                   # every enumerated strategy (all statuses)
    meta["current_strategy_count"] = current_count            # the LIVE book — the "how many wallets" number
    meta["closed_strategy_count"] = closed_count              # churned/closed redeployments — HISTORY
    meta["trade_count"] = len(trades)
    # telemetry rollup — how enrichment did (never affects the discovery trade list, only enrichment).
    src_counts = _exit_reason_source_counts(trades)
    meta["exit_reason_source_counts"] = src_counts             # telemetry / ratchet / unknown
    meta["telemetry_source"] = _telemetry_source(src_counts, meta.get("_telemetry_warned", False))
    meta["missed_signal_count"] = len(missed_signals)
    meta["leak_counts"] = {k: v["count"] for k, v in leaks.items()}   # quick meta glance at the leak tallies
    meta["partial_close_count"] = len(partial_closes)
    meta["has_partial_closes"] = bool(partial_closes)         # the SKILL gates guardrail 9 on this
    meta.pop("_telemetry_warned", None)                        # internal flag — not part of the contract
    if not strategies:
        meta["degraded"] = "no strategies — check the token is USER-scoped"
    elif not trades and not partial_closes:
        # "no closed trades" is NOT "no activity": only degrade when there are ALSO zero partial closes
        # (profit taken on open positions). If partial_closes exist, there IS something to review.
        meta["degraded"] = "no closed trades in the window (or trade history unavailable)"

    return {
        "window": window,
        "trades": trades,                 # DISCOVERY-owned onchain facts + telemetry-enriched exit_reason
        "timing_summary": timing,         # PROCESS-framed counts — LEAD with these
        "dsl_close_reason_mix": dsl_mix,  # 'shaken out too early / how exits fire' → DSL preset lever
        "book_vs_market": bvm,            # the honest 'what did I miss' gap (movers the book didn't hold)
        "missed_signals": missed_signals, # TELEMETRY-native 'what did I miss' — blocked/rejected signals
        "blocked_summary": blocked,       # 'what did my own limits block' → slot / margin / risk-gate lever
        "leaks": leaks,                   # 'where am I leaking' — failed orders, protection gaps, risk halts
        "execution_quality": exec_quality, # 'fees — maker vs taker' (+ future authoritative-fee ledger hook)
        "strategies": strat_reads,        # CURRENT book only — each judged vs its OWN mandate
        "closed_strategies": closed_reads, # HISTORY — minimal rollup, NO verdict/mandate (never consolidate)
        "partial_closes": partial_closes,  # realized profit ALREADY TAKEN on still-OPEN positions (TP/SL/partial)
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
        "partial_closes": result.get("partial_closes"),
        "meta_warnings": (result.get("meta") or {}).get("warnings", []),
    }
    _save_state(state_path, state)
    return result


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
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
