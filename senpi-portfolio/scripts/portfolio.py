#!/usr/bin/env python3
"""senpi-portfolio engine — real-time wallet/balance taxonomy + holdings analysis (hidden).

The agent (LLM) runs this via the OpenClaw `exec` tool, reads the JSON on stdout, and NARRATES a
portfolio analysis (see SKILL.md). The script does the precise, real-time data work — enumerate every
wallet, classify every dollar into the right bucket, attribute positions, and pull market context for
analysis — and the LLM does the prose, the comparison, and the CTAs.

  python3 portfolio.py              # full real-time pull (all wallets + market context)
  python3 portfolio.py --no-market  # skip the per-asset market enrichment
  python3 portfolio.py --fixture f.json   # offline: recorded MCP-response map (tests)
  python3 portfolio.py --dry        # dump raw MCP responses for schema debugging

WHY THIS EXISTS — the balance-bucket trap:
Agents conflate `total_withdrawable` (free margin sitting INSIDE strategy wallets) with "idle cash in
the main embedded wallet." They are different buckets. This engine computes three structurally
separate pools so the agent never mixes them:
  1. idle_in_embedded   = total_usdc_in_hyperliquid + EVM token_balances   (truly free; deploy or withdraw)
  2. idle_in_strategies = sum of each strategy wallet's `withdrawable`      (in a strategy, not a position)
  3. deployed           = margin backing open positions
Grand total = idle_in_embedded + idle_in_strategies + deployed.

REAL-TIME, NEVER CACHED: account_get_portfolio caches HL data 12h unless forceFetch=true — this
engine always passes forceFetch. Per-strategy truth comes from live strategy_get_clearinghouse_state.

⚠ All tools here are USER-scoped (your own account): needs a USER-scoped SENPI_AUTH_TOKEN.
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import argparse
import json
import os
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
MARKET_ENRICH_CAP = 24      # cap the per-asset market pull
CLOSED_HISTORY_CAP = 5      # recent closed trades to surface per strategy (realized PnL is over the full pull)
CLOSED_HISTORY_PULL = 50    # closed positions to pull for the realized-PnL total (API default page)
# Fees come from the Hyperliquid fills ledger, NOT from discovery: HL `realizedPnl`/`closedPnl` is
# price-PnL EXCLUDING fees, so the discovery total is GROSS. The per-fill `fee` (which ALREADY includes
# the builder fee — never add a builder fee on top, that double-counts) is the separate cost; net =
# gross − fees. `userFills` is public + no-auth and keyed by wallet ADDRESS, the same transport the
# sibling senpi-improve-trades review engine uses.
HL_INFO_URL = "https://api.hyperliquid.xyz/info"

# WHAT A STRATEGY DOES = its `profile`, and the load-bearing field is `profile.description`, taken from
# the descriptor the RUNTIME renders for each runtime it has. This is UNIVERSAL: it works for a user's
# OWN authored strategy, not just our catalog templates — every deployed strategy has a runtime.yaml,
# only templates are in the catalog. The catalog stays as OPTIONAL enrichment
# (archetype/belief_plain/asset_classes/…) for templates, keyed by skill_name.
#   registry (universal, the runtime's own rendered descriptor)  →  the "what it does / how it works"
#   catalog  (templates only, our packages)                      →  extra facets when present
# Neither is agent memory; the runtime outranks the catalog.

# ASK THE RUNTIME, don't guess at its files. Every registry-derived field on this surface comes from
# this ONE command — the process that owns the registry, answering through its own CLI.
RUNTIME_LIST_CMD = ["openclaw", "senpi", "runtime", "list", "--json"]
RUNTIMES_FIXTURE_ENV = "SENPI_RUNTIMES_FIXTURE"     # offline test hook (see load_runtime_registry)
# The producer's exact blind-runtime status is `running — NO ENTRY SCANNERS` (em dash, U+2014). Matched
# on the phrase, not the whole string, so a dash/spacing drift cannot silently repaint blind as healthy.
RUNTIME_BLIND_MARK = "NO ENTRY SCANNERS"
# Telemetry liveness (health check): `openclaw senpi status -r <runtime_id> --json` says whether a
# REGISTERED runtime is actually WORKING (healthy vs degraded), not just present in the registry. Same
# fail-open + fixture pattern as senpi-improve-trades' event-log read. Offline test hook: a JSON file at
# $SENPI_STATUS_FIXTURE keyed {"<runtime_id>": {status payload}} is read instead of shelling out.
STATUS_FIXTURE_ENV = "SENPI_STATUS_FIXTURE"

CATALOG_REF = os.environ.get("SENPI_SKILLS_REF", "main")
CATALOG_URL = f"https://raw.githubusercontent.com/Senpi-ai/senpi-skills/{CATALOG_REF}/strategies/catalog.json"
# Compact catalog enrichment = the extra facets the agent judges a template strategy against (SKILL.md).
# Not the whole record. `description` is NOT sourced here — it comes from the runtime registry.
CATALOG_KEYS = ("belief_plain", "thesis", "archetype", "archetype_label", "sub_style", "direction",
                "asset_classes", "risk_level", "time_horizon", "tagline")


# ──────────────────────────────────────────────────────────────── guarded I/O helpers
# Vendored from senpi-strategy-ops/scripts/_cli.py — skills install as bare sibling dirs, so there
# is no cross-skill import. Held identical by tests/test_name_reader_parity.py; edit both or neither.
SPAWN_FAILED_PREFIX = "command not found: "


def _run_cli(args, timeout=60):
    """Run a CLI command; return (returncode, stdout, stderr). rc=-1 on spawn failure/timeout —
    `SPAWN_FAILED_PREFIX` on stderr distinguishes the never-ran case from the stopped-waiting one.
    NEVER raises for a failure to run: EVERY spawn-side OSError is caught, not just the missing
    binary, because a caller that dies here takes the whole script's output with it.

    Suppresses the senpi plugin's info logs (which it prints to STDOUT and which otherwise corrupt
    `--json` output) by forcing SENPI_LOG_LEVEL=error in the child env."""
    env = dict(os.environ, SENPI_LOG_LEVEL="error")
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout, env=env)
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        return -1, "", f"timed out after {timeout}s: {' '.join(args)}"
    except OSError as e:
        # The command NEVER RAN. FileNotFoundError (no such binary) is the common case; the rest are
        # fork/exec failures a strained box really does produce — ENOMEM ("Cannot allocate memory"),
        # EAGAIN (process-table exhaustion), EACCES, ENOEXEC. All of them are the never-ran event, so
        # all of them carry SPAWN_FAILED_PREFIX: the distinction the money path makes is never-ran vs
        # stopped-waiting, and only a timeout is the latter. Catching only FileNotFoundError let an
        # ENOMEM fork failure propagate out of the read and kill the caller mid-run.
        return -1, "", f"{SPAWN_FAILED_PREFIX}{args[0]}" + ("" if isinstance(e, FileNotFoundError)
                                                            else f" ({e})")


def _extract_json(text):
    """Recover a JSON object/array from output that may be polluted with leading/trailing log lines
    (e.g. `[plugins] [senpi-runtime] …` printed to stdout). Tries a clean parse, then raw_decode at
    every `{`/`[` offset and returns the LARGEST successful parse (the real payload, not a log line)."""
    text = text.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    dec = json.JSONDecoder()
    best = None
    best_len = -1
    for i, ch in enumerate(text):
        if ch not in "{[":
            continue
        try:
            obj, end = dec.raw_decode(text, i)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, (dict, list)) and (end - i) > best_len:
            best, best_len = obj, end - i
    return best


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


def _ms(ts):
    """Normalize a Unix timestamp to MILLISECONDS. trader-history close times come in seconds OR ms and HL
    fill `time` is ms; anything below ~1e12 (≈ 2001 in ms) is seconds → scale ×1000. Without this a
    seconds-valued closeTime and a ms-valued fill time land in different eras and every fee falls outside
    the window (a fee-window that reads $0 on a book that paid fees)."""
    n = _num(ts)
    if n is None:
        return None
    return n * 1000.0 if n < 1e12 else n


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


def _strategy_name_and_source(s):
    """What to CALL a strategy, and WHICH FIELD said so — `(name, name_source)`.

    ONE reader: the full read and the fast money map both build a row per wallet and must never name the
    same wallet two different things.

    `strategyName` first: it is the strategy's own name, and `deploy.py` creates it as `<id>-<instance>`,
    so it is the only field that tells one sleeve of a multi-wallet strategy from another.
    `tradingStrategyName` is NOT a second name — `strategy_list` sets it to `strategyMetadata.skillName`
    verbatim, so it is the PACKAGE id, identical across every instance of a package (three cub sleeves all
    render as "cub" through it) and already surfaced as `skill_name`.

    It stays in the chain as the fallback because `strategyName` is nullable BY MECHANISM: `strategy_create`
    takes no name at all and it is optional on `strategy_create_custom_strategy` (null on 21 of 23 rows in a
    live sample — the fallback is the COMMON path, not the edge case). For an unnamed strategy the package
    id is the most informative thing on the record; `"strategy"` is the last resort.

    Which is exactly why `name_source` is returned beside the name and rendered on every row: the name
    alone cannot tell "this strategy is called cub" from "this strategy is unnamed and cub is its package",
    and a surface that presents the second as the first is claiming a name it cannot prove. The source is
    the FIELD that answered, not a category, so it stays checkable against the payload. It also disambiguates
    the collision the fallback creates: a user-named `cub` and an unnamed strategy from package `cub` both
    render `name: "cub"`, and only `name_source` separates them.

    No `shortTraderAddress` leg. `strategy_list` DOES carry the field — the MCP maps every row through
    `withShortTraderAddress` — but it is the masked OG *trader* address (a copy-trade lineage), empty for
    the custom strategies this skill reads, and the row already carries `wallet`/`wallet_short`."""
    for key in ("strategyName", "tradingStrategyName", "name"):
        got = _first_written(s, key)
        if got:
            return got, key
    return "strategy", None


def _pct(mark, prev):
    m, p = _num(mark), _num(prev)
    if m is None or p is None or p == 0:
        return None
    return round((m - p) / p * 100, 2)


# ────────────────────────────── the runtime's own inventory (ASK THE RUNTIME — never read its files)
def _collapse_ws(s):
    """Collapse internal whitespace/newlines in a folded `description` block to single spaces + strip."""
    if not isinstance(s, str):
        return None
    out = " ".join(s.split()).strip()
    return out or None


def _runtime_read_failed(meta, cause):
    """A failed runtime read, made LOUD and returned as three Nones — never as empty maps.

    Empty maps read as "asked, and there is nothing", which is what turned every registry-derived field
    into a reassuring value on a host where the read never worked. The warning names the command, the
    cause, and the fields the failure disarms, so the narrator cannot mistake silence for an all-clear."""
    meta.setdefault("warnings", []).append(
        f"RUNTIME STATE UNVERIFIED — `{' '.join(RUNTIME_LIST_CMD)}` failed ({cause}). On EVERY strategy "
        f"this run, runtime_registered / not_running / running_blind / protected are null and "
        f"runtime_health is 'unverified': we could NOT check whether a runtime is behind a strategy, nor "
        f"whether a DSL exit is protecting it. Say so — never report a strategy as running, protected, "
        f"or not-running from this run.")
    meta["runtime_read_ok"] = False
    return None, None, None


def load_runtime_registry(meta):
    """wallet_lower → the engine's rendered descriptor, for every runtime the RUNTIME says it has.

    SOURCE: `openclaw senpi runtime list --json` — the process that owns the registry, asked through
    its own CLI. This used to resolve `~/.openclaw/senpi-state` by hand, which is `/root/…` on a box
    where OpenClaw lives under `/data/…`: the read failed on every real host and said nothing.
    FAIL-LOUD: a failed read returns (None, None, None) — three Nones, NOT empty maps — so callers
    render "could not verify" rather than a reassuring false. Offline test hook:
    $SENPI_RUNTIMES_FIXTURE is read instead of shelling out.
    Returns (descriptors_by_wallet, id_map, status_by_wallet) or (None, None, None)."""
    fixture = os.environ.get(RUNTIMES_FIXTURE_ENV)
    if fixture:
        try:
            with open(fixture) as fh:
                payload = json.load(fh)
        except Exception as e:  # noqa — a bad fixture is a failed read like any other, never an empty one
            return _runtime_read_failed(meta, f"fixture {fixture} unreadable ({e})")
    else:
        rc, out, err = _run_cli(RUNTIME_LIST_CMD, timeout=30)
        payload = _extract_json(out)          # stdout carries `[plugins]` banners on a real box
        if payload is None:                   # spawn failure, timeout, or nothing parseable on stdout
            return _runtime_read_failed(meta, (err or "").strip()[:200] or f"exit {rc}, no JSON on stdout")
    if not isinstance(payload, dict):
        return _runtime_read_failed(meta, "output was not a JSON object")
    if payload.get("ok") is False:
        return _runtime_read_failed(meta, f"reported ok:false ({str(payload.get('error'))[:120]})")
    entries = payload.get("runtimes")
    if not isinstance(entries, list):         # no `runtimes` key ⇒ not the payload we asked for
        return _runtime_read_failed(meta, "payload carried no `runtimes` list")

    # An EMPTY list is a successful read of zero runtimes — an answer, not a failure.
    descriptors, id_map, statuses = {}, {}, {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        wallet = entry.get("wallet")
        if not wallet:                        # unattributable to a strategy row; nothing to key it by
            continue
        wl = str(wallet).lower()
        # PRESENCE is the key's existence; the VALUE may be None. `descriptor: null` is what the engine
        # emits when an entry's YAML was absent/unparseable — that runtime is still registered and
        # running, it is just undescribed (and, having no read exit, not assertable as protected).
        desc = entry.get("descriptor")
        descriptors[wl] = desc if isinstance(desc, dict) else None
        rid = _field(entry, "id", "runtimeId", "runtime_id")   # the `senpi status -r <id>` address
        if rid:
            id_map[wl] = rid
        # The key is written for EVERY listed runtime, `status` present or not — a missing/null status
        # is "the inventory did not say", which must stay tellable from "not in the list at all".
        st = entry.get("status")
        statuses[wl] = str(st) if st is not None else None
    meta["runtime_read_ok"] = True
    return descriptors, id_map, statuses


def _profile_from_descriptor(desc):
    """The engine's rendered descriptor → the universal profile fields this surface narrates. None when
    the runtime had no descriptor to give (YAML absent/unparseable). Never raises."""
    if not isinstance(desc, dict):
        return None
    return {
        "runtime_name": desc.get("name"),
        "group": desc.get("group"),
        "version": desc.get("version"),
        "description": _collapse_ws(desc.get("description")),   # the UNIVERSAL "what it does / how it works"
        "dsl_preset": desc.get("dslPreset"),
        # The DSL protection LADDER as the ENGINE renders it: the phase1 hard-stop floor (active FROM
        # ENTRY) + the phase2 profit-lock tiers, or {preset_name, note} for a named preset. Reported per
        # strategy; the per-position tier state comes live from ratchet_stop_list (see hydrate).
        "dsl": desc.get("dsl"),
    }


# ──────────────────────────────────────────── telemetry liveness (is a REGISTERED runtime actually working?)
# Registry presence says a runtime was DEPLOYED; telemetry says it's actually RUNNING/healthy. Same
# fail-open + fixture pattern as senpi-improve-trades' event-log read: absence degrades to "unknown" (never
# a false "broken"), and once a host shows no CLI we stop shelling out.
def _note_telemetry_unavailable(meta, msg):
    """One-time telemetry warning; marks meta so the rollup can say liveness is unverified (registry-only)."""
    if not meta.get("_telemetry_warned"):
        meta.setdefault("warnings", []).append(f"telemetry: {msg}")
        meta["_telemetry_warned"] = True


def _fetch_runtime_status(runtime_id, meta):
    """`openclaw senpi status -r <id> --json` → parsed dict or None. FAIL-OPEN: no `openclaw` / a
    fork-or-exec failure / timeout / non-zero exit / unknown method / parse error → None + a one-time
    note. Raises on none of those: the spawn itself is guarded inside `_run_cli`, which catches every
    OSError (not just the missing binary) precisely so this claim holds. `meta._telemetry_dead`
    short-circuits once the host has no CLI. Offline test hook: $SENPI_STATUS_FIXTURE = JSON
    {"<runtime_id>": {the document that call prints}} is read instead of shelling out (tests use this —
    no subprocess), so a fixture carries the real `{ok, statuses:[…]}` shape, never an invented one."""
    if not runtime_id or meta.get("_telemetry_dead"):
        return None
    fixture = os.environ.get(STATUS_FIXTURE_ENV)
    if fixture:
        try:
            with open(fixture) as fh:
                data = json.load(fh)
            v = data.get(str(runtime_id)) if isinstance(data, dict) else None
            return v if isinstance(v, dict) else None
        except Exception as e:  # noqa — a bad fixture is fail-open too
            _note_telemetry_unavailable(meta, f"status fixture unreadable ({e})")
            return None
    rc, out, err = _run_cli(["openclaw", "senpi", "status", "-r", str(runtime_id), "--json"], timeout=20)
    if rc != 0:
        err = (err or "")[:200]
        if err.startswith(SPAWN_FAILED_PREFIX):   # not a runtime host → every call fails; stop shelling out
            meta["_telemetry_dead"] = True
            _note_telemetry_unavailable(meta, "openclaw CLI not found — runtime liveness unverified")
        elif "unknown method" in err.lower():
            meta["_telemetry_dead"] = True
            _note_telemetry_unavailable(meta, "runtime build predates the status RPC — liveness unverified")
        else:
            _note_telemetry_unavailable(meta, f"status read exit {rc} ({err.strip()})")
        return None
    # NOT a strict json.loads: on a real box stdout carries `[plugins] …` banners around the payload, so
    # strict parsing failed even when the payload was right there. (`_run_cli` also forces
    # SENPI_LOG_LEVEL=error, which suppresses most of them at the source.)
    data = _extract_json(out)
    if data is None:
        _note_telemetry_unavailable(meta, "status read returned no parseable JSON")
    return data


# The runtime's own health vocabulary is healthy/degraded/unhealthy/disabled/unknown (the engine's
# `ComponentHealth`). Only the HEALTHY family earns 'live'; the broken family earns 'degraded'; everything
# else (`unknown`, `disabled`, and any verdict we don't recognise) is UNPROVEN, not confirmed working.
_HEALTH_LIVE = ("healthy", "ok")
_HEALTH_BROKEN = ("degraded", "warn", "warning", "unhealthy", "failed", "error", "down", "false", "stopped")
# The keys that carry a HEALTH VERDICT the runtime computed about ITSELF (`RuntimeHealthStatus.health`;
# `overallHealth` is the older spelling this skill has always accepted). ONLY these may promote to 'live'.
_HEALTH_KEYS = ("overallHealth", "health")
# Keys that carry a RUN or JOB STATE, not health: `runtime list`'s `status`, a deploy snapshot's
# `state.overall`. They prove a process exists — never that it works — so they may only DOWNGRADE, never
# promote. `senpi-strategy-ops/scripts/_cli.py` splits the two for exactly this reason, and its comment
# records the incident: reading a run state as health turned a `{name, status: "running"}` entry into a ✅
# for a runtime no tick had ever proven.
_RUN_STATE_KEYS = ("overall", "status")


def _classify_health(raw, allow_live):
    """One verdict string → live / degraded / unknown. `allow_live=False` for a run state: it can say
    the process is stopped (→ degraded), it can never say the runtime is working."""
    h = str(raw).strip().lower()
    if h in _HEALTH_BROKEN:
        return "degraded"
    if allow_live and h in _HEALTH_LIVE:
        return "live"
    return "unknown"    # runtime-reported unknown/disabled, a run state, or an unrecognised verdict


def _entry_verdict(entry):
    """One `RuntimeHealthStatus` record → live / degraded / unknown. Health keys first (they may
    promote), then run-state keys (they may only downgrade), then 'unknown' — a record we could read but
    could not interpret is UNPROVEN, never 'live'."""
    if not isinstance(entry, dict):
        return "unknown"
    for k in _HEALTH_KEYS:
        if entry.get(k) is not None:
            return _classify_health(entry[k], allow_live=True)
    for k in _RUN_STATE_KEYS:
        if entry.get(k) is not None:
            return _classify_health(entry[k], allow_live=False)
    return "unknown"


def _status_entries(payload):
    """The `RuntimeHealthStatus` records inside a `senpi status --json` DOCUMENT.

    The document the CLI actually writes is `{ok: true, statuses: [RuntimeHealthStatus…]}` — the verdict
    lives one level DOWN, inside the array. Corroborated twice: the producer (senpi-trading-runtime
    `src/cli/senpi-commands.ts`, the status subcommand's `writeJson({ ok: true, statuses })`) and this
    repo's field-proven reader (`senpi-strategy-ops/scripts/_cli.py`'s `runtime_health_map`, which reads
    `find_list(obj, "statuses")`). A mapper that only looked at the wrapper found no verdict on EVERY
    real payload — which is how a fail-open default read `live` for a runtime the engine called unhealthy.

    An EMPTY `statuses` is a real ANSWER — the gateway is running no runtime under that id — and returns
    `[]`, which the caller maps to 'unknown' (never 'live').

    Also accepts the single-record shape (`{status: {…}}`, what the gateway hands the `-r <id>` form
    before the CLI folds it into `statuses`) and a bare record (a flatter/rewrapped payload)."""
    scopes = [payload] + [payload.get(k) for k in ("payload", "data", "result", "runtime")]
    scopes = [s for s in scopes if isinstance(s, dict)]
    for s in scopes:
        if isinstance(s.get("statuses"), list):
            return s["statuses"]                      # THE shape the CLI writes
        if isinstance(s.get("status"), dict):
            return [s["status"]]                      # the single-record shape
    for s in scopes:
        if any(s.get(k) is not None for k in _HEALTH_KEYS + _RUN_STATE_KEYS):
            return [s]                                # the scope IS the record
    return []


def _liveness_from_status(status):
    """Map a `senpi status -r <id> --json` DOCUMENT → runtime_health: 'live' / 'degraded' / 'unknown'.

    'live' has to be EARNED by a health verdict the runtime computed about itself (healthy/ok). A run
    state ("running") is not that verdict and cannot promote; a document we could read but found no
    recognisable verdict in is 'unknown'; an empty `statuses[]` is 'unknown'. None ⇒ 'unknown' too.
    Broken verdicts (degraded/unhealthy/…) and a stopped run state → 'degraded'. Worst wins across
    records (degraded > unknown > live) — an id that answers with several runtimes cannot have the sick
    one averaged away.

    'unknown' is NOT PROVEN LIVE — telemetry unavailable, or the runtime itself says it can't vouch for
    the runtime yet (never-heard scanners, right after a restart, a scanner-only runtime whose overall
    verdict renders `unknown`). It is never asserted as broken and never upgraded to 'live': the runtime
    is fail-closed about `unknown` (it refuses to paint an unproven scanner healthy), so painting that
    `unknown` as 'live' here would re-open exactly the fail-open it closes. Registered-vs-not-running is
    the registry read's verdict, not this one's — telemetry that reports nothing may neither upgrade a
    strategy to running nor condemn it.

    Reads the verdict ONLY from a record's own top level — NOT via a deep search. A deep search would
    pick up a per-scanner / per-order `status:"error"` on an otherwise-healthy runtime and cry DEGRADED
    (a false alarm); the OVERALL verdict is `RuntimeHealthStatus.health`."""
    if not isinstance(status, dict) or not status:
        return "unknown"
    if status.get("ok") is False:                     # the document itself says it could not answer
        return "unknown"
    verdicts = [_entry_verdict(e) for e in _status_entries(status)]
    for worst in ("degraded", "unknown", "live"):
        if worst in verdicts:
            return worst
    return "unknown"                                  # no records at all (empty `statuses[]`)


# ──────────────────────────────────────────────────────────────── strategy profile (catalog enrichment)
def _catalog_facets(rec):
    """The OPTIONAL template-only enrichment facets, pulled from a strategy's catalog record (its
    strategy.yaml). None if the strategy isn't in the catalog (e.g. a user-authored/custom strategy)."""
    if not isinstance(rec, dict):
        return None
    m = {k: rec[k] for k in CATALOG_KEYS if rec.get(k) is not None}
    return m or None


def _merge_profile(registry_prof, catalog_facets):
    """Merge the universal registry profile (load-bearing `description`) with optional catalog facets
    into a single `profile` dict. Sparse-safe: registry-only, catalog-only, or neither.
      - registry present            → `description` + runtime_name/group/dsl_preset (source "registry")
      - catalog present             → belief_plain/thesis/archetype/… (source adds "+catalog"/"catalog")
      - neither                     → None
    """
    if not registry_prof and not catalog_facets:
        return None
    prof = {
        "description": None, "runtime_name": None, "group": None, "version": None,
        "dsl_preset": None, "dsl": None,
        "belief_plain": None, "thesis": None, "archetype": None, "sub_style": None,
        "asset_classes": None, "risk_level": None, "time_horizon": None, "tagline": None,
        "source": None,
    }
    if registry_prof:
        prof["description"] = registry_prof.get("description")
        prof["runtime_name"] = registry_prof.get("runtime_name")
        prof["group"] = registry_prof.get("group")
        prof["version"] = registry_prof.get("version")
        prof["dsl_preset"] = registry_prof.get("dsl_preset")
        prof["dsl"] = registry_prof.get("dsl")   # the DSL protection ladder (how DSL works for this strat)
    if catalog_facets:
        for k in ("belief_plain", "thesis", "archetype", "sub_style", "asset_classes",
                  "risk_level", "time_horizon", "tagline"):
            if catalog_facets.get(k) is not None:
                prof[k] = catalog_facets[k]
    if registry_prof and catalog_facets:
        prof["source"] = "registry+catalog"
    elif registry_prof:
        prof["source"] = "registry"
    else:
        prof["source"] = "catalog"
    return prof


def _catalog_local_paths():
    """Candidate local catalog.json locations, freshest-first. A local copy (repo checkout or a
    co-installed senpi-strategy-discover) is fresh + offline; first that parses wins."""
    cands = []
    env = os.environ.get("SENPI_CATALOG_PATH")
    if env:
        cands.append(env)
    root = os.path.dirname(HERE)          # senpi-portfolio/       (HERE = .../scripts)
    repo = os.path.dirname(root)          # senpi-skills/  (dev/repo checkout)
    cands += [
        os.path.join(repo, "strategies", "catalog.json"),
        os.path.join(repo, "senpi-strategy-discover", "catalog.json"),
        os.path.expanduser("~/.openclaw/senpi-skills/senpi-strategy-discover/catalog.json"),
        os.path.expanduser("~/.claude/skills/senpi-strategy-discover/catalog.json"),
    ]
    return cands


def load_catalog(meta):
    """id → catalog record for every template strategy (its strategy.yaml facets, compiled by
    gen_catalog). OPTIONAL enrichment only — the universal mandate `description` comes from the runtime
    registry, not here; the catalog just adds template facets (archetype/belief_plain/…), keyed by
    skill_name. Local copy first (fresh, offline), then the remote catalog, then degrade to {} + a
    warning. Never raises. Returns (map, src)."""
    raw, src = None, None
    for p in _catalog_local_paths():
        try:
            if p and os.path.isfile(p):
                with open(p) as fh:
                    raw = json.load(fh)
                src = "local"
                break
        except Exception:  # noqa — a bad local copy shouldn't block the remote fallback
            continue
    if raw is None:
        try:
            import urllib.request
            with urllib.request.urlopen(CATALOG_URL, timeout=6) as r:
                raw = json.loads(r.read().decode("utf-8"))
            src = "remote"
        except Exception as e:  # noqa
            meta.setdefault("warnings", []).append(
                f"strategy catalog unavailable ({e}); template facets omitted — registry description still applies")
            return {}, None
    recs = raw.get("skills", raw) if isinstance(raw, dict) else raw
    out = {}
    for rec in (recs if isinstance(recs, list) else []):
        sid = rec.get("id") if isinstance(rec, dict) else None
        if sid:
            out[sid] = rec
    return out, src


# ──────────────────────────────────────────────────────────────── client
def _get_client():
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    from mcp_client import MCPClient
    return MCPClient()


class _FixtureClient:
    """Offline stand-in. Keys a call by (tool, strategy_wallet) or (tool, asset/dex) so a fixture can
    return per-wallet clearinghouse state. Falls back to the bare tool name."""
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


# ──────────────────────────────────────────────────────────────── wallet discovery
def fetch_embedded(client, meta):
    """Main/embedded wallet idle cash — the ONLY truly-free pool. Real-time (forceFetch)."""
    out = {"address": None, "idle_hl_usdc": None, "evm_usdc": [], "spot_usd": None,
           "idle_total": None}
    try:
        me = _ok(client.mcp_call("user_get_me", timeout=12)) or {}
        wallets = _field(me, "wallets", default=[]) or (me.get("user", {}) or {}).get("wallets", [])
        for w in wallets if isinstance(wallets, list) else []:
            if str(_field(w, "walletType", "type", default="")).lower() == "embedded":
                out["address"] = _field(w, "walletAddress", "address")
                break
    except Exception as e:  # noqa
        meta.setdefault("warnings", []).append(f"user_get_me failed: {e}")

    try:
        # forceFetch=True → bypass the 12h HL cache. This is the cache-freshness guarantee.
        p = _ok(client.mcp_call("account_get_portfolio", forceFetch=True, strategyStatus="ALL", timeout=25)) or {}
    except Exception as e:  # noqa
        meta.setdefault("warnings", []).append(f"account_get_portfolio failed: {e}")
        return out, {}

    # account_get_portfolio (GetPortfolioV3) nests the balance fields under a `portfolio` key
    # ({data: {portfolio: {...}}}); _ok() strips only the outer `data`. Unwrap `portfolio` here so the
    # field reads below hit real values (else the whole embedded read is $0). Robust to both shapes.
    # This nesting + the wrong field name below is why a $10k+ embedded infusion read as $0.
    if isinstance(p, dict) and isinstance(p.get("portfolio"), dict):
        p = p["portfolio"]

    # Idle HL balance is `total_in_hyperliquid` (per the account_get_portfolio schema + ops deploy.py) —
    # NOT `total_usdc_in_hyperliquid` (does not exist; the wrong name made this $0). Old name kept as a
    # harmless fallback so it can never regress.
    out["idle_hl_usdc"] = _f(p, "total_in_hyperliquid", "total_usdc_in_hyperliquid", default=0.0)
    out["spot_usd"] = _f(p, "total_spot_usd_in_hyperliquid", default=0.0)
    evm = 0.0
    for tb in (_field(p, "token_balances", default=[]) or []):
        sym = str(_field(tb, "symbol", "tokenSymbol", default="")).upper()
        if sym in ("USDC", "USDC.E", "USDT"):
            # Live GetPortfolioV3 uses `balanceInUSD` (+ `formattedBalance`/`tokenPriceInUSD`) —
            # without it every token read $0 and evm_usdc was always [].
            amt = _f(tb, "usdValue", "usd_value", "amountUsd", "balanceUsd", "balanceInUSD", "amount", default=0.0)
            if amt == 0.0:
                raw = _f(tb, "formattedBalance", "amount", default=0.0)
                price = _f(tb, "tokenPriceInUSD", default=1.0)
                amt = raw * price
            chain = _field(tb, "chain", "network", "chainName", default="EVM")
            if amt:
                out["evm_usdc"].append({"chain": chain, "usd": round(amt, 2)})
                evm += amt
    out["idle_total"] = round((out["idle_hl_usdc"] or 0.0) + evm, 2)
    portfolio_totals = {
        "total_balance_usd": _f(p, "total_balance_usd", default=None),
        "total_allocated_in_strategy": _f(p, "total_allocated_in_strategy", default=None),
        "total_withdrawable": _f(p, "total_withdrawable", default=None),
    }
    return out, portfolio_totals


def fetch_strategies(client, meta):
    """Live per-strategy state: enumerate strategies, then clearinghouse state per wallet (real-time,
    both DEXes). withdrawable = free margin idle IN that strategy; positions = deployed."""
    try:
        sl = _ok(client.mcp_call("strategy_list", status=["ACTIVE"], timeout=20))
    except Exception as e:  # noqa
        meta.setdefault("warnings", []).append(f"strategy_list failed: {e}")
        return []
    rows = sl if isinstance(sl, list) else _field(sl, "strategies", "data", default=[])
    # UNIVERSAL source of "what it does / how it works": the descriptor the RUNTIME renders for each
    # runtime it has, keyed by wallet — works for user-authored strategies, not just catalog templates.
    # THREE NONES when the read failed: every field below then reports null, never a reassuring value.
    descriptors, runtime_id_map, runtime_statuses = load_runtime_registry(meta)
    read_ok = descriptors is not None
    meta["registry_source"] = "runtime-cli" if read_ok else None
    # OPTIONAL template enrichment (archetype/belief_plain/…), keyed by skill_name.
    catalog, catalog_src = load_catalog(meta)
    meta["catalog_source"] = catalog_src
    strategies = []
    for s in (rows or []):
        wallet = _field(s, "strategyWalletAddress", "strategy_wallet_address", "walletAddress")
        if not wallet:
            continue
        # Attribution: the package a strategy was deployed under. Lives in strategyMetadata.skillName/
        # skillVersion (set by strategy_create_custom_strategy's skillName arg), with flat fallbacks.
        skill_name, skill_version = None, None
        meta_obj = _field(s, "strategyMetadata", "metadata")
        if isinstance(meta_obj, dict):
            skill_name = _field(meta_obj, "skillName", "skill_name")
            skill_version = _field(meta_obj, "skillVersion", "skill_version")
        if not skill_name:
            skill_name = _field(s, "skillName", "skill_name", "skill")
        if not skill_version:
            skill_version = _field(s, "skillVersion", "skill_version")
        wl = str(wallet).lower()
        desc = descriptors.get(wl) if read_ok else None
        rt_status = runtime_statuses.get(wl) if read_ok else None
        # UNIVERSAL profile: the descriptor's `description` (keyed by wallet) is the load-bearing "what it
        # does / how it works" — present for user-authored strategies too. Catalog facets enrich templates
        # only. Merged into a single `profile`; None only if BOTH are absent.
        registry_prof = _profile_from_descriptor(desc)
        catalog_facets = _catalog_facets(catalog.get(skill_name) if skill_name else None)
        profile = _merge_profile(registry_prof, catalog_facets)
        # Registered per the runtime itself. None ⇒ we could not ask — never False, which reads as
        # "we checked and there is nothing", a claim no failed read has earned. PRESENCE-based, not
        # descriptor-based: a registered runtime whose YAML the engine couldn't render is still RUNNING.
        runtime_registered = (wl in descriptors) if read_ok else None
        # ACTIVE + funded + attributed, with no runtime behind it: the trap that let a user think a
        # funded-but-never-registered strategy was live and protected. Unanswerable ⇒ None.
        not_running = (bool(skill_name) and runtime_registered is False) if read_ok else None
        # Up, and structurally unable to produce entry signals. Invisible on this surface before now.
        # Tri-state for the same reason every field here is: a registered runtime whose inventory row
        # carried NO status was never checked, and False there would claim "we checked, and it can
        # enter positions" — the reassuring answer, which is what a dropped field must never buy.
        if not read_ok:
            running_blind = None
        elif not runtime_registered:
            running_blind = False        # no runtime at all — `not_running` carries that, not this
        elif rt_status is None:
            running_blind = None         # listed, but it did not say
        else:
            running_blind = RUNTIME_BLIND_MARK in rt_status.upper()
        # PROTECTED — from an exit the ENGINE read in the deployed runtime.yaml (`descriptor.hasExit`, the
        # engine's own funding-gate predicate). The presence of a skillName stamp is ATTRIBUTION, not
        # protection, and asserting it as DSL protection is what this field did on every real host.
        if not read_ok:
            protected = None
        elif not_running:
            protected = False
        else:
            protected = bool(desc and desc.get("hasExit"))
        name, name_source = _strategy_name_and_source(s)
        strategies.append({
            "name": name,
            # WHICH field named it: "strategyName" = its own name; "tradingStrategyName"/"name" = the
            # package id standing in; None = unnamed, `name` is the "strategy" placeholder.
            "name_source": name_source,
            "wallet": wallet,
            # strategyId — needed for the live per-position DSL/ratchet lookup (ratchet_stop_list keys
            # on strategyId + wallet). Kept off the presentation surface; used only by hydrate().
            "strategy_id": _field(s, "id", "strategyId", "strategy_id"),
            "status": _field(s, "status", default="ACTIVE"),
            "total_funded": _f(s, "totalFunded", "total_funded", default=None),
            "total_withdrawn": _f(s, "totalWithdrawn", "total_withdrawn", default=None),
            "skill_name": skill_name,
            "skill_version": skill_version,
            # PROTECTED — an exit the ENGINE read in the deployed runtime.yaml. True | False | None
            # (null = the runtime read failed; we could not check, so nothing here is claimed).
            "protected": protected,
            "runtime_registered": runtime_registered,   # True | False | None (null — could not ask)
            "not_running": not_running,                  # ACTIVE + funded skill strategy, no runtime → dead
            "running_blind": running_blind,              # up, but with NO entry scanners — cannot enter
            # The strategy's declared job — `profile.description` from the descriptor the RUNTIME
            # rendered for it, plus optional catalog facets. The yardstick to judge it against.
            "profile": profile,
        })

    # Where did the strategies' profiles come from, in aggregate: registry / catalog / mixed / None.
    prof_srcs = {s["profile"]["source"] for s in strategies if s.get("profile")}
    if not prof_srcs:
        meta["profile_source"] = None
    elif prof_srcs <= {"registry"}:
        meta["profile_source"] = "registry"
    elif prof_srcs <= {"catalog"}:
        meta["profile_source"] = "catalog"
    else:
        meta["profile_source"] = "mixed"

    def hydrate(strat):
        try:
            ch = _ok(client.mcp_call("strategy_get_clearinghouse_state", strategy_wallet=strat["wallet"], timeout=20))
        except Exception as e:  # noqa
            meta.setdefault("warnings", []).append(f"clearinghouse {strat['wallet'][:8]} failed: {e}")
            return strat
        dex_av, dex_wd, positions = {}, {}, []
        for dex in ("main", "xyz"):
            d = _field(ch, dex, default={}) if isinstance(ch, dict) else {}
            ms = _field(d, "marginSummary", "margin_summary", default={}) or {}
            dex_av[dex] = _f(ms, "accountValue", "account_value", default=0.0)
            dex_wd[dex] = _f(d, "withdrawable", default=0.0)
            for ap in (_field(d, "assetPositions", "asset_positions", default=[]) or []):
                pos = _field(ap, "position", default=ap) or {}
                szi = _f(pos, "szi", "size", default=0.0)
                if szi == 0:
                    continue
                lev = pos.get("leverage") or {}
                positions.append({
                    "asset": _field(pos, "coin", "asset"),
                    "dex": dex,
                    "direction": "long" if szi > 0 else "short",
                    "leverage": _f(lev, "value", default=None) if isinstance(lev, dict) else _num(lev),
                    "notional": round(abs(_f(pos, "positionValue", "position_value", default=0.0)), 2),
                    "margin": round(_f(pos, "marginUsed", "margin_used", default=0.0), 2),
                    "entry_px": _f(pos, "entryPx", "entry_px", default=None),
                    "upnl": round(_f(pos, "unrealizedPnl", "unrealized_pnl", default=0.0), 2),
                    "return_on_equity_pct": round(_f(pos, "returnOnEquity", "return_on_equity", default=0.0) * 100, 2),
                    "liq_px": _f(pos, "liquidationPx", "liquidation_px", default=None),
                })
        # CRITICAL — main and xyz are two VIEWS of ONE wallet, not separate pools. `withdrawable` is
        # the SHARED idle collateral, mirrored identically in both views — count it ONCE (max == either).
        # Each view's accountValue = shared idle + that DEX's own position equity (margin + uPnL), so:
        #   wallet_value = main.av + xyz.av − shared_idle   (subtract the duplicated base exactly once)
        # Summing av (or summing withdrawable) double-counts the shared collateral — the bug this fixes.
        shared_idle = max(dex_wd.get("main", 0.0), dex_wd.get("xyz", 0.0))
        deployed = sum(max(0.0, dex_av.get(dex, 0.0) - shared_idle) for dex in ("main", "xyz"))
        strat["idle_withdrawable"] = round(shared_idle, 2)         # shared free margin (counted once)
        strat["deployed"] = round(deployed, 2)                     # position equity across BOTH dexes
        strat["account_value"] = round(shared_idle + deployed, 2)  # = main.av + xyz.av − shared_idle
        strat["position_margin"] = round(sum(p["margin"] for p in positions), 2)   # initial margin detail
        strat["positions"] = positions
        # RECONCILE status vs live wallet — the clearinghouse is the TRUTH, `status` is not. `strategy_list`
        # can report a just-closed strategy as ACTIVE (the status lags the close). A $0 account value with
        # NO positions AND NO idle is an EMPTY wallet: the strategy was CLOSED/DRAINED (funds returned to
        # the embedded wallet) or never funded. Flag it so the narrator never presents `total_funded` as
        # live/idle/reserved capital and never counts a ghost as a live strategy. (A FLAT sleeve merely
        # waiting for a signal still holds idle margin → account_value > 0 → NOT flagged empty.)
        tf, tw = strat.get("total_funded"), strat.get("total_withdrawn")
        strat["empty"] = (strat["account_value"] <= 0.01 and strat["idle_withdrawable"] <= 0.01 and not positions)
        if strat["empty"]:
            drained = bool(tf and tf > 0 and tw is not None and tw >= tf - 0.01)
            strat["empty_reason"] = "closed_or_drained" if drained else "unfunded"
        # LIVE per-position DSL/ratchet tier — read-guarded + fail-open. Attaches a `dsl` object to each
        # open position (armed → tier/lock; not armed → "protected from entry, ratchet arms at +X%").
        # NEVER leaves a live position looking "unprotected." (See attach_position_dsl.)
        attach_position_dsl(client, strat, meta)
        strat["closed"] = fetch_closed(client, strat["wallet"], meta)
        return strat

    try:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=6) as ex:
            strategies = list(ex.map(hydrate, strategies))
    except Exception:  # noqa
        strategies = [hydrate(s) for s in strategies]
    # TELEMETRY LIVENESS — for each strategy WITH a registered runtime, ask the runtime itself (senpi
    # status) whether it's actually healthy, not just registered. runtime_health:
    #   unverified  — the runtime read FAILED; we could not ask ANY of it (never say running/protected)
    #   not_running — the read succeeded and there is no runtime behind an ACTIVE + funded strategy
    #   degraded    — up but broken: no entry scanners, stopped, or telemetry reports unhealthy
    #   live        — registered and telemetry reports healthy. Only this earns "running"
    #   unknown     — NOT PROVEN LIVE (telemetry unavailable, or the runtime won't vouch for it yet)
    # Fail-open + short-circuited by _telemetry_dead; sequential (few per user).
    for s in strategies:
        if s.get("runtime_registered") is None:
            s["runtime_health"] = "unverified"     # the read failed — nothing below was ever asked
        elif s.get("not_running"):
            s["runtime_health"] = "not_running"
        elif s.get("running_blind"):
            s["runtime_health"] = "degraded"       # up, and structurally unable to enter a position
        elif s.get("running_blind") is None:
            # Registered, but the inventory carried no status for it: we do not know whether it can
            # enter a position at all, so telemetry's "healthy" cannot make this row read 'live'.
            s["runtime_health"] = "unknown"
        elif s.get("runtime_registered") is not True:
            s["runtime_health"] = "unknown"        # unattributed strategy with no runtime (e.g. copy-trade)
        elif str(runtime_statuses.get(str(s.get("wallet")).lower(), "")).lower().startswith("stopped"):
            s["runtime_health"] = "degraded"       # the inventory itself says the process is stopped
        else:
            rid = runtime_id_map.get(str(s.get("wallet")).lower())
            s["runtime_health"] = _liveness_from_status(_fetch_runtime_status(rid, meta) if rid else None)
    # Roll up any strategy reported ACTIVE but holding $0 (empty wallet) — status/clearinghouse mismatch.
    dormant = [s["name"] for s in strategies if s.get("empty")]
    if dormant:
        meta["dormant_active"] = dormant
        meta.setdefault("warnings", []).append(
            f"{len(dormant)} strategy(ies) report status ACTIVE but hold $0 (empty wallet) — likely just "
            f"closed, funds returned to embedded (or never funded): {', '.join(str(d) for d in dormant)}")
    # Roll up any ACTIVE + funded strategy with NO runtime registered — status says ACTIVE but there is no
    # runtime, so it is NOT running: no scanner, no DSL, no guardrails. The "ACTIVE record ≠ live runtime"
    # trap — must be surfaced as unprotected/not-running, never as "alive and waiting".
    not_running = [s["name"] for s in strategies if s.get("not_running")]
    if not_running:
        meta["not_running"] = not_running
        meta.setdefault("warnings", []).append(
            f"{len(not_running)} strategy(ies) show status ACTIVE but have NO runtime registered — NOT "
            f"running: no scanner, no DSL, no guardrails despite 'ACTIVE'. Report as UNPROTECTED / not "
            f"running, never as live or 'waiting for a setup': {', '.join(str(n) for n in not_running)}. "
            f"Confirm + fix with senpi-strategy-ops `diagnose.py <id>` (then close.py → redeploy).")
    # Up, and CANNOT ENTER: the runtime reports `running — NO ENTRY SCANNERS`. Broken wiring, not a quiet
    # market — a strategy that will never take a position no matter what its scanners would have found.
    # Kept out of the degraded roll-up below so the cause the agent relays is the real one.
    blind = [s["name"] for s in strategies if s.get("running_blind")]
    if blind:
        meta["running_blind"] = blind
        meta.setdefault("warnings", []).append(
            f"{len(blind)} strategy(ies) are RUNNING WITH NO ENTRY SCANNERS — the runtime is up but has no "
            f"scanner wired to produce entry signals, so it can never open a position (this is broken "
            f"wiring, NOT 'waiting for a setup'): {', '.join(str(b) for b in blind)}. Confirm + fix with "
            f"senpi-strategy-ops `diagnose.py <id>` (then close.py → redeploy).")
    # Registered but telemetry says the runtime is DEGRADED/unhealthy — running, but not cleanly (scanner
    # erroring, monitor stalled, etc.). Distinct from not_running (no runtime) and from live (healthy).
    degraded = [s["name"] for s in strategies
                if s.get("runtime_health") == "degraded" and not s.get("running_blind")]
    if degraded:
        meta["degraded_runtimes"] = degraded
        meta.setdefault("warnings", []).append(
            f"{len(degraded)} strategy(ies) have a runtime that telemetry reports DEGRADED/unhealthy — "
            f"registered but not working cleanly. Confirm the cause with senpi-strategy-ops "
            f"`diagnose.py <id>` (scanner registered? ticked? no signals yet? erroring?): "
            f"{', '.join(str(d) for d in degraded)}")
    return strategies


def _hl_info(payload, meta, client=None, timeout=12):
    """POST the Hyperliquid Info API (public, no auth) — the same transport the sibling senpi-improve-trades
    review engine uses. HL keys fills by wallet ADDRESS (durable across a `strategy_close`). Offline/fixture
    aware for tests (`_FixtureClient` serves a recorded `hl::<type>::<wallet>` entry). Fails OPEN → None."""
    if client is not None and hasattr(client, "_r"):          # _FixtureClient — serve recorded HL response
        u = str(payload.get("user", "")).lower()
        return client._r.get(f"hl::{payload.get('type')}::{u}") or client._r.get(f"hl::{payload.get('type')}")
    try:
        p = subprocess.run(
            ["curl", "-s", "-m", str(timeout), "-X", "POST", HL_INFO_URL,
             "-H", "Content-Type: application/json", "-d", json.dumps(payload)],
            capture_output=True, text=True, timeout=timeout + 3)
        if p.returncode != 0 or not (p.stdout or "").strip():
            return None
        return json.loads(p.stdout)
    except Exception as e:  # noqa
        meta.setdefault("warnings", []).append(f"hl_info {payload.get('type')} failed: {e}")
        return None


def _window_fees(client, wallet, rows, meta):
    """Total builder-inclusive trading fee for the closed-trade window, summed from the HL `userFills`
    ledger — the SEPARATE cost that discovery's gross `realizedPnl` excludes (net = gross − fees). The
    window is the span the reported closed trades occupy: every fill with `time` at or before the LATEST
    reported close, so BOTH the opening and closing legs of each closed trade are captured (with the
    default CLOSED_HISTORY_PULL page this is the whole book — the exact total fee for the exact gross).

    Returns (fees, status):
      (sum, "ok")            fills read and summed — a per-fill `fee` ALREADY includes the builder fee, so
                             it is NEVER added again.
      (None, "undetermined") the `userFills` read FAILED or came back EMPTY while the wallet HAS closed
                             trades — fees are UNKNOWN and MUST NOT be reported as $0 (that would overstate
                             the user's booked profit, the exact bug this guards).
    FAIL-OPEN: any fee-source error degrades to `undetermined`, it never raises into the portfolio read."""
    closes = [t for t in (_ms(_field(p, "closeTime", "closed_time", "closeTimeMs"))
                          for p in rows if isinstance(p, dict)) if t is not None]
    until_ms = max(closes) if closes else None      # bound above by the latest reported close; no lower bound
    try:
        fills = _hl_info({"type": "userFills", "user": wallet}, meta, client)
    except Exception as e:  # noqa
        meta.setdefault("warnings", []).append(f"userFills {wallet[:8]} failed: {e}")
        return None, "undetermined"
    # A FAILED (None) or EMPTY fills read while the wallet has closed trades ⇒ fees UNKNOWN, never $0.
    if not isinstance(fills, list) or not fills:
        meta.setdefault("warnings", []).append(
            f"userFills {wallet[:8]} empty/unavailable — fees undetermined (NOT $0)")
        return None, "undetermined"
    total = 0.0
    for fl in fills:
        if not isinstance(fl, dict):
            continue
        t = _ms(fl.get("time"))
        if until_ms is not None and t is not None and t > until_ms:
            continue                                # a fill after the last reported close — not this window
        total += _num(fl.get("fee")) or 0.0
    return round(total, 2), "ok"


def fetch_closed(client, wallet, meta):
    """Read-guarded closed-position ledger for a strategy wallet: GROSS realized PnL, the trading fees, the
    NET (gross − fees), and a short list of recent closed trades. Extraction matches the real
    `discovery_get_trader_history` shape (senpi://guides/trader-closed-positions): a `closedPositions[]` of
    records with `coin`, signed `szi` (>0 closed long / <0 closed short), string `realizedPnl` (price-PnL
    EXCLUDING fees → GROSS), Unix-ms `closeTime`, `entryPx`/`exitPx`. Fees are sourced SEPARATELY from the
    HL `userFills` ledger (see `_window_fees`).

    Emits: `gross_realized_pnl`, `fees`, `net_realized_pnl`, `fees_status` ("ok" | "undetermined"),
    `trade_count`, `recent[]`. Degrades HONESTLY — a wallet WITH closed trades whose fills read
    fails/empty reports `fees`/`net_realized_pnl` = null + `fees_status` "undetermined" (NEVER a fake $0).
    Fails OPEN — any read/parse error → empty closed block + a meta.warning, never crashes."""
    # trader-history itself unread ⇒ GROSS is unknown, so net + fees are unknowable too → undetermined.
    empty = {"gross_realized_pnl": None, "fees": None, "net_realized_pnl": None,
             "fees_status": "undetermined", "trade_count": 0, "recent": []}
    try:
        h = _ok(client.mcp_call("discovery_get_trader_history", trader_address=wallet,
                                sort_by="CLOSED_TIME", sort_direction="DESC",
                                limit=CLOSED_HISTORY_PULL, timeout=20))
    except Exception as e:  # noqa
        meta.setdefault("warnings", []).append(f"trader_history {wallet[:8]} failed: {e}")
        return empty
    if h is None:
        # _ok returns None on an explicit success:false envelope
        meta.setdefault("warnings", []).append(f"trader_history {wallet[:8]} returned no data")
        return empty
    rows = h if isinstance(h, list) else _field(h, "closedPositions", "closed_positions", "positions", default=[])
    if not isinstance(rows, list):
        rows = []
    realized_total = 0.0
    recent = []
    for p in rows:
        if not isinstance(p, dict):
            continue
        pnl = _f(p, "realizedPnl", "realized_pnl", default=0.0)   # GROSS price-PnL; often a string → _f coerces
        realized_total += pnl
        if len(recent) < CLOSED_HISTORY_CAP:
            szi = _f(p, "szi", "size", default=0.0)
            recent.append({
                "asset": _field(p, "coin", "coinDisplayName", "asset"),
                "direction": "long" if szi >= 0 else "short",   # closed-side sign (szi>0 closed a long)
                "realized_pnl": round(pnl, 2),                   # per-trade GROSS (kept as-is)
                "entry_px": _field(p, "entryPx", "entry_px"),
                "exit_px": _field(p, "exitPx", "exit_px"),
                "closed_time": _field(p, "closeTime", "closed_time", "closeTimeMs"),
            })
    gross = round(realized_total, 2)
    if not rows:
        # A SUCCESSFUL read of an empty book: genuinely no closed trades ⇒ fees really ARE $0 (net = gross).
        # This is the ONLY branch that may report $0 fees — distinct from a failed/empty fills read above.
        return {"gross_realized_pnl": gross, "fees": 0.0, "net_realized_pnl": gross,
                "fees_status": "ok", "trade_count": 0, "recent": recent}
    fees, fees_status = _window_fees(client, wallet, rows, meta)
    net = round(gross - fees, 2) if fees is not None else None
    return {"gross_realized_pnl": gross, "fees": fees, "net_realized_pnl": net,
            "fees_status": fees_status, "trade_count": len(rows), "recent": recent}


# ──────────────────────────────────────────────────────────────── live per-position DSL / ratchet tier
def _locked_pct_at_tier(ladder, tier_index):
    """The lock_hw_pct configured at `tier_index` in the parsed profile.dsl ladder (what % of the peak
    is locked once the ratchet reaches that tier). None if the ladder/index isn't available."""
    if not isinstance(ladder, dict):
        return None
    tiers = ladder.get("tiers")
    if not isinstance(tiers, list) or tier_index is None:
        return None
    try:
        i = int(tier_index)
    except (TypeError, ValueError):
        return None
    if 0 <= i < len(tiers) and isinstance(tiers[i], dict):
        return tiers[i].get("lock_hw_pct")
    return None


def _unarmed_dsl(ladder, roe):
    """The DSL object for an open position that has NOT yet crossed Tier 1 (no ratchet record). This is
    the WHOLE POINT of the fix: an empty ratchet record is NOT "no DSL / unprotected" — the phase1 hard
    stop protects the position FROM ENTRY, and the profit-ratchet simply hasn't ARMED yet. Frame it that
    way, NEVER as unmonitored. `ladder` = the strategy's profile.dsl (config); `roe` = this position's
    return_on_equity_pct. Stands alone even if the live ratchet call failed (config + ROE only)."""
    ladder = ladder if isinstance(ladder, dict) else {}
    hard = ladder.get("hard_stop_roe_pct")
    arm = ladder.get("arm_at_roe_pct")
    obj = {
        "armed": False,
        "hard_stop_roe_pct": hard,        # floor active FROM ENTRY (phase1) — always protecting
        "arm_at_roe_pct": arm,            # where the profit-ratchet ARMS (Tier 1)
        "roe": roe,                       # this position's current ROE
    }
    # A plain-language note the narrator can lean on — never reads as "unprotected."
    if arm is not None:
        roe_txt = f"+{roe}%" if (roe is not None and roe >= 0) else (f"{roe}%" if roe is not None else "n/a")
        floor_txt = f"; hard stop at {hard}% ROE" if hard is not None else ""
        obj["note"] = (f"protected from entry by the phase1 hard stop{floor_txt}; profit-ratchet arms at "
                       f"Tier 1 (+{arm}%) — currently {roe_txt}")
    elif hard is not None:
        obj["note"] = (f"protected from entry by the phase1 hard stop (floor {hard}% ROE); "
                       f"phase1-only preset — no profit-ratchet tiers")
    else:
        obj["note"] = ("protected by the strategy's DSL exit from entry; live ratchet tier not yet armed")
    return obj


def attach_position_dsl(client, strat, meta):
    """Attach a `dsl` object to each open position of ONE strategy instance — its LIVE ratchet tier state.

    Read-guarded + FAIL-OPEN. One `ratchet_stop_list(strategyId, wallet, status:ACTIVE)` call per
    instance, indexed by asset:
      - a record exists (position crossed Tier 1) → armed: true, tier_index, high_water_roe, status,
        locked (= lock_hw_pct at that tier from the parsed ladder).
      - NO record (sub-Tier-1) → the `_unarmed_dsl` object: armed: false + the "protected from entry,
        ratchet arms at +X%" framing. This is EXPECTED, not a gap — never "unprotected."
      - the ratchet call fails entirely → EVERY position still gets the config-based `_unarmed_dsl`
        object (config + ROE stands alone), plus a meta.warnings note.
    NEVER emits anything that reads as "no DSL / no monitoring."
    """
    positions = strat.get("positions") or []
    if not positions:
        return
    prof = strat.get("profile") or {}
    ladder = prof.get("dsl")
    sid = strat.get("strategy_id")
    wallet = strat.get("wallet")

    records = None
    try:
        rl = _ok(client.mcp_call("ratchet_stop_list", strategyId=sid,
                                 strategy_wallet_address=wallet, status="ACTIVE", timeout=15))
        rows = rl if isinstance(rl, list) else _field(rl, "configs", "ratchetStops", "data", "items", default=[])
        records = {}
        for r in (rows if isinstance(rows, list) else []):
            if not isinstance(r, dict):
                continue
            asset = _field(r, "asset", "coin")
            if asset:
                records[str(asset)] = r
    except Exception as e:  # noqa — fail-open: config-based framing stands alone
        meta.setdefault("warnings", []).append(
            f"ratchet_stop_list {str(wallet)[:8]} failed: {e}; DSL tier from config only")
        records = None

    for p in positions:
        roe = p.get("return_on_equity_pct")
        rec = records.get(str(p.get("asset"))) if isinstance(records, dict) else None
        if rec is not None:
            ti = _field(rec, "currentTierIndex", "current_tier_index")
            p["dsl"] = {
                "armed": True,
                "tier_index": ti,
                "high_water_roe": _field(rec, "highWaterRoe", "high_water_roe"),
                "status": _field(rec, "status", default="ACTIVE"),
                "locked": _locked_pct_at_tier(ladder, ti),   # lock_hw_pct at the active tier
            }
        else:
            # no ratchet record (sub-Tier-1) OR the list call failed — either way, config-based framing
            p["dsl"] = _unarmed_dsl(ladder, roe)


# ──────────────────────────────────────────────────────────────── market context (for analysis)
def enrich_market(client, strategies, meta):
    """Per-held-asset 24h move so the LLM can compare each position to the broader market."""
    assets = []
    for s in strategies:
        for p in s.get("positions", []):
            tag = (p["asset"], p["dex"])
            if p["asset"] and tag not in assets:
                assets.append(tag)
    assets = assets[:MARKET_ENRICH_CAP]

    def one(item):
        asset, dex = item
        kw = dict(asset=asset, candle_intervals=["1h"], include_order_book=False, timeout=12)
        if dex == "xyz" or str(asset).startswith("xyz:"):
            kw["dex"] = "xyz"
        try:
            data = _ok(client.mcp_call("market_get_asset_data", **kw))
            ctx = _field(data, "asset_context", "context", default={}) or {}
            # live schema nests the quote under `context`; handle both
            inner = ctx if ("markPx" in ctx) else (_field(data, "context", default={}) or {})
            mark = _field(ctx, "markPx", default=None) or _field(inner, "markPx", default=None)
            prev = _field(ctx, "prevDayPx", default=None) or _field(inner, "prevDayPx", default=None)
            return (asset, _pct(mark, prev))
        except Exception:  # noqa
            return (asset, None)

    facts = {}
    try:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=6) as ex:
            for a, chg in ex.map(one, assets):
                facts[a] = chg
    except Exception:  # noqa
        for item in assets:
            a, chg = one(item)
            facts[a] = chg
    # fold onto positions + tag alignment
    for s in strategies:
        for p in s.get("positions", []):
            chg = facts.get(p["asset"])
            if chg is None:
                continue
            p["market_24h_pct"] = chg
            # a short is "working" when the asset is down; a long when it's up
            working = (p["direction"] == "short" and chg < 0) or (p["direction"] == "long" and chg > 0)
            p["vs_market"] = "with the move" if working else "against the move"
    return facts


# ──────────────────────────────────────────────────────────────── taxonomy + signals
def compute(embedded, strategies, portfolio_totals):
    idle_strat = round(sum(_num(s.get("idle_withdrawable")) or 0.0 for s in strategies), 2)
    deployed = round(sum(_num(s.get("deployed")) or 0.0 for s in strategies), 2)
    idle_emb = embedded.get("idle_total") or 0.0
    strat_acct = round(sum(_num(s.get("account_value")) or 0.0 for s in strategies), 2)
    grand_total = round(idle_emb + strat_acct, 2)

    # exposure
    gross_long = gross_short = 0.0
    by_asset = {}
    upnl_total = 0.0
    largest = None
    for s in strategies:
        for p in s.get("positions", []):
            n = p["notional"]
            upnl_total += p["upnl"]
            if p["direction"] == "long":
                gross_long += n
            else:
                gross_short += n
            sign = n if p["direction"] == "long" else -n
            by_asset[p["asset"]] = round(by_asset.get(p["asset"], 0.0) + sign, 2)
            if largest is None or n > largest["notional"]:
                largest = {"asset": p["asset"], "notional": n, "strategy": s["name"]}

    totals = {
        "grand_total_usd": grand_total,
        "idle_in_embedded": round(idle_emb, 2),
        "idle_in_strategies": idle_strat,
        "deployed_in_positions": deployed,
        "strategy_account_value": strat_acct,
        "unrealized_pnl": round(upnl_total, 2),
        # cross-check against the (cached-bypassed) portfolio aggregate, if present
        "portfolio_total_balance_usd": portfolio_totals.get("total_balance_usd"),
        "portfolio_total_withdrawable": portfolio_totals.get("total_withdrawable"),
    }
    # reconciliation flag — surfaces silent drift between the two sources
    pbal = portfolio_totals.get("total_balance_usd")
    totals["reconciles"] = (pbal is None) or (abs(pbal - grand_total) <= max(2.0, 0.01 * grand_total))

    net = round(gross_long - gross_short, 2)
    exposure = {
        "net_notional_usd": net, "net_bias": ("long" if net > 0 else "short" if net < 0 else "flat"),
        "gross_long_usd": round(gross_long, 2), "gross_short_usd": round(gross_short, 2),
        "by_asset_net_usd": by_asset, "largest_position": largest,
    }
    working_cap = idle_emb + strat_acct
    signals = {
        "idle_drag_pct": round((idle_emb + idle_strat) / working_cap * 100, 1) if working_cap else None,
        "deployed_pct": round(deployed / working_cap * 100, 1) if working_cap else None,
        "largest_position_pct_of_deployed": round(largest["notional"] / (gross_long + gross_short) * 100, 1)
            if largest and (gross_long + gross_short) else None,
    }
    return totals, exposure, signals


# ──────────────────────────────────────────────────────────────── strategy grouping (A STRATEGY IS ALL ITS WALLETS)
def _group_key(strat):
    """The grouping key for a per-wallet `strategies[]` row → the STRATEGY it belongs to.

    A single strategy can deploy as MULTIPLE instances on SEPARATE wallets (ox = core+ballast,
    cougar = long+short, cub = long+short+preipo). `strategy_list` returns each instance/wallet as its
    OWN row, so the engine lists them as separate `strategies[]` entries. Re-uniting them is the whole
    point: `profile.group` (from the deployed runtime.yaml, shared by every instance of a strategy) is
    the authoritative key → fall back to `skill_name` (package attribution) → fall back to the wallet
    itself (a genuinely ungrouped / custom one-off is its own group of one). Fail-open: never raises."""
    prof = strat.get("profile") or {}
    grp = prof.get("group")
    if grp:
        return str(grp)
    if strat.get("skill_name"):
        return str(strat["skill_name"])
    return str(strat.get("wallet") or id(strat))


def _short_wallet(w):
    w = str(w or "")
    return f"{w[:6]}...{w[-4:]}" if len(w) > 12 else w


def _rollup_flag(insts, field):
    """ALL-instances roll-up of a tri-state flag: None if ANY instance is null (we could not check that
    sleeve, so the strategy-level answer is not known either), else all(...)."""
    vals = [s.get(field) for s in insts]
    if any(v is None for v in vals):
        return None
    return all(bool(v) for v in vals)


def _rollup_any(insts, field):
    """ANY-instance roll-up of a tri-state flag: True if any sleeve says yes (one is enough), else None
    if any sleeve is unknown, else False. A null sleeve may never roll up as a reassuring False."""
    vals = [s.get(field) for s in insts]
    if any(v is True for v in vals):
        return True
    if any(v is None for v in vals):
        return None
    return False


def group_strategies(strategies, meta):
    """Collapse the per-wallet `strategies[]` rows into `strategy_groups[]` — ONE entry per real strategy.

    SUPPLEMENTS `strategies[]` (does not replace it — the bucket math + per-wallet detail still rely on
    the flat list). Each group re-unites every instance/wallet of a strategy so the agent reasons at the
    STRATEGY level: a multi-wallet strategy (long+short, core+ballast, multi-sleeve) is ONE strategy
    across N wallets, never N separate strategies. Order is preserved by first appearance. Fail-open:
    a malformed row can't sink the grouping; worst case it lands in its own wallet-keyed group."""
    order = []          # group keys, first-seen order
    buckets = {}        # key → list of strategies
    for s in (strategies or []):
        try:
            key = _group_key(s)
        except Exception:  # noqa — a malformed row must not sink the grouping
            key = str(s.get("wallet") or id(s))
        if key not in buckets:
            buckets[key] = []
            order.append(key)
        buckets[key].append(s)

    groups = []
    for key in order:
        insts = buckets[key]
        # Instances share the strategy's identity (profile is per-strategy, mirrored on every wallet);
        # pull the shared facets from the first instance that carries a profile, else the first row.
        prof = next((s.get("profile") for s in insts if s.get("profile")), None) or {}
        first = insts[0]
        # mandate = the strategy's declared job — description (universal, from the deployed runtime.yaml)
        # or belief_plain (catalog facet); instances share it.
        mandate = prof.get("description") or prof.get("belief_plain")
        skill_name = next((s.get("skill_name") for s in insts if s.get("skill_name")), None)

        instances = []
        for s in insts:
            instances.append({
                "name": s.get("name"),                          # = runtime_name, e.g. ox-core
                "wallet": s.get("wallet"),
                "wallet_short": _short_wallet(s.get("wallet")),
                "account_value": s.get("account_value"),
                "idle_withdrawable": s.get("idle_withdrawable"),
                "deployed": s.get("deployed"),
                "upnl": round(sum(_num(p.get("upnl")) or 0.0 for p in (s.get("positions") or [])), 2),
                "positions": s.get("positions", []),
                "closed": s.get("closed"),
            })

        # totals — summed across every instance/wallet of the strategy (a strategy is all its wallets)
        def _sum(field):
            vals = [_num(i.get(field)) for i in instances]
            vals = [v for v in vals if v is not None]
            return round(sum(vals), 2) if vals else None
        # Closed-PnL roll-up across the strategy's wallets. GROSS (from discovery) sums independently of
        # fees. FEES/NET are known for the STRATEGY only if EVERY instance's fees were determined — one
        # `undetermined` sleeve makes the strategy total fees/net undetermined too (a partial fee sum must
        # never be reported as the complete cost). Never launder an unknown fee into $0.
        closed_recs = [(s.get("closed") or {}) for s in insts]
        gross_vals = [_num(c.get("gross_realized_pnl")) for c in closed_recs]
        gross_vals = [v for v in gross_vals if v is not None]
        gross_total = round(sum(gross_vals), 2) if gross_vals else None
        fee_statuses = [c.get("fees_status") for c in closed_recs if c.get("fees_status") is not None]
        fees_known = bool(fee_statuses) and all(st == "ok" for st in fee_statuses)
        if fees_known:
            fee_vals = [v for v in (_num(c.get("fees")) for c in closed_recs) if v is not None]
            fees_total = round(sum(fee_vals), 2) if fee_vals else None
            net_total = (round(gross_total - fees_total, 2)
                         if (gross_total is not None and fees_total is not None) else None)
            fees_status_total = "ok"
        else:
            fees_total = net_total = None
            fees_status_total = "undetermined" if fee_statuses else None
        totals = {
            "account_value": _sum("account_value"),
            "idle_withdrawable": _sum("idle_withdrawable"),
            "deployed": _sum("deployed"),
            "upnl": _sum("upnl"),
            "gross_realized_pnl": gross_total,
            "fees": fees_total,
            "net_realized_pnl": net_total,
            "fees_status": fees_status_total,
        }

        # flat instances = an instance with NO open positions. For a multi-wallet strategy this is its
        # OTHER sleeve waiting for its signal (e.g. cougar's long book flat while its short book trades),
        # NOT redeployable idle. Named so the agent never calls it "dead money."
        flat_instances = [i["name"] for i, s in zip(instances, insts) if not (s.get("positions") or [])]

        groups.append({
            "label": key,                                       # the group id, e.g. ox / cougar / cub
            "skill_name": skill_name,
            "archetype": prof.get("archetype"),
            "archetype_label": prof.get("archetype_label"),
            "direction": prof.get("direction"),
            "mandate": mandate,                                 # the strategy's declared job (shared)
            # HOW the strategy's DSL works — the phase1 hard-stop floor + phase2 tier ladder, shared by
            # every instance (one config per strategy). Surfaced once here; per-position tier state lives
            # on each position's `dsl` object. None for a named-preset/no-phase2 strategy handled inline.
            "dsl": prof.get("dsl"),
            "is_multi_wallet": len(insts) > 1,
            "instances": instances,                             # per-wallet detail
            "totals": totals,                                   # summed across all wallets
            # protected ONLY if ALL instances are protected — a strategy with one unguarded sleeve is not
            # fully protected. None if ANY instance is null: the roll-up may not launder an unverified
            # instance into a verdict (bool(None) would quietly render "we checked, and it is not").
            "protected": _rollup_flag(insts, "protected"),
            # not_running: ANY instance is ACTIVE + funded but has no runtime registered → the strategy (or
            # a sleeve of it) isn't actually running. running_blind: ANY sleeve is up but cannot enter.
            # runtime_registered: True (all registered) / False (some missing) / None (could not ask).
            "not_running": _rollup_any(insts, "not_running"),
            "running_blind": _rollup_any(insts, "running_blind"),
            "runtime_registered": _rollup_flag(insts, "runtime_registered"),
            # runtime_health = the WORST across instances (not_running > degraded > unverified > unknown >
            # live) — one dead/degraded/unverifiable sleeve makes the whole strategy not-fully-live.
            "runtime_health": next((v for v in ("not_running", "degraded", "unverified", "unknown", "live")
                                    if any(s.get("runtime_health") == v for s in insts)), "unknown"),
            "flat_instances": flat_instances,
            "profile_source": prof.get("source"),
        })

    if any(g["is_multi_wallet"] for g in groups):
        meta["has_multi_wallet_strategy"] = True
    return groups


# ──────────────────────────────────────────────────────────────── orchestration
def run(client, want_market=True):
    meta = {"warnings": [], "real_time": True, "force_fetch": True}
    embedded, portfolio_totals = fetch_embedded(client, meta)
    strategies = fetch_strategies(client, meta)
    if want_market and strategies:
        enrich_market(client, strategies, meta)
    totals, exposure, signals = compute(embedded, strategies, portfolio_totals)
    meta["strategy_count"] = len(strategies)
    meta.setdefault("has_multi_wallet_strategy", False)   # default; group_strategies flips it to True
    # A STRATEGY IS ALL ITS WALLETS — re-unite the per-wallet rows into one entry per real strategy.
    # SUPPLEMENTS `strategies[]` (kept — bucket math + detail rely on it); groups add the strategy-level view.
    strategy_groups = group_strategies(strategies, meta)
    if not strategies and not embedded.get("address"):
        meta["degraded"] = "no wallet data — check the token is USER-scoped"
    return {
        "as_of": "live",
        "totals": totals,           # the three buckets — NEVER conflate them
        "embedded_wallet": embedded,
        "strategies": strategies,
        # ONE entry per real strategy (a strategy is ALL its wallets); reason + recommend at THIS level.
        "strategy_groups": strategy_groups,
        "exposure": exposure,
        "signals": signals,
        "meta": meta,
    }


# ──────────────────────────────────────────────────────────── shared state file (resumable steps)
# The step subcommands (money → strategies → positions) are FAST, resumable slices that persist their work
# to a shared JSON state file so a later step never re-fetches what an earlier one already pulled. The
# agent runs them in sequence and NARRATES between — no single call carries the whole multi-wallet pull
# (which trips the exec timeout and pushes the agent to raw MCP, losing every guardrail). Each step is
# idempotent + fail-open: a missing/corrupt state file → recompute (self-heal); every step also works
# STANDALONE (just slower). `all` writes the same state but prints run()'s full composed dict
# (byte-identical to the pre-steps output). State default: <tempdir>/senpi-portfolio/state.json.
STATE_SUBDIR = "senpi-portfolio"


def _default_state_path():
    """Default shared-state path <tempdir>/senpi-portfolio/state.json. Uses tempfile.gettempdir()
    (never $HOME — the state dir may live somewhere else on a runtime host)."""
    return os.path.join(tempfile.gettempdir(), STATE_SUBDIR, "state.json")


STATE_TTL_S = 90   # a shared state file older than this is cross-run STALE (well beyond one narrated
                   # money→strategies→positions turn) → recomputed, so nothing lingers across runs.


def _load_state(path):
    """Read the shared state JSON. Never raises — a missing/corrupt/unreadable file → {} (fail-open: the
    step then recomputes its prerequisites and self-heals). Also drops a state file older than STATE_TTL_S
    (see above): it persists across separate runs, so past that window any snapshot is treated as cross-run
    STALE — the same fail-open self-heal as a corrupt file. This is what stops a strategy CLOSED since a
    prior run from lingering as a ghost on a standalone `strategies`/`positions` call."""
    if not path or not os.path.isfile(path):
        return {}
    try:
        if time.time() - os.path.getmtime(path) > STATE_TTL_S:
            return {}
        with open(path) as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa — corrupt/unreadable/undatable state is fail-open → recompute
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
    """A meta skeleton seeded like run()'s — the same real_time/force_fetch scaffolding, so every step's
    meta reads consistently whether it ran standalone or off state."""
    return {"warnings": [], "real_time": True, "force_fetch": True}


# ─────────────────────────────────────────── money-lite hydrate (fast bucket math, no positions detail)
def _hydrate_money(client, strat, meta):
    """The FAST per-strategy money pull for the `money` step: ONE strategy_get_clearinghouse_state call
    per wallet → account_value / idle_withdrawable / deployed ONLY. This is exactly the bucket math from
    fetch_strategies.hydrate (the shared-idle de-dup across the main+xyz views), WITHOUT the positions
    detail, the live DSL/ratchet pull, the closed-history read, or the market enrichment — those are the
    slow parts and belong to the `strategies`/`positions` steps. Fail-open: a read error leaves the
    strategy money-less + a meta.warnings note, never crashes."""
    try:
        ch = _ok(client.mcp_call("strategy_get_clearinghouse_state", strategy_wallet=strat["wallet"], timeout=20))
    except Exception as e:  # noqa
        meta.setdefault("warnings", []).append(f"clearinghouse {strat['wallet'][:8]} failed: {e}")
        return strat
    dex_av, dex_wd = {}, {}
    for dex in ("main", "xyz"):
        d = _field(ch, dex, default={}) if isinstance(ch, dict) else {}
        ms = _field(d, "marginSummary", "margin_summary", default={}) or {}
        dex_av[dex] = _f(ms, "accountValue", "account_value", default=0.0)
        dex_wd[dex] = _f(d, "withdrawable", default=0.0)
    # main + xyz are two VIEWS of ONE wallet — `withdrawable` is the SHARED idle, mirrored in both; count
    # it ONCE (see fetch_strategies.hydrate for the full derivation). deployed = each DEX's own position
    # equity (accountValue − shared idle), summed. wallet_value = main.av + xyz.av − shared_idle.
    shared_idle = max(dex_wd.get("main", 0.0), dex_wd.get("xyz", 0.0))
    deployed = sum(max(0.0, dex_av.get(dex, 0.0) - shared_idle) for dex in ("main", "xyz"))
    strat["idle_withdrawable"] = round(shared_idle, 2)
    strat["deployed"] = round(deployed, 2)
    strat["account_value"] = round(shared_idle + deployed, 2)
    return strat


def fetch_strategy_money(client, meta):
    """The FAST money-map strategy fetch: enumerate ACTIVE strategies (same strategy_list call + wallet
    extraction as fetch_strategies) and money-lite-hydrate each wallet in parallel. Returns lightweight
    strategy rows carrying name / wallet / strategy_id / status / total_funded / total_withdrawn plus the
    account_value / idle_withdrawable / deployed money fields — NO profile / dsl / protected / positions /
    closed (those are the `strategies` step). Deliberately DOES NOT read the runtime registry or catalog
    (both are for the mandate read, not the money map). Fail-open: []. Mirrors fetch_strategies' skeleton
    so the two agree on the wallet set + the bucket math."""
    try:
        sl = _ok(client.mcp_call("strategy_list", status=["ACTIVE"], timeout=20))
    except Exception as e:  # noqa
        meta.setdefault("warnings", []).append(f"strategy_list failed: {e}")
        return []
    rows = sl if isinstance(sl, list) else _field(sl, "strategies", "data", default=[])
    strategies = []
    for s in (rows or []):
        wallet = _field(s, "strategyWalletAddress", "strategy_wallet_address", "walletAddress")
        if not wallet:
            continue
        name, name_source = _strategy_name_and_source(s)
        strategies.append({
            "name": name,
            "name_source": name_source,     # see fetch_strategies — same reader, same contract
            "wallet": wallet,
            "strategy_id": _field(s, "id", "strategyId", "strategy_id"),
            "status": _field(s, "status", default="ACTIVE"),
            "total_funded": _f(s, "totalFunded", "total_funded", default=None),
            "total_withdrawn": _f(s, "totalWithdrawn", "total_withdrawn", default=None),
        })
    try:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=6) as ex:
            strategies = list(ex.map(lambda s: _hydrate_money(client, s, meta), strategies))
    except Exception:  # noqa — fail-open to sequential
        strategies = [_hydrate_money(client, s, meta) for s in strategies]
    return strategies


def _money_totals(embedded, strategies, portfolio_totals):
    """The three-bucket money map — the SAME classification compute() does, over money-lite strategy rows
    (which carry idle_withdrawable / deployed / account_value). idle_in_embedded / idle_in_strategies /
    deployed_in_positions + grand_total_usd + reconciles. No exposure/signals here (those need the full
    positions detail — the `positions` step)."""
    idle_strat = round(sum(_num(s.get("idle_withdrawable")) or 0.0 for s in strategies), 2)
    deployed = round(sum(_num(s.get("deployed")) or 0.0 for s in strategies), 2)
    idle_emb = embedded.get("idle_total") or 0.0
    strat_acct = round(sum(_num(s.get("account_value")) or 0.0 for s in strategies), 2)
    grand_total = round(idle_emb + strat_acct, 2)
    totals = {
        "grand_total_usd": grand_total,
        "idle_in_embedded": round(idle_emb, 2),
        "idle_in_strategies": idle_strat,
        "deployed_in_positions": deployed,
        "strategy_account_value": strat_acct,
        "portfolio_total_balance_usd": portfolio_totals.get("total_balance_usd"),
        "portfolio_total_withdrawable": portfolio_totals.get("total_withdrawable"),
    }
    pbal = portfolio_totals.get("total_balance_usd")
    totals["reconciles"] = (pbal is None) or (abs(pbal - grand_total) <= max(2.0, 0.01 * grand_total))
    return totals


# ─────────────────────────────────────────────── self-heal: full strategies[] in state (for step 2 / 3)
def _ensure_full_strategies_in_state(client, state, want_market, meta):
    """Return the FULLY-hydrated strategies[] (positions + DSL + closed + profile/mandate) — from the state
    file when a prior step already built it, else recompute the full fetch right here (so the `strategies`
    and `positions` steps each work STANDALONE). Also rehydrates the embedded wallet + portfolio totals
    from state (or re-fetches). Merges its work back into state for the next step. Returns (embedded,
    strategies, portfolio_totals).

    Freshness is enforced UPSTREAM, not here: `step_money` (step 1) starts each turn from a clean state,
    and `_load_state` discards a state file older than STATE_TTL_S. So any snapshot this reuses is from
    the CURRENT turn — the cross-run ghost (a strategy CLOSED since a prior run, or one DEPLOYED since) is
    killed at the source, with no per-step strategy_list round-trip."""
    embedded = state.get("embedded_wallet")
    portfolio_totals = state.get("portfolio_totals")
    strategies = state.get("strategies_full")
    if isinstance(embedded, dict) and isinstance(strategies, list) and isinstance(portfolio_totals, dict):
        return embedded, strategies, portfolio_totals
    # state absent/partial → recompute the full pull (embedded + fully-hydrated strategies). The market
    # enrichment is the `positions` step's job — skip it here (want_market only gates step 3's fold).
    embedded, portfolio_totals = fetch_embedded(client, meta)
    strategies = fetch_strategies(client, meta)
    state["embedded_wallet"] = embedded
    state["portfolio_totals"] = portfolio_totals
    state["strategies_full"] = strategies
    state.setdefault("meta_warnings", [])
    state["meta_warnings"] = meta.get("warnings", [])
    state["registry_source"] = meta.get("registry_source")
    state["catalog_source"] = meta.get("catalog_source")
    state["profile_source"] = meta.get("profile_source")
    if "runtime_read_ok" in meta:
        state["runtime_read_ok"] = meta["runtime_read_ok"]
    return embedded, strategies, portfolio_totals


def _carry_provenance(meta, state):
    """Restore the read-provenance keys onto a step's own `meta` from the shared state.

    On a state HIT the reads that set these ran in an EARLIER step, so without this a step drops
    `registry_source` — documented in SKILL.md as always present, and the field a reader checks before
    trusting any runtime claim. `runtime_read_ok` is copied only when the state HAS it: a bool defaulted
    to null would report a failed runtime read this step never made."""
    for k in ("registry_source", "catalog_source", "profile_source"):
        meta[k] = state.get(k, meta.get(k))
    if "runtime_read_ok" in state:
        meta["runtime_read_ok"] = state["runtime_read_ok"]


# ──────────────────────────────────────────── step subcommands (fast, resumable, standalone)
def step_money(client, want_market=True, state_path=None):
    """STEP 1 `money` — the FAST money map the agent NARRATES FIRST. Embedded idle + each strategy wallet's
    account_value/withdrawable → the three buckets (idle_in_embedded / idle_in_strategies /
    deployed_in_positions) + grand_total_usd + reconciles. Persists the strategy list + wallets so
    `strategies`/`positions` don't re-enumerate. FAST: no positions detail, no DSL/ratchet, no closed
    history, no market. `want_market` is accepted for a uniform step signature but unused here."""
    if state_path is None:
        state_path = _default_state_path()
    # step 1 is by definition the START of a turn — derived snapshots from a PRIOR run (e.g. a
    # `strategies_full` cached while a since-closed strategy was ACTIVE) must not survive it. Start clean;
    # money re-fetches everything it needs. (_load_state's TTL covers a standalone `strategies`/`positions`.)
    state = {}
    meta = _fresh_meta()
    embedded, portfolio_totals = fetch_embedded(client, meta)
    strategies = fetch_strategy_money(client, meta)
    totals = _money_totals(embedded, strategies, portfolio_totals)
    meta["strategy_count"] = len(strategies)
    if not strategies and not embedded.get("address"):
        meta["degraded"] = "no wallet data — check the token is USER-scoped"
    # persist the money-lite strategy rows (name/wallet/id/status/money) so the later steps reuse the
    # wallet set; the full hydrate (positions/DSL/closed/profile) is the `strategies` step's self-heal.
    state["embedded_wallet"] = embedded
    state["portfolio_totals"] = portfolio_totals
    state["strategies_money"] = strategies
    state["totals"] = totals
    state["meta_warnings"] = meta.get("warnings", [])
    _save_state(state_path, state)
    return {"totals": totals, "embedded_wallet": embedded, "strategies": strategies, "meta": meta}


def step_strategies(client, want_market=True, state_path=None):
    """STEP 2 `strategies` — the per-strategy detail (the verdict surface). Reads state (or self-heals the
    full fetch when state is absent): fully-hydrated `strategies[]` (mandate/DSL from the registry +
    `protected` + closed/realized) + `strategy_groups[]` (a strategy is ALL its wallets). Runs the runtime
    registry + catalog reads here (the mandate source). NO market enrichment (that's `positions`)."""
    if state_path is None:
        state_path = _default_state_path()
    state = _load_state(state_path)
    meta = _fresh_meta()
    embedded, strategies, portfolio_totals = _ensure_full_strategies_in_state(
        client, state, want_market, meta)
    # carry forward any warnings the self-heal fetch (or an earlier step) recorded
    for w in state.get("meta_warnings", []):
        if w not in meta["warnings"]:
            meta["warnings"].append(w)
    _carry_provenance(meta, state)
    meta["strategy_count"] = len(strategies)
    meta.setdefault("has_multi_wallet_strategy", False)
    strategy_groups = group_strategies(strategies, meta)
    if not strategies and not embedded.get("address"):
        meta["degraded"] = "no wallet data — check the token is USER-scoped"
    state["strategies_full"] = strategies
    state["strategy_groups"] = strategy_groups
    state["meta_warnings"] = meta.get("warnings", [])
    state["has_multi_wallet_strategy"] = meta.get("has_multi_wallet_strategy", False)
    _save_state(state_path, state)
    return {"strategies": strategies, "strategy_groups": strategy_groups, "meta": meta}


def step_positions(client, want_market=True, state_path=None):
    """STEP 3 `positions` — position-level analysis. Reads the full strategies[] from state (self-heals if
    absent), runs the per-asset market enrichment (`market_24h_pct`/`vs_market` — the fan-out isolated
    HERE), then computes `exposure` + `signals` off the full positions detail. Skipped-to-no-fold when
    --no-market (positions keep their bucket math; market fields stay absent)."""
    if state_path is None:
        state_path = _default_state_path()
    state = _load_state(state_path)
    meta = _fresh_meta()
    embedded, strategies, portfolio_totals = _ensure_full_strategies_in_state(
        client, state, want_market, meta)
    for w in state.get("meta_warnings", []):
        if w not in meta["warnings"]:
            meta["warnings"].append(w)
    if want_market and strategies:
        enrich_market(client, strategies, meta)
    totals, exposure, signals = compute(embedded, strategies, portfolio_totals)
    _carry_provenance(meta, state)   # same restore as `step_strategies` — see _carry_provenance
    meta["strategy_count"] = len(strategies)
    meta.setdefault("has_multi_wallet_strategy", False)
    # REBUILD strategy_groups AFTER the market fold so the persisted groups reference the market-enriched
    # positions — this is exactly run()'s order (enrich_market → group_strategies), keeping the shared
    # state after the full pipeline byte-consistent with `all`.
    strategy_groups = group_strategies(strategies, meta)
    if not strategies and not embedded.get("address"):
        meta["degraded"] = "no wallet data — check the token is USER-scoped"
    # persist the enriched strategies (market fields now folded onto positions) + exposure/signals + the
    # refreshed groups (over the enriched positions).
    state["strategies_full"] = strategies
    state["strategy_groups"] = strategy_groups
    state["exposure"] = exposure
    state["signals"] = signals
    state["totals"] = totals            # the full totals (incl. unrealized_pnl from positions)
    state["meta_warnings"] = meta.get("warnings", [])
    state["has_multi_wallet_strategy"] = meta.get("has_multi_wallet_strategy", False)
    _save_state(state_path, state)
    return {"strategies": strategies, "strategy_groups": strategy_groups, "exposure": exposure,
            "signals": signals, "totals": totals, "meta": meta}


# ──────────────────────────────────────────────────────────────── CLI
def _dry(client):
    out = {}
    for label, tool, kw in (("user_get_me", "user_get_me", {}),
                            ("account_get_portfolio", "account_get_portfolio", {"forceFetch": True}),
                            ("strategy_list", "strategy_list", {})):
        try:
            out[label] = client.mcp_call(tool, timeout=20, **kw)
        except Exception as e:  # noqa
            out[label] = {"error": str(e)}
    return out


_STEPS = ("money", "strategies", "positions", "all")
_STEP_FNS = {"money": step_money, "strategies": step_strategies, "positions": step_positions}


def _all_and_persist(client, want_market, state_path):
    """`all` = the composed one-shot. Runs the UNCHANGED `run()` (its output is byte-identical to the
    pre-steps engine) and ALSO writes the shared state file (the same shape the steps build) so an `all`
    run can seed a later narrow step. The state write never alters the printed dict."""
    result = run(client, want_market=want_market)
    if state_path is None:
        state_path = _default_state_path()
    state = {
        "embedded_wallet": result.get("embedded_wallet"),
        "strategies_full": result.get("strategies"),
        "strategy_groups": result.get("strategy_groups"),
        "totals": result.get("totals"),
        "exposure": result.get("exposure"),
        "signals": result.get("signals"),
        "meta_warnings": (result.get("meta") or {}).get("warnings", []),
        "registry_source": (result.get("meta") or {}).get("registry_source"),
        "catalog_source": (result.get("meta") or {}).get("catalog_source"),
        "profile_source": (result.get("meta") or {}).get("profile_source"),
        "has_multi_wallet_strategy": (result.get("meta") or {}).get("has_multi_wallet_strategy", False),
    }
    _save_state(state_path, state)
    return result


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    # optional leading positional STEP (money|strategies|positions|all); default `all` = the composed
    # one-shot (unchanged output + shape). Parsed before argparse so the flags stay shared.
    step = "all"
    if argv and not argv[0].startswith("-"):
        cand = argv[0]
        if cand not in _STEPS:
            print(json.dumps({"strategies": [], "meta": {"error": f"unknown step {cand!r}; "
                                                         f"expected one of {', '.join(_STEPS)}"}}))
            return 1
        step, argv = cand, argv[1:]

    ap = argparse.ArgumentParser(
        description="senpi portfolio engine (real-time wallet taxonomy + analysis). Optional leading STEP: "
                    "money|strategies|positions|all (default all = the composed one-shot). Steps share a "
                    "state file so later steps don't re-fetch.")
    ap.add_argument("--no-market", action="store_true", help="skip per-asset market enrichment")
    ap.add_argument("--state", default=None,
                    help="shared STEP state file path (default <tempdir>/senpi-portfolio/state.json) — "
                         "not the runtime state dir; this engine no longer reads one")
    ap.add_argument("--fixture", help="offline: path to a recorded MCP-response map (tests only)")
    ap.add_argument("--dry", action="store_true", help="dump raw MCP responses for schema debugging")
    # `step` was already peeled off argv above; feed the remainder (flags only).
    args = ap.parse_args(argv)

    if args.fixture:
        try:
            with open(args.fixture) as f:
                client = _FixtureClient(json.load(f))
        except Exception as e:  # noqa
            print(json.dumps({"strategies": [], "meta": {"error": f"fixture load failed: {e}"}}))
            return 1
    else:
        try:
            client = _get_client()
        except Exception as e:  # noqa
            print(json.dumps({"strategies": [], "meta": {"error": f"mcp client init failed: {e}"}}))
            return 1

    if args.dry:
        print(json.dumps(_dry(client), ensure_ascii=False, indent=2, default=str))
        return 0

    want_market = not args.no_market
    try:
        if step == "all":
            result = _all_and_persist(client, want_market, args.state)
        else:
            fn = _STEP_FNS[step]
            result = fn(client, want_market=want_market, state_path=args.state)
    except Exception as e:  # noqa
        print(json.dumps({"strategies": [], "meta": {"error": f"engine failure: {e}"}}))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
