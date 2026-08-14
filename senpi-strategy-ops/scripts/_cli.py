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


def cli_json(args, timeout=60):
    """Run a CLI command expected to emit JSON on stdout; return the parsed object or None.

    Does NOT gate on `rc`: some verbs (`deploy status`) exit with the JOB's own verdict code, not
    a transport code, so a refusal still prints a complete report. Discarding it on `rc != 0` threw
    away the one payload callers most need to read. Mirrors `deploy.py`'s `read_status`, which
    already ignores rc for the same reason. A transport failure still degrades to None here because
    it also leaves `out` empty or unparseable — the sentinel survives on those grounds, not on rc."""
    _rc, out, _err = run_cli(args, timeout)
    if not out.strip():
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


def find_list_or_none(obj, *wrapper_keys):
    """Locate a list payload: a bare list, or nested under a common wrapper key. Returns **None**
    when the payload carries no list at all — which is NOT the same fact as an empty list.

    `find_list`'s `[]` answers both questions with one value, so a response whose shape drifted (a
    renamed wrapper, an error envelope) reads as "there is nothing" at every call site that trusts
    it. Callers that must fail closed on an unreadable surface use this one."""
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
            return find_list_or_none(d, *wrapper_keys)
    return None


def find_list(obj, *wrapper_keys):
    """Locate a list payload (see `find_list_or_none`), degrading an unrecognised shape to `[]`."""
    found = find_list_or_none(obj, *wrapper_keys)
    return [] if found is None else found


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
    list --json` exists only on newer builds (see `senpi-trading-runtime/references/runtime-cli.md`) and
    this reader must keep working on the older ones, and `status --json` is *flaky* — it transiently
    returns an empty `statuses[]` even while runtimes are running — so it is NOT a reliable inventory.
    The text table (id / wallet / source / status) is what every build prints and stays authoritative
    here; use `status -r <id>` only for health.
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


# The keys that carry a HEALTH verdict the runtime itself computed. `RuntimeHealthStatus.health` and
# each `components.scanners.scanners[].health` are the real ones (senpi-trading-runtime
# `src/health/types.ts`); `overallHealth` is the older spelling this repo has always accepted.
HEALTH_KEYS = ("overallHealth", "health")
# Keys that carry a RUN or JOB state, not health: `runtime list`'s `status`, and a deploy snapshot's
# `state.overall`. They prove a process (or a job) exists — never that it is working.
_RUN_STATE_KEYS = ("overall", "status")


def _classify_health(raw, allow_healthy):
    h = str(raw).lower()
    if h in ("degraded", "warn", "warning"):
        return "degraded"
    if h in ("unhealthy", "failed", "error", "down", "false"):
        return "unhealthy"
    if allow_healthy and h in ("healthy", "ok"):
        return "healthy"
    return "unknown"  # unknown / disabled / a run state / unrecognised verdict → not proven live


def run_state(status_json):
    """The RUN/JOB state string an entry published (`status`/`overall`), or None — for QUOTING.

    Not health, and never rendered as health: it is the evidence a caller quotes when
    `health_verdict` reached its verdict off a run state because no health field was published."""
    h = _deep_first(status_json, list(_RUN_STATE_KEYS))
    return None if h is None else str(h)


def health_verdict(status_json):
    """Map a `senpi status` payload to healthy | degraded | unhealthy | unknown | None (shape-tolerant).

    Fail-closed twice over:

    * Any verdict that is PRESENT but not a recognised healthy/broken value — the runtime's
      `unknown` (scanner not yet proven by a tick), `disabled`, or future vocabulary — maps to
      `unknown`, never to None: None triggers the caller's "running" fallback, which would paint an
      unproven runtime ✅. None is reserved for payloads with no health field at all.
    * **Only a real health field may render `healthy`.** The runtime's health vocabulary is
      `healthy|degraded|unhealthy|disabled|unknown` — "running"/"live"/"true" are not in it, they are
      RUN states. Reading one as healthy turned a `{name, status: "running"}` entry into a ✅ for a
      runtime no tick had ever proven. A run-state key can still DOWNGRADE (positive broken evidence
      is believed wherever it is found); it can never promote.
    """
    h = _deep_first(status_json, list(HEALTH_KEYS))
    if h is not None:
        return _classify_health(h, allow_healthy=True)
    h = _deep_first(status_json, list(_RUN_STATE_KEYS))
    if h is None:
        return None
    return _classify_health(h, allow_healthy=False)


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

class ReadFailed(Exception):
    """A surface a caller needs came back unusable — a transport failure, or a payload whose shape
    carries no answer. Raised by CALLERS of the fail-closed `*_or_none` readers
    (`list_strategies_or_none`, `list_runtimes_or_none`, `cli_json`) once they have turned that
    sentinel into a refusal: a caller that catches this must render NO verdict."""


def list_strategies(mcp, timeout=15, statuses=None):
    """strategy_list, degrading to `[]` on a transport error or an unrecognised payload. Pass
    `statuses` to filter server-side (much smaller payload than fetching a long closed/failed
    history — strategy_list with no filter can return many dozens of records).

    A CHECK must not use this: "no strategies" and "I could not read the strategies" come back as
    the same empty list. `list_strategies_or_none` is that caller's reader."""
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
    Lets a lost-state redeploy match a backend strategy back to its package instance.

    `_first_written`, not `dig`: the MCP now SELECTS `strategyName`, so the key is PRESENT on every
    row and the column is NULLABLE (null on 21 of 23 rows in a live sample). `dig` answers with the
    first key that exists — null included — so a present-null would answer for the whole chain and
    switch off the `tradingStrategyName` fallback that is the only name most rows carry today. That
    is the exact shape of an upstream fix turning a working reader OFF.

    Fixed HERE and not in `dig` because `dig`'s present-but-falsy answer is load-bearing for other
    callers — `find_list_or_none` (`[]` is an answer, not an unreadable shape), `runtime_running`
    (`running: False` must not fall through to a health string) and `strategy_funded` (`totalFunded:
    0` is what LANDED) all flip the wrong way if it skips falsy. This reader has exactly ONE
    production consumer (`deploy.py`'s `verify_instance`), so the narrow fix is also the small one."""
    return _first_written(strategy_obj(s), "strategyName", "tradingStrategyName", "name")


def strategy_name_and_source(s):
    """What to CALL a strategy, and WHICH FIELD said so — `(name, name_source)`.

    Same fallback chain as `strategy_name` (`strategyName` → `tradingStrategyName` → `name`), and the
    exact `(name, name_source)` shape `senpi-portfolio/scripts/portfolio.py`'s
    `_strategy_name_and_source` returns — quoted, not reinvented. `strategyName` is the strategy's
    own name; `tradingStrategyName`/`name` are the package id standing in for an unnamed strategy
    (nullable by mechanism — `strategy_create_custom_strategy` makes it optional). `name_source`
    records which of those answered, so a caller can tell "this strategy is named cub" from "this
    strategy is unnamed and cub is its package" — a distinction `status.py`'s runtime-name column
    collapsed by printing a different field (`runtime`, not `strategyName`) in what a reader takes
    for the name column.

    The chain and the ANSWER are held identical to `portfolio._strategy_name_and_source` by
    `senpi-portfolio/tests/test_name_reader_parity.py::test_ops_chain_answers_match_portfolio_where_the_readers_cannot_disagree`
    — but this reader is built on `_first_written` below (`dig()`-dispatched: case-insensitive, no
    `.strip()`, no container/bool exclusion), NOT on portfolio's vendored one (exact-cased, strips,
    excludes dict/list/bool). The two are held to the same chain, not the same implementation, so a
    handful of shapes legitimately diverge — a case-only key variant, a whitespace-only string, a
    dict/bool/bare-scalar value in a name field — and each one is pinned to both readers' real
    answers by that same test file's `test_ops_chain_diverges_only_where_pinned_as_intended`, not
    left as an untested gap."""
    o = strategy_obj(s)
    for key in ("strategyName", "tradingStrategyName", "name"):
        got = _first_written(o, key)
        if got:
            return got, key
    return "strategy", None


def strategy_name_match(a, b):
    """Do these two strategy names name the same strategy? Case- and whitespace-insensitive; an
    empty/absent name on EITHER side is never a match.

    Case-folded because the backend case-normalizes what it stores (observed in production create
    calls: `"WARPATH"` in, `"warpath"` back) while `deploy.py`'s `_sanitize_strategy_name`
    deliberately preserves `[A-Za-z0-9_-]` capitals — so a mixed-case package id derives a name a
    case-SENSITIVE compare could never match, and the check reads its own live funded wallets as
    "nothing is funded here" and steers at a deploy that funds a second one beside each.

    Two absences are not an identity: an unnamed strategy matched on `"" == ""` binds to the first
    instance that asks. ONE producer, because the runtime's deploy verb asks this same question of
    this same field (`src/deploy/orchestrator.ts`, the `byName` filter) — the two consumers
    answering differently is how a wallet gets funded twice."""
    a, b = str(a or "").strip().lower(), str(b or "").strip().lower()
    return bool(a) and a == b


def strategy_skill_match(a, b):
    """Do these two attribution stamps name the same PACKAGE? Same normalisation as
    `strategy_name_match` — `.strip().lower()` on both sides — and an empty/absent stamp on either
    side is never a match.

    Case-folded for the same reason and against the same consumer: the runtime's deploy verb reads
    this field as `(s.skillName ?? "").trim().toLowerCase()` (`src/deploy/orchestrator.ts`, both the
    name route's stamp partition and `gateOrCreate`'s), while the verb STAMPS `pkg.id` verbatim and
    nothing forces a package id lowercase. An exact compare here therefore diverges from the layer
    that wrote the stamp: for a package id with a capital in it the runtime's own gates match and
    `close.py <id>` matches NOTHING, printing "no OPEN strategies to close." over a live, funded,
    trading wallet — a false all-clear on the one command a user runs to get their money back.

    Two absences are not an identity, for a sharper reason than names: an unattributed strategy that
    matched an empty filter would be handed to a teardown that was asked about one package."""
    a, b = str(a or "").strip().lower(), str(b or "").strip().lower()
    return bool(a) and a == b


def _strategy_metadata(o):
    """`strategyMetadata` as a dict, or None when the record carries none this reader can navigate.

    A JSON-encoded STRING is parsed rather than skipped. The MCP declares the field
    `Record<string, unknown> | null` but only passes the backend's GraphQL scalar straight through
    (`strategy_list` spreads it verbatim), so the shape is the backend's promise, not the MCP's —
    and read as "no metadata", a serializer drift would make a genuinely FOREIGN-attributed wallet
    read as unattributed, which is exactly the adoption `strategy_skill_declared` exists to stop."""
    meta = dig(o, "strategyMetadata", "metadata")
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except ValueError:
            return None
    return meta if isinstance(meta, dict) else None


def _first_written(o, *keys):
    """The first key of `o` carrying a value someone actually WROTE — a present-but-empty field
    (`skillName: ""`) is silence, not a value.

    `dig` stops at the first key that EXISTS, so it hands back `''` verbatim; a caller comparing
    that against a package id ("is this someone else's wallet?") then reads effectively-silent
    attribution as a foreign owner and drops the user's own live wallet out of the match."""
    for k in keys:
        v = dig(o, k)
        if v:
            return v
    return None


def _declared_skill(o):
    """The WRITTEN attribution on a strategy object: `strategyMetadata.skillName`, else a top-level
    one. None when every leg is absent or empty — silence, at every leg, is never an owner."""
    meta = _strategy_metadata(o)
    if meta:
        sk = _first_written(meta, "skillName", "skill_name")
        if sk:
            return sk
    return _first_written(o, "skillName", "skill_name", "skill")


def strategy_skill(s):
    """The package id a strategy was created under. Lives in strategyMetadata.skillName (set by
    strategy_create_custom_strategy's skillName arg); falls back to tradingStrategyName."""
    o = strategy_obj(s)
    return _declared_skill(o) or _first_written(o, "tradingStrategyName", "name")


def strategy_skill_declared(s):
    """The package id a strategy was actually ATTRIBUTED to, or **None** when the record carries no
    attribution at all — the same fields as `strategy_skill` minus its tradingStrategyName fallback.

    Two different questions, so two readers. `strategy_skill` answers "which package do we file this
    under" and guesses from the name when nobody said; that guess is unusable for deciding whether a
    wallet belongs to SOMEONE ELSE, because an unattributed wallet named `spider-swing` and a wallet
    a package called `spider-swing` really created read identically through it. Only an attribution
    that was WRITTEN can rule a candidate out — silence must never be read as a foreign owner, and
    an EMPTY stamp is silence by the same rule: `''` is not a package id, so returning it verbatim
    would make every caller comparing against a package id read a blank field as a DIFFERENT owner.
    (No sanctioned path writes one — the MCP's create schema is `z.string().trim().min(1)` — so this
    is the invariant held at the reader, not a field sighting.)"""
    return _declared_skill(strategy_obj(s))


# strategies in these states are done — never close them again, and they must NOT block a new deploy.
DEAD_STATUSES = ("CLOSED", "FAILED", "INACTIVE", "TERMINATED", "CLOSING_DONE")

# Live (non-terminal) statuses — pass to `strategy_list` to filter SERVER-side (much smaller payload
# than a long closed/failed history). One list, shared by every caller: three copies of it drifted
# apart is three different answers to "what is still live".
LIVE_STATUSES = ["ACTIVE", "PAUSED", "CREATE_WALLET", "FUND_WALLET", "INITIALIZE_POSITIONS",
                 "SUBSCRIBE_TRADER", "CLOSING_POSITIONS"]


def strategy_funded(s):
    """The backend's own funded figure for a strategy, rendered for display (`$300`), or **None**
    when the record carries none. ONE producer: `status.py` and `deploy.py verify` must print the
    same number for the same wallet — and it is always what the backend says LANDED (`totalFunded`,
    else `netFunded`), never a requested amount.

    `initialBudget` used to close the chain, and it is the REQUESTED figure: a $500 request that
    partially funded $60 printed as "funded $500". Same rule as the deploy verb's
    `[W_BUDGET_FUNDED_UNREADABLE]` — an unread amount is reported as unknown and the reader is sent
    to a surface that can prove it, never rendered as a number nobody read."""
    v = dig(strategy_obj(s), "totalFunded", "netFunded")
    return f"${float(v):g}" if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def strategy_trader(s):
    """The trader a COPY strategy follows (None for custom/manual). Distinguishes copy-trading
    (managed by the copy engine, no runtime) from autonomous custom strategies."""
    return dig(strategy_obj(s), "traderAddress", "trader")


def strategy_type(s):
    return dig(strategy_obj(s), "strategyType", "type")


def strategy_open(s):
    return str(strategy_status(s) or "").upper() not in DEAD_STATUSES


def strategy_active(s):
    """True only for `ACTIVE` — the one status that means this wallet is TRADING.

    Everything else `strategy_open` admits is a transition: `CREATE_WALLET`/`FUND_WALLET`/
    `INITIALIZE_POSITIONS`/`SUBSCRIBE_TRADER` is a deploy still in flight, `PAUSED`/
    `CLOSING_POSITIONS` is a wallet being wound down (`close.py`'s own doctrine path leaves exactly
    that window open, runtime already removed). Open and active are different questions: reading
    open as active is how a teardown-in-progress gets a "start trading this funded wallet" steer."""
    return str(strategy_status(s) or "").upper() == "ACTIVE"


def list_strategies_or_none(mcp, timeout=15, statuses=None, why=None):
    """`strategy_list` for callers that must fail CLOSED: returns **None** when the read did not
    produce an answer — so a money path can tell 'no strategies' (→ `[]`, an answer) from 'couldn't
    read the list' (→ None) and REFUSE rather than fund a second wallet next to an unread live one.
    Mirrors `list_runtimes_or_none`.

    **Two ways a read produces no answer, and both return None:**

    * the call FAILED — a transport/tool error;
    * the call ANSWERED, but with a payload carrying no recognisable strategies list — a renamed
      wrapper key, an error envelope, any response-shape drift.

    The second is the one that hid, which is why this routes through `find_list_or_none` and not
    `find_list`: `find_list` navigating nothing returns `[]`, which looks exactly like a backend
    with nothing live, so a shape drift renders as "no live strategy — nothing is funded here" and
    steers at the money path over wallets that may be perfectly live. A genuinely empty list is
    still `[]` — that IS an answer, and the only one of the three this function reports as one.

    The **verdict** is the same for both, deliberately — render NOTHING, say the surface was
    unreadable — so there is one sentinel and no caller can treat either mode as benign. The
    **cause** is still worth printing, so pass `why`: a list the reason is appended to, for callers
    that render a "could not check" line an operator has to act on ("no SENPI_AUTH_TOKEN" and "the
    payload carried no list" send them to different places). Callers that only branch omit it."""
    args = {"status": statuses} if statuses else {}
    try:
        res = mcp.mcp_call("strategy_list", timeout=timeout, **args)
    except Exception as e:  # noqa: BLE001 — the WHOLE point: surface the failure instead of swallowing to []
        if why is not None:
            why.append(f"the MCP `strategy_list` call failed ({e})")
        return None
    found = find_list_or_none(res, "strategies")
    if found is None and why is not None:
        why.append("MCP `strategy_list` answered, but with no recognisable strategies list in it "
                   "(an empty list would have read as an answer; this payload carries none)")
    return found


def _match_strategy(s, skill_name, strategy_id, wallet):
    if strategy_id is not None and strategy_id_of(s) != strategy_id:
        return False
    # The stamp compare goes through `strategy_skill_match`, not `!=`: the runtime case-folds this
    # field (it stamps `pkg.id` verbatim and reads it back lowercased) and an exact compare here
    # made `close.py <MixedCaseId>` match nothing and report "no OPEN strategies to close." over a
    # live funded wallet. One producer for the comparison, so the two layers cannot disagree.
    if skill_name is not None and not strategy_skill_match(strategy_skill(s), skill_name):
        return False
    if wallet is not None and str(strategy_wallet(s) or "").lower() != str(wallet).lower():
        return False
    return True


def strategies_for(mcp, skill_name=None, strategy_id=None, wallet=None, timeout=15, statuses=None):
    """Return strategies matching any provided filter (skill_name / strategyId / wallet). Pass `statuses`
    to filter server-side (faster; e.g. close only needs live ones). Leave None when you must also see
    CLOSED/FAILED (e.g. create's reconcile checks a recorded id's terminal state). Fail-OPEN ([] on read
    error) — fine for reads that only ADD work; use `strategies_for_or_none` on a money path."""
    return [s for s in list_strategies(mcp, timeout, statuses=statuses)
            if _match_strategy(s, skill_name, strategy_id, wallet)]


def strategies_for_or_none(mcp, skill_name=None, strategy_id=None, wallet=None, timeout=15,
                           statuses=None, why=None):
    """Fail-CLOSED `strategies_for`: returns None when the `strategy_list` read produced no answer —
    either it failed or its payload carried no list — so a money path can refuse rather than mistake
    'unreadable' for 'none' (the double-fund / un-consented-flatten trap). `why` carries the cause
    out for callers that render it; see `list_strategies_or_none`."""
    rows = list_strategies_or_none(mcp, timeout, statuses=statuses, why=why)
    if rows is None:
        return None
    return [s for s in rows if _match_strategy(s, skill_name, strategy_id, wallet)]
