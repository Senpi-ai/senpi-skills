#!/usr/bin/env python3
"""Shared helpers for the lifecycle scripts: openclaw CLI runner + tolerant JSON digging +
runtime/strategy lookups. Used by deploy.py and close.py.

The openclaw/MCP JSON shapes are not strictly pinned, so every extractor tries a few key
spellings and degrades gracefully (returns None / []) rather than throwing on a missing field.
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import json
import os
import re
import subprocess


# ---- openclaw CLI ----

# The two rc=-1 causes are not the same event: a spawn failure means the command NEVER RAN, a timeout
# means it ran and we stopped waiting (so whatever it dispatched may still be in flight). Callers on the
# money path have to tell them apart, so the spawn message carries a stable prefix instead of prose.
SPAWN_FAILED_PREFIX = "command not found: "


def run_cli(args, timeout=60):
    """Run a CLI command; return (returncode, stdout, stderr). rc=-1 on spawn failure/timeout —
    `SPAWN_FAILED_PREFIX` on stderr distinguishes the never-ran case from the stopped-waiting one.

    Suppresses the senpi plugin's info logs (which it prints to STDOUT and which otherwise corrupt
    `--json` output) by forcing SENPI_LOG_LEVEL=error in the child env."""
    env = dict(os.environ, SENPI_LOG_LEVEL="error")
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout, env=env)
        return p.returncode, p.stdout, p.stderr
    except FileNotFoundError:
        return -1, "", f"{SPAWN_FAILED_PREFIX}{args[0]}"
    except subprocess.TimeoutExpired:
        return -1, "", f"timed out after {timeout}s: {' '.join(args)}"


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


def cli_json(args, timeout=60):
    """Run a CLI command expected to emit JSON on stdout; return the parsed object or None."""
    rc, out, _err = run_cli(args, timeout)
    if rc != 0 or not out.strip():
        return None
    return _extract_json(out)


# Stdout/stderr lines that are pure plugin/telemetry chatter — never the failure cause.
_NOISE_PREFIXES = ("[plugins]",)


def _head_and_tail(text, limit):
    """Both ends of an over-limit error, with the omission said out loud. A refusal opens with
    the `[CODE]` line agents branch on and closes with the cause detail — a tail-only cut
    decapitates the code line (the scanner-`enabled` refusal did exactly this), a head-only cut
    loses the cause. The tail gets the larger share: the code line is short, causes ramble."""
    if len(text) <= limit:
        return text
    head_n = limit // 3
    tail_n = limit - head_n
    marker = f"\n… [{len(text) - head_n - tail_n} chars omitted] …\n"
    return text[:head_n].rstrip() + marker + text[-tail_n:].lstrip()


def error_tail(err, out="", limit=600):
    """Best-available error text from a failed CLI call: prefer stderr, fall back to stdout;
    drop blank + known banner lines; over `limit` chars keep BOTH ends (head + tail, loud
    omission marker) — the head carries the `[CODE]` line agents branch on, the END carries
    the cause CLI failures print last. The raw fallback (nothing survived the noise filter)
    stays a plain LAST-`limit` cut: its text is unfiltered, so its head is exactly the banner
    flood that destroyed a cause once before (the register-error banner-flood blackout).

    ANSI escapes are stripped FIRST (before filtering) so a color-coded `\\x1b[90m[plugins]…`
    banner still matches the noise filter, and no raw escape sequences leak into
    `.deploy-state.json` / the report. If a stream filters down to nothing (all banner noise),
    we try the SAME filter on the other stream before the raw-tail fallback — a Node CLI that
    prints banners to stderr and the real error to stdout must not surface the banner as the
    cause. Filtering must never turn a non-empty capture into an empty message."""
    err_s = _strip_ansi(err or "").strip()
    out_s = _strip_ansi(out or "").strip()
    for text in (err_s, out_s):
        if not text:
            continue
        lines = [ln for ln in text.splitlines()
                 if ln.strip() and not ln.strip().startswith(_NOISE_PREFIXES)]
        cleaned = "\n".join(lines).strip()
        if cleaned:
            return _head_and_tail(cleaned, limit)
    return (err_s or out_s)[-limit:]


# ---- tolerant extraction ----

def dig(obj, *keys, default=None):
    """Return obj[k] for the first key present (case-insensitive), else default."""
    if not isinstance(obj, dict):
        return default
    lower = {k.lower(): v for k, v in obj.items()}
    for k in keys:
        if k in obj:
            return obj[k]
        if k.lower() in lower:
            return lower[k.lower()]
    return default


def find_list(obj, *wrapper_keys):
    """Locate a list payload: a bare list, or nested under a common wrapper key."""
    if isinstance(obj, list):
        return obj
    if isinstance(obj, dict):
        for k in wrapper_keys + ("data", "result", "items"):
            v = dig(obj, k)
            if isinstance(v, list):
                return v
        # single nested dict that itself wraps a list
        d = obj.get("data") if isinstance(obj.get("data"), dict) else None
        if d:
            return find_list(d, *wrapper_keys)
    return []


# ---- runtime lookups (openclaw senpi runtime ...) ----

_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def _strip_ansi(s):
    return _ANSI.sub("", s)


def wallet_match(a, b):
    """Compare two wallet strings, tolerating the truncated `0xabc…wxyz` / `0xabc...wxyz` form that
    `runtime list` prints in a TTY (full addresses when piped). Case-insensitive."""
    if not a or not b:
        return False
    a, b = str(a).strip().lower(), str(b).strip().lower()
    if a == b:
        return True
    for x, y in ((a, b), (b, a)):
        for sep in ("...", "…"):
            if sep in x:
                pre, _, suf = x.partition(sep)
                if pre and suf and y.startswith(pre) and y.endswith(suf):
                    return True
    return False


def _parse_runtime_list(out):
    """Parse `runtime list` text → (rows, valid). `valid` is True only when the output actually looked
    like the runtime-list table (header row seen, or an explicit 'no runtimes' line) — so a garbled /
    banner-only stdout that yields zero rows is reported as NOT valid rather than as an empty inventory."""
    rows, seen_header, empty_marker = [], False, False
    for line in out.splitlines():
        line = _strip_ansi(line).strip()
        if not line:
            continue
        low = line.lower()
        if not seen_header:
            if low.startswith("id") and "status" in low and "wallet" in low:
                seen_header = True
            continue
        if "no runtimes" in low:
            empty_marker = True
            break
        parts = [p for p in re.split(r"\s{2,}|\t+", line) if p]
        if len(parts) >= 2:
            rows.append({"name": parts[0], "wallet": parts[1],
                         "source": parts[2] if len(parts) > 2 else None, "status": parts[-1]})
    return rows, (seen_header or empty_marker)


def list_runtimes():
    """All runtimes (running AND stopped) by parsing `runtime list` text. NOTE on runtime v3: `runtime
    list` has no --json (human text only), and `status --json` is *flaky* — it transiently returns an
    empty `statuses[]` even while runtimes are running — so it is NOT a reliable inventory. The text
    table (id / wallet / source / status) is authoritative; use `status -r <id>` only for health.
    Returns [] on a failed/garbled read; a caller that must NOT conflate 'none' with 'unreadable'
    (teardown's money path) uses `list_runtimes_or_none` instead."""
    rc, out, _err = run_cli(["openclaw", "senpi", "runtime", "list"])
    if rc != 0:
        return []
    rows, _valid = _parse_runtime_list(out)
    return rows


def list_runtimes_or_none():
    """Like `list_runtimes()` but returns None when the `runtime list` read FAILED (rc != 0) or produced
    unparseable output — so callers can tell 'no runtimes' (→ []) from 'couldn't read the inventory'
    (→ None). The teardown money path must never mistake an unreadable inventory for 'the runtime is gone'."""
    rc, out, _err = run_cli(["openclaw", "senpi", "runtime", "list"])
    if rc != 0:
        return None
    rows, valid = _parse_runtime_list(out)
    return rows if valid else None


def runtime_name(rt):
    return dig(rt, "name", "id", "runtime_id", "runtimeId", "runtimeName")


def runtime_wallet(rt):
    # text-list entries carry "wallet" directly; `status -r` entries nest it under components — deep search.
    w = dig(rt, "wallet", "address", "walletAddress", "strategyWalletAddress", "strategyWallet")
    return w or _deep_first(rt, ["address", "wallet", "walletAddress", "strategyWalletAddress"])


def runtime_running(rt):
    st = dig(rt, "status", "state", "running", "health", "overallHealth")
    if isinstance(st, bool):
        return st
    s = str(st).lower()
    # "running — NO ENTRY SCANNERS" (B1) is a RUNNING runtime whose entry scanners never wired —
    # the process is up (and DSL may be protecting positions), so it must never read as stopped:
    # deploy.py's create closes "open but not running" strategies, and that would flatten a live one.
    if s.startswith("running"):
        return True
    if s in ("active", "live", "ok", "true", "healthy", "degraded"):
        return True
    return False


def runtime_no_entry_scanners(rt):
    """True when `runtime list` marks this running runtime as `running — NO ENTRY SCANNERS`
    (entry scanners never wired — positive wiring-failure evidence; NOT live)."""
    st = dig(rt, "status", "state")
    return "no entry scanners" in str(st or "").lower()


def find_runtime(name):
    for rt in list_runtimes():
        if runtime_name(rt) == name:
            return rt
    return None


def find_runtime_by_wallet(wallet):
    """Find a runtime bound to a wallet address (close maps strategy→runtime by wallet,
    so it doesn't depend on the runtime name). Tolerates truncated TTY wallets."""
    if not wallet:
        return None
    for rt in list_runtimes():
        if wallet_match(runtime_wallet(rt), wallet):
            return rt
    return None


def _deep_first(obj, keys):
    """Deep-search a nested obj for the first value under any of `keys` (case-insensitive)."""
    if isinstance(obj, dict):
        v = dig(obj, *keys)
        if v is not None:
            return v
        for x in obj.values():
            r = _deep_first(x, keys)
            if r is not None:
                return r
    elif isinstance(obj, list):
        for x in obj:
            r = _deep_first(x, keys)
            if r is not None:
                return r
    return None


def runtime_status(name, timeout=15):
    """`openclaw senpi status -r <name> --json` — lightweight per-runtime health (or None).

    Single fast read, no in-call retry. `_check_live` prefers the batched `runtime_health_map` (which
    already retries) and only falls here when a runtime is missing from that map; retry across reads is
    the caller's job (re-run `verify`, or its `--max-wait` poll loop) — NOT per call. Each openclaw
    invocation pays ~2-3s startup, so a per-call retry loop silently blows verify's fast-single-check
    budget (regression seen live: 2× retries here + on `state` pushed one pass past the tool timeout)."""
    return cli_json(["openclaw", "senpi", "status", "-r", name, "--json"], timeout)


def runtime_state(name, timeout=15):
    """`openclaw senpi state -r <name> --json` → parsed state, or None.

    Single fast read — do NOT retry in-call. `senpi.getSystemState` transiently THROWS for minutes
    after a runtime starts (external-scanner subprocesses still launching); a throw exits non-zero
    with empty stdout so this returns None. That is EXPECTED and cheap to handle: the scanner verdict
    falls straight through to `senpi status` (see deploy.py `_scanner_verdict`), which answers while
    `state` is still throwing. Waiting/retrying on `state` here just burns the caller's budget for a
    read we already know how to do without — retry belongs in the poll loop, not this call."""
    return cli_json(["openclaw", "senpi", "state", "-r", name, "--json"], timeout)


def scanner_health_in_status(status_entry, scanner_name):
    """Per-scanner health string (or None) from a `senpi status` entry (getHealthStatus), by stable
    scanner name. The scanners component is `components.scanners.scanners[] = [{address, scannerId,
    health}]`. This is the RELIABLE liveness source: getHealthStatus keeps answering while
    getSystemState is still throwing post-deploy, and the runtime has already computed each scanner's
    health verdict for us here. Returns None both when status is unreadable and when the scanner isn't
    (yet) in the list — the caller treats both the same (a running runtime supervises the scanner
    regardless), so there is intentionally no 'was the list populated?' distinction to return."""
    comp = None
    comps = dig(status_entry, "components")
    if isinstance(comps, dict):
        comp = comps.get("scanners")
    if not isinstance(comp, dict):
        comp = _deep_first(status_entry, ["scanners"])  # tolerate a flatter/rewrapped shape
    if isinstance(comp, dict):
        rows = comp.get("scanners")
    elif isinstance(comp, list):
        rows = comp
    else:
        rows = None
    if not isinstance(rows, list):
        return None
    for r in rows:
        if dig(r, "scannerId", "name", "scanner") == scanner_name:
            return str(dig(r, "health") or "").lower() or None
    return None


def runtime_health_map(timeout=15):
    """Health for ALL running runtimes in ONE `status --json` call, keyed by runtime name. One CLI
    invocation regardless of fleet size (each openclaw call pays ~2-3s plugin-load startup, so per-runtime
    calls are the slow path). The gateway is flaky-empty, so retry the single call twice."""
    for _ in range(2):
        obj = cli_json(["openclaw", "senpi", "status", "--json"], timeout)
        sts = find_list(obj, "statuses") if obj else []
        if sts:
            return {runtime_name(e): e for e in sts}
    return {}


def health_verdict(status_json):
    """Map a `senpi status` payload to healthy | degraded | unhealthy | unknown | None (shape-tolerant).

    Fail-closed: any verdict that is PRESENT but not a recognised healthy/broken value — the
    runtime's `unknown` (scanner not yet proven by a tick), `disabled`, or future vocabulary —
    maps to `unknown`, never to None: None triggers the caller's "running" fallback, which would
    paint an unproven runtime ✅. None is reserved for payloads with no health field at all.
    """
    h = _deep_first(status_json, ["overallHealth", "health", "overall", "status"])
    if h is None:
        return None
    h = str(h).lower()
    if h in ("healthy", "ok", "running", "live", "true"):
        return "healthy"
    if h in ("degraded", "warn", "warning"):
        return "degraded"
    if h in ("unhealthy", "failed", "error", "down", "false"):
        return "unhealthy"
    return "unknown"  # unknown / disabled / unrecognised verdict → not proven live


def active_positions(status_json):
    """Best-effort active-position count from a `senpi status` payload (None if not found)."""
    n = _deep_first(status_json, ["activePositions", "activePositionCount", "openPositions",
                                  "positionCount", "numPositions", "positions"])
    if isinstance(n, bool):
        return None
    if isinstance(n, (int, float)):
        return int(n)
    if isinstance(n, str) and n.strip().lstrip("-").isdigit():  # the gateway stringifies numbers
        return int(n)
    if isinstance(n, list):
        return len(n)
    return None


# ---- strategy lookups (MCP strategy_list) ----

def list_strategies(mcp, timeout=15, statuses=None):
    """strategy_list. Pass `statuses` to filter server-side (much smaller payload than fetching a long
    closed/failed history — strategy_list with no filter can return many dozens of records)."""
    args = {"status": statuses} if statuses else {}
    try:
        res = mcp.mcp_call("strategy_list", timeout=timeout, **args)
    except Exception:  # noqa: BLE001 — degrade to empty on transport error
        return []
    return find_list(res, "strategies")


def strategy_obj(x):
    """Unwrap the strategy dict from a response or list entry. strategy_create_custom_strategy nests
    it at data.strategy; strategy_list entries may be flat or wrapped. Tries data.strategy → strategy
    → data → x, returning the first dict that carries an id/status field."""
    if not isinstance(x, dict):
        return {}
    for path in (("data", "strategy"), ("strategy",), ("data",)):
        cur = x
        for k in path:
            cur = cur.get(k) if isinstance(cur, dict) else None
        if isinstance(cur, dict) and (dig(cur, "strategyId", "id", "strategy_id")
                                      or dig(cur, "status", "state")):
            return cur
    return x  # already the strategy object (flat)


def strategy_id_of(s):
    return dig(strategy_obj(s), "strategyId", "id", "strategy_id")


def strategy_status(s):
    return dig(strategy_obj(s), "status", "state")


def strategy_wallet(s):
    return dig(strategy_obj(s), "strategyWalletAddress", "walletAddress", "wallet", "address")


def strategy_name(s):
    """The strategyName a strategy was created under (create sets it to <id> or <id>-<instance>).
    Lets a lost-state redeploy match a backend strategy back to its package instance."""
    return dig(strategy_obj(s), "strategyName", "tradingStrategyName", "name")


def strategy_skill(s):
    """The package id a strategy was created under. Lives in strategyMetadata.skillName (set by
    strategy_create_custom_strategy's skillName arg); falls back to tradingStrategyName."""
    o = strategy_obj(s)
    meta = dig(o, "strategyMetadata", "metadata")
    if isinstance(meta, dict):
        sk = dig(meta, "skillName", "skill_name")
        if sk:
            return sk
    return dig(o, "skillName", "skill_name", "skill") or dig(o, "tradingStrategyName", "name")


# strategies in these states are done — never close them again, and they must NOT block a new deploy.
DEAD_STATUSES = ("CLOSED", "FAILED", "INACTIVE", "TERMINATED", "CLOSING_DONE")


def strategy_trader(s):
    """The trader a COPY strategy follows (None for custom/manual). Distinguishes copy-trading
    (managed by the copy engine, no runtime) from autonomous custom strategies."""
    return dig(strategy_obj(s), "traderAddress", "trader")


def strategy_type(s):
    return dig(strategy_obj(s), "strategyType", "type")


def strategy_open(s):
    return str(strategy_status(s) or "").upper() not in DEAD_STATUSES


def strategies_for(mcp, skill_name=None, strategy_id=None, wallet=None, timeout=15, statuses=None):
    """Return strategies matching any provided filter (skill_name / strategyId / wallet). Pass `statuses`
    to filter server-side (faster; e.g. close only needs live ones). Leave None when you must also see
    CLOSED/FAILED (e.g. create's reconcile checks a recorded id's terminal state)."""
    out = []
    for s in list_strategies(mcp, timeout, statuses=statuses):
        if strategy_id is not None and strategy_id_of(s) != strategy_id:
            continue
        if skill_name is not None and strategy_skill(s) != skill_name:
            continue
        if wallet is not None and str(strategy_wallet(s) or "").lower() != str(wallet).lower():
            continue
        out.append(s)
    return out
