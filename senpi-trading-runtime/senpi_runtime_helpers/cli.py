"""senpi-helpers CLI — operator interface for senpi_runtime_helpers daemons.

Invocation:
    ${OPENCLAW_WORKSPACE}/skills/senpi-trading-runtime/senpi-helpers <subcommand> [args]

The CLI is a thin reader + signaler over the self-describing state files
written by `producer_daemon` (`pid.json`, `boot.json`, `heartbeat.json`
under `${SENPI_HELPERS_STATE_DIR}/<name>/`). No coupling to the openclaw
gateway: the SDK was built to bypass it, the CLI follows suit.

Subcommands ship incrementally — this module is structured so each new
subcommand is one self-contained function + one `subparsers.add_parser`
entry. See `senpi-trading-runtime/references/senpi-helpers-cli.md` for
full operator documentation.
"""
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
# Source: https://github.com/Senpi-ai/senpi-skills

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from . import manage as _manage
from . import state as _state
from . import stats as _stats


# ─── Helpers shared across subcommands ──────────────────────────────────────
#
# Schema awareness: this module reads pid.json / boot.json / heartbeat.json
# via `state.read_*`. Each file is independently versioned — see the
# top-of-file comment in `state.py` for the protocol. The CLI uses `.get(...)`
# for any field that was added in a later schema (e.g. `cmdline_fingerprint`,
# `log_path` in boot.json), so reading a legacy-schema file degrades cleanly
# instead of KeyError'ing.


def _parse_iso(s: Optional[str]) -> Optional[datetime]:
    """Parse the millisecond-precision ISO string written by `state.py`.

    Returns a tz-aware UTC datetime or None on any failure. Tolerates the
    legacy non-ms variant `YYYY-MM-DDTHH:MM:SSZ` as well as the canonical
    `YYYY-MM-DDTHH:MM:SS.mmmZ`.
    """
    if not isinstance(s, str) or not s:
        return None
    payload = s[:-1] if s.endswith("Z") else s
    try:
        dt = datetime.fromisoformat(payload)
    except (ValueError, TypeError):
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def _age_seconds(iso_string: Optional[str]) -> Optional[int]:
    """Seconds between now and the parsed timestamp. None on parse failure."""
    dt = _parse_iso(iso_string)
    if dt is None:
        return None
    delta = datetime.now(timezone.utc) - dt
    # Negative ages are theoretically possible due to clock skew between
    # the daemon's host and the CLI's host. Clamp at 0 — surfacing a
    # negative age would just confuse operators.
    return max(0, int(delta.total_seconds()))


def _format_duration(seconds: Optional[int]) -> str:
    """Human-readable short form: `45s`, `2m 30s`, `14h 32m`, `3d 12h`."""
    if seconds is None:
        return "-"
    if seconds < 60:
        return f"{seconds}s"
    minutes, _ = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {seconds % 60}s"
    hours, minutes_rem = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h {minutes_rem}m"
    days, hours_rem = divmod(hours, 24)
    return f"{days}d {hours_rem}h"


def _shorten_wallet(wallet: Optional[str]) -> str:
    if not wallet:
        return "-"
    return f"{wallet[:6]}…{wallet[-4:]}" if len(wallet) > 14 else wallet


def _resolve_name_readonly(args: argparse.Namespace) -> Optional[str]:
    """Resolve `<name>` for READ-ONLY subcommands (list, health, stats,
    boot, logs).

    Rules:
      - Explicit `<name>` arg always wins.
      - If no daemons registered → return None (caller surfaces "not found").
      - If exactly one daemon registered → use it. Single-daemon hosts
        shouldn't pay the cost of always typing the name for inspection.
      - If multiple daemons registered → return None and print a list.

    For DESTRUCTIVE subcommands (stop, restart, start), use
    `_resolve_name_explicit` instead — auto-resolve is a footgun when the
    operator could be on the wrong box.
    """
    if getattr(args, "name", None):
        return args.name
    names = _state.list_daemons(state_dir=args.state_dir)
    if len(names) == 0:
        sys.stderr.write(
            "senpi-helpers: no daemons found in state dir "
            f"({_state.get_state_dir(args.state_dir)}).\n"
            "Start a daemon first; it writes its state files on boot.\n"
        )
        return None
    if len(names) == 1:
        return names[0]
    sys.stderr.write(
        "senpi-helpers: multiple daemons registered; pass <name> explicitly.\n"
        f"  Available: {', '.join(names)}\n"
    )
    return None


def _resolve_name_explicit(
    args: argparse.Namespace, *, action: str,
) -> Optional[str]:
    """Resolve `<name>` for DESTRUCTIVE subcommands (stop, restart, start).

    Unlike `_resolve_name_readonly`, this NEVER auto-resolves to the only
    daemon on the host. The operator (or agent) MUST type the name. This
    closes a footgun: `senpi-helpers stop` typed on the wrong SSH session
    used to silently kill whatever daemon happened to be on that box.

    Read-only commands keep the auto-resolve convenience because they can
    only mis-inform — not destroy state.

    Why no interactive `[y/N]` prompt:
        The primary tool consumer is the openclaw agent (via its `exec`
        tool, which has no interactive stdin). An interactive prompt would
        hang every agent-driven stop/restart. Requiring an explicit name
        is equally safe and works for both human + agent uniformly.
    """
    if getattr(args, "name", None):
        return args.name
    names = _state.list_daemons(state_dir=args.state_dir)
    if not names:
        sys.stderr.write(
            "senpi-helpers: no daemons found in state dir "
            f"({_state.get_state_dir(args.state_dir)}).\n"
        )
        return None
    sys.stderr.write(
        f"senpi-helpers: '{action}' requires an explicit <name>. "
        f"Auto-resolution is disabled for destructive commands.\n"
        f"  Available: {', '.join(names)}\n"
        f"  See: `senpi-helpers list` for details before choosing.\n"
    )
    return None


# Backward-compat alias for any code that imported `_resolve_name`. The
# new code should pick the correct variant. We keep this pointing at the
# READ-ONLY variant to preserve old call-site behavior for non-destructive
# subcommands; destructive commands are switched explicitly below.
_resolve_name = _resolve_name_readonly


def _collect_daemon_row(name: str, state_dir: Optional[str]) -> Dict[str, Any]:
    """Build a single row for `list` — combines pid.json + heartbeat.json."""
    pid_data = _state.read_pid(name, state_dir=state_dir) or {}
    hb_data = _state.read_heartbeat(name, state_dir=state_dir) or {}
    pid = pid_data.get("pid")
    # `running` consults the pid-recycle guard: True only if the pid exists
    # AND its cmdline/start-time fingerprints match what write_pid recorded.
    # On schema-1 pid.json or non-Linux hosts, degrades to plain pid_alive.
    return {
        "name": name,
        "pid": pid if isinstance(pid, int) else None,
        "running": _pid_alive_for_daemon(pid_data) if pid_data else False,
        "wallet": pid_data.get("wallet"),
        "scanner": pid_data.get("scanner"),
        "interval_seconds": pid_data.get("interval_seconds"),
        "start_time_iso": pid_data.get("start_time_iso"),
        "log_path": pid_data.get("log_path"),
        "last_tick_iso": hb_data.get("last_tick_iso"),
        "last_tick_status": hb_data.get("last_tick_status"),
        "last_tick_code": hb_data.get("last_tick_code"),
        "tick_count": hb_data.get("tick_count", 0),
        "error_count": hb_data.get("error_count", 0),
    }


# ─── Subcommand: list ───────────────────────────────────────────────────────


def _truncate(value: Optional[str], width: int) -> str:
    s = "-" if value is None else str(value)
    return s if len(s) <= width else s[: width - 1] + "…"


def _print_list_table(rows: List[Dict[str, Any]], state_dir_resolved: str) -> None:
    """Aligned-column text output for human operators."""
    if not rows:
        print(f"No daemons registered (looked in: {state_dir_resolved}).")
        print("Start one by running its producer script — the daemon writes its")
        print("state files on boot. See: senpi-trading-runtime/SKILL.md.")
        return

    cols = ("NAME", "PID", "RUNNING", "WALLET", "SCANNER", "TICKS", "ERRORS", "LAST_TICK")
    # Wallet shown as 0xabc…1234 (head + tail). Lets operators eyeball whose
    # daemon this is without consuming column width for full 42-char addrs.
    fmt_rows: List[List[str]] = []
    for r in rows:
        wallet = r["wallet"] or ""
        if len(wallet) > 14:
            wallet_short = f"{wallet[:6]}…{wallet[-4:]}"
        else:
            wallet_short = wallet or "-"
        fmt_rows.append([
            _truncate(r["name"], 32),
            str(r["pid"]) if r["pid"] is not None else "-",
            "yes" if r["running"] else "no",
            wallet_short,
            _truncate(r["scanner"], 24),
            str(r.get("tick_count") or 0),
            str(r.get("error_count") or 0),
            _truncate(r["last_tick_iso"], 24),
        ])

    widths = [len(c) for c in cols]
    for row in fmt_rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    print(fmt.format(*cols))
    for row in fmt_rows:
        print(fmt.format(*row))


def cmd_list(args: argparse.Namespace) -> int:
    state_dir = args.state_dir
    names = _state.list_daemons(state_dir=state_dir)
    rows = [_collect_daemon_row(n, state_dir) for n in names]
    if args.json:
        print(json.dumps({"daemons": rows}, indent=2, default=str))
    else:
        _print_list_table(rows, str(_state.get_state_dir(state_dir)))
    return 0


# ─── Subcommand: health ─────────────────────────────────────────────────────

# Exit codes — documented so scripts can branch:
HEALTH_OK = 0
HEALTH_UNHEALTHY = 1
HEALTH_NOT_FOUND = 2

# Health states surfaced in the payload's `health` field — keep these stable
# so callers (Telegram alerts, dashboards) can pattern-match.
_HEALTH_HEALTHY = "healthy"
_HEALTH_DOWN = "down"
_HEALTH_NO_TICKS = "no_ticks_yet"
_HEALTH_STALE = "stale_ticks"
_HEALTH_LAST_FAILED = "last_tick_failed"


def _compute_health(
    *,
    running: bool,
    last_tick_age: Optional[int],
    last_tick_status: Optional[str],
    interval_seconds: Optional[float],
) -> str:
    """Apply the health-state rules. Pure function — easy to unit-test."""
    if not running:
        return _HEALTH_DOWN
    if last_tick_age is None or last_tick_status is None:
        # No heartbeat yet — daemon started but hasn't completed a tick.
        return _HEALTH_NO_TICKS
    # Stale ticks: more than 2× interval since last tick. The 2× multiplier
    # absorbs one missed cycle (e.g. a slow tick that ran into the next slot)
    # before flagging — single-tick jitter shouldn't trip the alarm.
    if (
        isinstance(interval_seconds, (int, float))
        and interval_seconds > 0
        and last_tick_age > 2 * interval_seconds
    ):
        return _HEALTH_STALE
    # `skipped_locked` means a prior tick was still running when the next
    # one fired — normal overlap, not a failure. Daemon is still healthy.
    if last_tick_status not in ("ok", "skipped_locked"):
        return _HEALTH_LAST_FAILED
    return _HEALTH_HEALTHY


def _build_health_payload(
    name: str,
    pid_data: Optional[Dict[str, Any]],
    hb_data: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    pid_data = pid_data or {}
    hb_data = hb_data or {}
    pid = pid_data.get("pid") if isinstance(pid_data.get("pid"), int) else None
    # Use pid-recycle guard so health doesn't false-positive on a recycled pid.
    running = _pid_alive_for_daemon(pid_data) if pid is not None else False
    interval = pid_data.get("interval_seconds")
    uptime = _age_seconds(pid_data.get("start_time_iso"))
    last_tick_iso = hb_data.get("last_tick_iso")
    last_tick_age = _age_seconds(last_tick_iso)
    last_tick_status = hb_data.get("last_tick_status")
    health = _compute_health(
        running=running,
        last_tick_age=last_tick_age,
        last_tick_status=last_tick_status,
        interval_seconds=interval,
    )
    return {
        "name": name,
        "health": health,
        "running": running,
        "pid": pid,
        "uptime_seconds": uptime,
        "wallet": pid_data.get("wallet"),
        "scanner": pid_data.get("scanner"),
        "interval_seconds": interval,
        "log_path": pid_data.get("log_path"),
        "tick_count": hb_data.get("tick_count", 0),
        "error_count": hb_data.get("error_count", 0),
        "last_tick_iso": last_tick_iso,
        "last_tick_age_seconds": last_tick_age,
        "last_tick_status": last_tick_status,
        "last_tick_code": hb_data.get("last_tick_code"),
        "last_tick_error": hb_data.get("last_tick_error"),
        "last_tick_duration_ms": hb_data.get("last_tick_duration_ms"),
    }


def _print_health_summary(payload: Dict[str, Any]) -> None:
    """Operator-readable single-block summary."""
    name = payload["name"]
    health = payload["health"]
    running = payload["running"]
    pid = payload["pid"]
    uptime = _format_duration(payload["uptime_seconds"])
    interval = payload["interval_seconds"]
    interval_str = f"{int(interval)}s" if isinstance(interval, (int, float)) else "-"
    last_tick = payload["last_tick_iso"] or "-"
    last_age = _format_duration(payload["last_tick_age_seconds"])
    last_status = payload["last_tick_status"] or "-"
    last_code = payload["last_tick_code"]

    print(f"name:           {name}")
    print(f"health:         {health}")
    if running and pid is not None:
        print(f"running:        yes (pid {pid}, uptime {uptime})")
    elif pid is not None:
        print(f"running:        no (last known pid {pid})")
    else:
        print("running:        no (no pid recorded)")
    print(f"wallet:         {_shorten_wallet(payload['wallet'])}")
    print(f"scanner:        {payload['scanner'] or '-'}")
    print(f"interval:       {interval_str}")
    tail = f"status={last_status}"
    if last_code:
        tail += f", code={last_code}"
    if payload["last_tick_age_seconds"] is not None:
        tail += f", {last_age} ago"
    print(f"last tick:      {last_tick} ({tail})")
    print(f"ticks total:    {payload['tick_count']} (errors: {payload['error_count']})")
    if payload["log_path"]:
        print(f"log path:       {payload['log_path']}")
    if payload["last_tick_error"]:
        # Trim long stack traces — operators reach for `logs` for the full message.
        err = str(payload["last_tick_error"])
        print(f"last error:     {err[:200]}{'…' if len(err) > 200 else ''}")


# ─── Subcommand: stats ──────────────────────────────────────────────────────

STATS_OK = 0
STATS_NO_LOG = 1
STATS_NOT_FOUND = 2


def _print_stats_summary(payload: Dict[str, Any], name: str) -> None:
    """Operator-readable totals + hourly bucket table.

    Format:
      Header: name, window, log path, events parsed
      Totals: MCP / Signals / Cache / Ticks
      Errors by code (if any)
      Hourly buckets (oldest first), one row per UTC hour
    """
    totals = payload["totals"]
    window = payload["window_hours"]
    print(f"{name} — last {window} hour{'s' if window != 1 else ''}")
    print(f"log: {payload['log_path']}")
    earliest = payload.get("earliest_event_iso") or "-"
    size = payload.get("log_size_bytes")
    size_str = f"{size:,} bytes" if isinstance(size, int) else "-"
    print(f"events parsed: {payload['total_events_counted']}  "
          f"(earliest: {earliest}, log size: {size_str})")
    print()

    print("Totals")
    print(f"  MCP calls         {totals['mcp_calls_ok']:>6} ok  "
          f"{totals['mcp_calls_failed']:>6} failed")
    print(f"  Signals posted    {totals['signals_posted_ok']:>6} ok  "
          f"{totals['signals_posted_failed']:>6} failed")
    print(f"  Cache hits        {totals['cache_hits']:>6}")
    t = totals["ticks_by_status"]
    print(f"  Ticks             {t.get('ok', 0):>6} ok  "
          f"{t.get('error', 0):>6} error  "
          f"{t.get('timeout', 0):>6} timeout  "
          f"{t.get('skipped_locked', 0):>6} skipped")
    print()

    # Errors by code — only show if non-empty.
    any_errors = (
        totals["mcp_errors_by_code"]
        or totals["signals_errors_by_code"]
        or totals["ticks_errors_by_code"]
    )
    if any_errors:
        print(f"Errors by code (last {window}h)")
        for source, codes in (
            ("mcp_call", totals["mcp_errors_by_code"]),
            ("signal_post", totals["signals_errors_by_code"]),
            ("tick", totals["ticks_errors_by_code"]),
        ):
            for code in sorted(codes.keys()):
                print(f"  {source:<12}  {code:<24}  {codes[code]}")
        print()

    # Hourly breakdown — oldest first; matches how operators scroll through.
    buckets = payload["buckets"]
    if not buckets:
        return
    print(f"Hourly breakdown ({len(buckets)} buckets, oldest first)")
    print(f"  {'HOUR (UTC)':<22}  {'MCP':>5}  {'SIG':>5}  {'CACHE':>5}  "
          f"{'T_OK':>5}  {'T_ERR':>6}  {'T_TO':>5}  {'T_SKIP':>6}")
    for b in buckets:
        ts = b["hour_start_iso"][:16].replace("T", " ")
        t = b["ticks_by_status"]
        print(f"  {ts:<22}  "
              f"{b['mcp_calls_ok']:>5}  "
              f"{b['signals_posted_ok']:>5}  "
              f"{b['cache_hits']:>5}  "
              f"{t.get('ok', 0):>5}  "
              f"{t.get('error', 0):>6}  "
              f"{t.get('timeout', 0):>5}  "
              f"{t.get('skipped_locked', 0):>6}")


def cmd_stats(args: argparse.Namespace) -> int:
    name = _resolve_name(args)
    if name is None:
        return STATS_NOT_FOUND

    # Stats can run on a cleanly-stopped daemon: pid.json may be gone, but
    # boot.json (which persists) records log_path under schema 2+. Try both.
    pid_data = _state.read_pid(name, state_dir=args.state_dir)
    boot_data = _state.read_boot(name, state_dir=args.state_dir)

    log_path = (pid_data or {}).get("log_path")
    if not log_path:
        log_path = (boot_data or {}).get("log_path")
    if not log_path and boot_data:
        env_snapshot = boot_data.get("env_snapshot") or {}
        log_path = env_snapshot.get("SENPI_HELPERS_LOG_PATH")

    if not log_path:
        if pid_data is None and boot_data is None:
            sys.stderr.write(
                f"senpi-helpers: no state files for '{name}'. "
                f"Try `senpi-helpers list` to see registered daemons.\n"
            )
            return STATS_NOT_FOUND
        sys.stderr.write(
            f"senpi-helpers: daemon '{name}' did not record a log path. "
            f"Stats parses from the daemon's stderr log file; without a "
            f"redirect, no log exists to parse.\n"
            f"Fix by starting the daemon with stderr redirected — e.g. "
            f"`nohup python3 -u <producer>.py > /tmp/{name}.log 2>&1 &` — "
            f"or set SENPI_HELPERS_LOG_PATH=/path/to/log before launch.\n"
        )
        return STATS_NO_LOG

    try:
        payload = _stats.aggregate_log_file(log_path, window_hours=args.hours)
    except FileNotFoundError:
        sys.stderr.write(
            f"senpi-helpers: log file not found at {log_path}.\n"
            f"The daemon's pid.json points here; the file may have been "
            f"rotated away or deleted.\n"
        )
        return STATS_NO_LOG
    except OSError as e:
        sys.stderr.write(f"senpi-helpers: cannot read {log_path}: {e}\n")
        return STATS_NO_LOG

    # Attach the daemon name so the human renderer can title the output and
    # JSON consumers can correlate without a second lookup.
    payload["name"] = name

    if args.json:
        print(json.dumps(payload, indent=2, default=str))
    else:
        _print_stats_summary(payload, name)
    return STATS_OK


# ─── Subcommand: stop ───────────────────────────────────────────────────────

STOP_OK = 0
STOP_FAILED = 1
STOP_NOT_FOUND = 2


def _print_stop_summary(name: str, pid: int, result: Dict[str, Any]) -> None:
    """Human-readable stop result."""
    outcome = result["outcome"]
    elapsed = result["elapsed_seconds"]
    if outcome == _manage.STOP_ALREADY_DEAD:
        print(f"{name}: already stopped (pid {pid} not alive).")
    elif outcome == _manage.STOP_TERM_OK:
        print(f"{name}: stopped cleanly via SIGTERM (pid {pid}, {elapsed:.1f}s).")
    elif outcome == _manage.STOP_KILL_OK:
        print(f"{name}: SIGTERM timed out — escalated to SIGKILL (pid {pid}, {elapsed:.1f}s).")
    elif outcome == _manage.STOP_PERMISSION_DENIED:
        print(f"{name}: permission denied signalling pid {pid} ({result.get('error')}).")
    elif outcome == _manage.STOP_KILL_FAILED:
        print(f"{name}: pid {pid} still alive after SIGKILL — kernel-level issue.")
        print(f"  ({result.get('error')})")
    elif outcome == _manage.STOP_INVALID_PID:
        print(f"{name}: cannot stop — {result.get('error')}.")
    else:
        print(f"{name}: unexpected outcome '{outcome}'.")


def cmd_stop(args: argparse.Namespace) -> int:
    # Destructive: require an explicit <name>. See `_resolve_name_explicit`
    # for why we don't auto-resolve on single-daemon hosts.
    name = _resolve_name_explicit(args, action="stop")
    if name is None:
        return STOP_NOT_FOUND

    pid_data = _state.read_pid(name, state_dir=args.state_dir)
    if pid_data is None:
        sys.stderr.write(
            f"senpi-helpers: no pid.json for '{name}'. "
            f"Daemon may have exited cleanly already, or never started.\n"
        )
        return STOP_NOT_FOUND

    pid = pid_data.get("pid")
    if not isinstance(pid, int) or pid <= 0:
        sys.stderr.write(
            f"senpi-helpers: pid.json for '{name}' has invalid pid: {pid!r}.\n"
        )
        return STOP_NOT_FOUND

    # Pid-recycle guard: if pid_data has fingerprints (schema 2) but
    # /proc/<pid>'s cmdline or start_time doesn't match, the kernel has
    # given our daemon's old pid to an unrelated process. Refuse to signal
    # — treat as already-dead — and clear the stale pid.json.
    if _state.pid_alive(pid) and not _pid_alive_for_daemon(pid_data):
        sys.stderr.write(
            f"senpi-helpers: pid {pid} is alive but its cmdline / start_time "
            f"doesn't match what '{name}' recorded — likely a pid recycle. "
            f"Refusing to SIGTERM an unrelated process. Clearing stale pid.json.\n"
        )
        _state.clear_pid(name, state_dir=args.state_dir)
        result = {
            "outcome": _manage.STOP_ALREADY_DEAD,
            "elapsed_seconds": 0.0,
            "sigterm_sent": False, "sigkill_sent": False,
            "error": "pid recycled to unrelated process",
        }
        if args.json:
            payload = {"name": name, "pid": pid, **result}
            print(json.dumps(payload, indent=2, default=str))
        else:
            print(f"{name}: pid {pid} recycled — daemon already gone.")
        return STOP_OK

    result = _manage.stop_pid(pid, timeout_seconds=args.timeout)

    # SIGKILL'd daemons can't run their own clear_pid, so the CLI cleans up.
    # On clean SIGTERM the daemon's finally-block already cleared the file;
    # our call is then a no-op (clear_pid is idempotent on FileNotFoundError).
    if result["outcome"] in (_manage.STOP_KILL_OK, _manage.STOP_ALREADY_DEAD):
        _state.clear_pid(name, state_dir=args.state_dir)

    if args.json:
        payload = {"name": name, "pid": pid, **result}
        print(json.dumps(payload, indent=2, default=str))
    else:
        _print_stop_summary(name, pid, result)

    return STOP_OK if _manage.stop_outcome_was_success(result["outcome"]) else STOP_FAILED


# ─── Subcommand: restart ────────────────────────────────────────────────────

RESTART_OK = 0
RESTART_FAILED = 1
RESTART_NOT_FOUND = 2

# How long to wait for the new daemon to write its pid.json before declaring
# the relaunch a partial failure. The daemon writes pid.json near the top of
# `producer_daemon` (right after argument validation and the daemon_started
# log), so 3 s is generous on healthy hardware.
_RELAUNCH_CONFIRM_TIMEOUT = 3.0
_RELAUNCH_CONFIRM_INTERVAL = 0.1


def _wait_for_new_pid_json(
    name: str,
    *,
    state_dir: Optional[str],
    expected_pid: int,
    timeout: float,
) -> Optional[Dict[str, Any]]:
    """Poll for a fresh pid.json whose pid matches `expected_pid`.

    Returns the new pid.json data dict, or None on timeout. Used by
    `restart` to confirm the new daemon got far enough into its boot
    sequence to write its state file.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        data = _state.read_pid(name, state_dir=state_dir)
        if data is not None and data.get("pid") == expected_pid:
            return data
        time.sleep(_RELAUNCH_CONFIRM_INTERVAL)
    return None


def _pid_alive_for_daemon(pid_data: Optional[Dict[str, Any]]) -> bool:
    """Liveness check that consults pid_data's fingerprints when available.

    Use this everywhere the CLI asks "is my daemon's pid alive?" — `stop`,
    `restart`, `start`, `health`, `list`. It defends against pid recycling
    by cross-checking /proc/<pid>/cmdline and /proc/<pid>/stat against the
    fingerprints `write_pid` recorded.
    """
    if not pid_data:
        return False
    pid = pid_data.get("pid")
    if not isinstance(pid, int):
        return False
    return _state.pid_alive_and_matches(
        pid,
        expected_fingerprint=pid_data.get("cmdline_fingerprint"),
        expected_jiffies=pid_data.get("start_time_jiffies"),
    )


def _resolve_log_path_for_relaunch(
    name: str,
    *,
    pid_data: Optional[Dict[str, Any]],
    boot_data: Dict[str, Any],
    warn_when_defaulting: bool = True,
) -> str:
    """Pick the log path to point the relaunched daemon's stderr at.

    Tried in order:
      1. pid.json's log_path — most recent observed value.
      2. boot.json's log_path (schema 2+) — survives clean stops.
      3. boot.json env_snapshot's SENPI_HELPERS_LOG_PATH — operator override.
      4. Deterministic default /tmp/<name>.log — with a stderr warning.

    Extracted so `cmd_restart` and `cmd_start` share identical resolution
    semantics. Each daemon's first successful relaunch under schema-2
    boot.json will persist its log_path, so the default is one-shot.
    """
    log_path = (pid_data or {}).get("log_path")
    if log_path:
        return log_path
    log_path = boot_data.get("log_path")
    if log_path:
        return log_path
    env_snapshot = boot_data.get("env_snapshot") or {}
    log_path = env_snapshot.get("SENPI_HELPERS_LOG_PATH")
    if log_path:
        return log_path
    default_path = f"/tmp/{name}.log"
    if warn_when_defaulting:
        sys.stderr.write(
            f"senpi-helpers: no log_path recorded in boot.json or pid.json; "
            f"defaulting to {default_path}. (Set SENPI_HELPERS_LOG_PATH before "
            f"launch, or rely on the new daemon's write_boot to persist the "
            f"resolved path so future restarts use it.)\n"
        )
    return default_path


# ─── Subcommand: start ──────────────────────────────────────────────────────
#
# `start` is the missing peer of `stop`/`restart`. Without it, operators had
# to hand-type the `nohup python3 -u … > /tmp/<name>.log 2>&1 &` playbook
# every time — which is what introduced the schema-1 boot.json gap we fixed
# earlier in this branch. With `start`, the daemon's argv + log path come
# from boot.json (auto-migrated if schema 1), the operator types one command.

START_OK = 0
START_FAILED = 1
START_NOT_FOUND = 2


def cmd_start(args: argparse.Namespace) -> int:
    # Destructive (spawns a process): require an explicit <name>.
    name = _resolve_name_explicit(args, action="start")
    if name is None:
        return START_NOT_FOUND

    boot_data = _state.read_boot(name, state_dir=args.state_dir)
    if boot_data is None:
        sys.stderr.write(
            f"senpi-helpers: cannot start '{name}': boot.json is missing.\n"
            f"The daemon has never started under the helper, so there's no "
            f"record of how to launch it. Use your skill's launch recipe one "
            f"time (`nohup python3 -u <producer>.py > /tmp/{name}.log 2>&1 &` "
            f"plus the skill's env vars); `start` will work from the next "
            f"launch onward.\n"
        )
        return START_NOT_FOUND

    script_path = boot_data.get("script_path")
    argv = boot_data.get("argv") or []
    cwd = boot_data.get("cwd")

    if not script_path or not os.path.exists(script_path):
        sys.stderr.write(
            f"senpi-helpers: cannot start '{name}': script_path "
            f"'{script_path}' no longer exists on disk.\n"
        )
        return START_FAILED

    # Idempotent: if the daemon is already alive (and the pid hasn't been
    # recycled to an unrelated process), do nothing. `_pid_alive_for_daemon`
    # cross-checks cmdline + start_time fingerprints from pid.json.
    pid_data = _state.read_pid(name, state_dir=args.state_dir)
    pid = pid_data.get("pid") if pid_data else None
    if isinstance(pid, int) and _pid_alive_for_daemon(pid_data):
        if args.json:
            print(json.dumps({
                "name": name, "outcome": "already_running", "pid": pid,
            }, indent=2, default=str))
        else:
            print(f"{name}: already running (pid {pid}). No action taken.")
        return START_OK

    log_path = _resolve_log_path_for_relaunch(
        name, pid_data=pid_data, boot_data=boot_data,
    )

    relaunch_result = _manage.relaunch_daemon(
        argv=argv,
        cwd=cwd,
        log_path=log_path,
    )

    if relaunch_result["outcome"] != _manage.RELAUNCH_OK:
        sys.stderr.write(
            f"senpi-helpers: start failed: {relaunch_result['outcome']} "
            f"({relaunch_result.get('error')})\n"
        )
        if args.json:
            print(json.dumps({
                "name": name, "outcome": relaunch_result["outcome"],
                "relaunch_result": relaunch_result,
            }, indent=2, default=str))
        return START_FAILED

    new_pid = relaunch_result["pid"]
    argv_normalized = bool(relaunch_result.get("argv_normalized"))
    confirmed = _wait_for_new_pid_json(
        name,
        state_dir=args.state_dir,
        expected_pid=new_pid,
        timeout=_RELAUNCH_CONFIRM_TIMEOUT,
    )

    payload = {
        "name": name,
        "outcome": "started",
        "new_pid": new_pid,
        "relaunch_result": relaunch_result,
        "argv_normalized": argv_normalized,
        "pid_json_confirmed": confirmed is not None,
        "log_path": log_path,
    }

    if args.json:
        print(json.dumps(payload, indent=2, default=str))
    else:
        print(f"{name}: started.")
        print(f"  new pid:        {new_pid}")
        print(f"  log path:       {log_path}")
        print(f"  script:         {script_path}")
        if argv_normalized:
            print(
                "  boot.json:      schema 1 detected; interpreter "
                "auto-prepended (one-time migration)"
            )
        if confirmed is not None:
            print("  pid.json:       confirmed (new daemon is ticking)")
        else:
            print(f"  pid.json:       not seen within {_RELAUNCH_CONFIRM_TIMEOUT}s")
            print(
                f"  → verify with `senpi-helpers health {name}` "
                f"and check the log."
            )

    return START_OK


def cmd_restart(args: argparse.Namespace) -> int:
    # Destructive (kills the old daemon): require an explicit <name>.
    name = _resolve_name_explicit(args, action="restart")
    if name is None:
        return RESTART_NOT_FOUND

    boot_data = _state.read_boot(name, state_dir=args.state_dir)
    if boot_data is None:
        sys.stderr.write(
            f"senpi-helpers: cannot restart '{name}': boot.json is missing.\n"
            f"This daemon has never started successfully under the helper, "
            f"so there's no record of how to launch it. Start it manually "
            f"using your skill's launch recipe (typically "
            f"`nohup python3 -u <producer>.py > /tmp/{name}.log 2>&1 &` "
            f"with the skill's required env vars); `restart` will work "
            f"next time.\n"
        )
        return RESTART_NOT_FOUND

    script_path = boot_data.get("script_path")
    argv = boot_data.get("argv") or []
    cwd = boot_data.get("cwd")

    if not script_path or not os.path.exists(script_path):
        sys.stderr.write(
            f"senpi-helpers: cannot restart '{name}': script_path "
            f"'{script_path}' no longer exists on disk.\n"
            f"The skill may have moved or been removed. Start manually "
            f"from the current location; the new boot.json will fix "
            f"future restarts.\n"
        )
        return RESTART_FAILED

    # If the daemon is running (and the pid hasn't been recycled to a
    # stranger process), stop it first via SIGTERM + escalation.
    # `_pid_alive_for_daemon` guards against recycling using cmdline +
    # start_time fingerprints from pid.json.
    pid_data = _state.read_pid(name, state_dir=args.state_dir)
    pid = pid_data.get("pid") if pid_data else None
    stop_result: Optional[Dict[str, Any]] = None
    if isinstance(pid, int) and _pid_alive_for_daemon(pid_data):
        stop_result = _manage.stop_pid(pid, timeout_seconds=args.timeout)
        if not _manage.stop_outcome_was_success(stop_result["outcome"]):
            sys.stderr.write(
                f"senpi-helpers: cannot restart '{name}': stop failed "
                f"({stop_result['outcome']} — {stop_result.get('error')})\n"
            )
            if args.json:
                print(json.dumps({
                    "name": name, "outcome": "stop_failed",
                    "stop_result": stop_result,
                }, indent=2, default=str))
            return RESTART_FAILED
        # SIGKILL'd daemons can't clean up pid.json themselves.
        if stop_result["outcome"] == _manage.STOP_KILL_OK:
            _state.clear_pid(name, state_dir=args.state_dir)

    # Shared resolution logic — see _resolve_log_path_for_relaunch's docstring
    # for the fallback order. cmd_start uses the same helper.
    log_path = _resolve_log_path_for_relaunch(
        name, pid_data=pid_data, boot_data=boot_data,
    )

    relaunch_result = _manage.relaunch_daemon(
        argv=argv,
        cwd=cwd,
        log_path=log_path,
    )

    if relaunch_result["outcome"] != _manage.RELAUNCH_OK:
        sys.stderr.write(
            f"senpi-helpers: relaunch failed: {relaunch_result['outcome']} "
            f"({relaunch_result.get('error')})\n"
        )
        if args.json:
            print(json.dumps({
                "name": name, "outcome": relaunch_result["outcome"],
                "stop_result": stop_result, "relaunch_result": relaunch_result,
            }, indent=2, default=str))
        return RESTART_FAILED

    new_pid = relaunch_result["pid"]
    argv_normalized = bool(relaunch_result.get("argv_normalized"))

    # Confirm the new daemon actually wrote its pid.json. If not, the script
    # may have crashed before writing — but the Popen succeeded, so it's
    # only a soft warning. The operator can verify with `senpi-helpers list`.
    confirmed = _wait_for_new_pid_json(
        name,
        state_dir=args.state_dir,
        expected_pid=new_pid,
        timeout=_RELAUNCH_CONFIRM_TIMEOUT,
    )

    payload = {
        "name": name,
        "outcome": "restarted",
        "new_pid": new_pid,
        "old_pid": pid if pid_data else None,
        "stop_result": stop_result,
        "relaunch_result": relaunch_result,
        "argv_normalized": argv_normalized,
        "pid_json_confirmed": confirmed is not None,
        "log_path": log_path,
    }

    if args.json:
        print(json.dumps(payload, indent=2, default=str))
    else:
        if pid_data:
            print(f"{name}: stopped old daemon (pid {pid}) and relaunched.")
        else:
            print(f"{name}: no running daemon found; relaunched cold.")
        print(f"  new pid:        {new_pid}")
        print(f"  log path:       {log_path}")
        print(f"  script:         {script_path}")
        if argv_normalized:
            # Legacy schema-1 boot.json was migrated on read. The new daemon
            # will write a fresh schema-2 boot.json on startup; this message
            # only appears for the first restart per daemon after the
            # upgrade lands.
            print(
                "  boot.json:      schema 1 detected; interpreter "
                "auto-prepended (one-time migration)"
            )
        if confirmed is not None:
            print("  pid.json:       confirmed (new daemon is ticking)")
        else:
            print(f"  pid.json:       not seen within {_RELAUNCH_CONFIRM_TIMEOUT}s")
            print(
                f"  → verify with `senpi-helpers health {name}` "
                f"and check the log."
            )

    return RESTART_OK


def cmd_health(args: argparse.Namespace) -> int:
    name = _resolve_name(args)
    if name is None:
        # _resolve_name already printed the explanation to stderr.
        return HEALTH_NOT_FOUND

    pid_data = _state.read_pid(name, state_dir=args.state_dir)
    hb_data = _state.read_heartbeat(name, state_dir=args.state_dir)

    if pid_data is None and hb_data is None:
        sys.stderr.write(
            f"senpi-helpers: no state files for '{name}' in "
            f"{_state.get_state_dir(args.state_dir)}.\n"
            f"Try `senpi-helpers list` to see registered daemons.\n"
        )
        return HEALTH_NOT_FOUND

    payload = _build_health_payload(name, pid_data, hb_data)

    if args.json:
        print(json.dumps(payload, indent=2, default=str))
    else:
        _print_health_summary(payload)

    return HEALTH_OK if payload["health"] == _HEALTH_HEALTHY else HEALTH_UNHEALTHY


# ─── Parser ─────────────────────────────────────────────────────────────────


def _make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="senpi-helpers",
        description=(
            "Operator CLI for senpi_runtime_helpers producer daemons. "
            "Reads the self-describing state files each daemon writes under "
            "$SENPI_HELPERS_STATE_DIR; sends signals to control running daemons."
        ),
    )
    parser.add_argument(
        "--state-dir",
        default=None,
        help=(
            "Override $SENPI_HELPERS_STATE_DIR for this invocation. "
            "Default: /data/.openclaw/senpi-helpers/"
        ),
    )
    sub = parser.add_subparsers(dest="command", metavar="<subcommand>")
    # Some argparse releases don't honor `required=True` directly on
    # `add_subparsers()` — set it explicitly for portability.
    sub.required = True

    # list
    list_p = sub.add_parser(
        "list",
        help="Show all daemons known to this host.",
        description=(
            "List every daemon that has written a state file under the state dir. "
            "Combines pid.json + heartbeat.json into one row per daemon."
        ),
    )
    list_p.add_argument("--json", action="store_true", help="Emit JSON instead of a table.")
    list_p.set_defaults(func=cmd_list)

    # health
    health_p = sub.add_parser(
        "health",
        help="Show health summary for one daemon.",
        description=(
            "Read pid.json + heartbeat.json for a daemon and report its health. "
            "Exit code 0 = healthy, 1 = unhealthy (not running / stale ticks / "
            "last tick failed), 2 = not found. When <name> is omitted the "
            "subcommand uses the only registered daemon (multi-daemon hosts "
            "must pass it explicitly)."
        ),
    )
    health_p.add_argument(
        "name",
        nargs="?",
        default=None,
        help="Daemon name (subdir under state dir). Optional on single-daemon hosts.",
    )
    health_p.add_argument("--json", action="store_true", help="Emit JSON instead of a summary.")
    health_p.set_defaults(func=cmd_health)

    # stats
    stats_p = sub.add_parser(
        "stats",
        help="Aggregate log events into hourly buckets.",
        description=(
            "Parse the daemon's stderr log file and aggregate "
            "[senpi_helpers] events into wall-clock UTC hourly buckets. "
            "Reports MCP call volume, signal posts, cache hits, tick "
            "outcomes, and error histograms by code. Default window: 72 hours."
        ),
    )
    stats_p.add_argument(
        "name",
        nargs="?",
        default=None,
        help="Daemon name (subdir under state dir). Optional on single-daemon hosts.",
    )
    stats_p.add_argument(
        "--hours",
        type=int,
        default=72,
        help="Window in hours (default: 72 = 3 days).",
    )
    stats_p.add_argument("--json", action="store_true", help="Emit JSON instead of a summary.")
    stats_p.set_defaults(func=cmd_stats)

    # start
    start_p = sub.add_parser(
        "start",
        help="Start a daemon from its recorded boot.json (idempotent).",
        description=(
            "Launch the daemon as a detached process using argv + cwd from "
            "boot.json. Idempotent — if a daemon with that name is already "
            "running, `start` reports it and exits 0. If boot.json is missing "
            "(the daemon has never been started under the helper), `start` "
            "errors with the manual launch recipe."
        ),
    )
    start_p.add_argument(
        "name",
        nargs="?",
        default=None,
        help="Daemon name. Optional on single-daemon hosts.",
    )
    start_p.add_argument("--json", action="store_true", help="Emit JSON instead of a summary.")
    start_p.set_defaults(func=cmd_start)

    # stop
    stop_p = sub.add_parser(
        "stop",
        help="Stop a running daemon (SIGTERM, escalate to SIGKILL on timeout).",
        description=(
            "Send SIGTERM to the daemon's pid and poll for exit. If the "
            "daemon does not exit within --timeout seconds, escalate to "
            "SIGKILL. After SIGKILL the CLI clears pid.json (which the "
            "kill-9'd daemon can no longer do itself)."
        ),
    )
    stop_p.add_argument(
        "name",
        nargs="?",
        default=None,
        help="Daemon name. Optional on single-daemon hosts.",
    )
    stop_p.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="Seconds to wait for SIGTERM before escalating (default: 30).",
    )
    stop_p.add_argument("--json", action="store_true", help="Emit JSON instead of a summary.")
    stop_p.set_defaults(func=cmd_stop)

    # restart
    restart_p = sub.add_parser(
        "restart",
        help="Stop a daemon and re-exec it from boot.json.",
        description=(
            "Stop the daemon (SIGTERM/SIGKILL via the same path as `stop`) "
            "and re-launch it from the argv + cwd recorded in boot.json. "
            "The new process inherits the CLI's current env (so wallet / "
            "auth / decision-model changes since the daemon was started "
            "take effect). Logs continue going to the original log_path."
        ),
    )
    restart_p.add_argument(
        "name",
        nargs="?",
        default=None,
        help="Daemon name. Optional on single-daemon hosts.",
    )
    restart_p.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="Seconds to wait for SIGTERM before escalating during stop (default: 30).",
    )
    restart_p.add_argument("--json", action="store_true", help="Emit JSON instead of a summary.")
    restart_p.set_defaults(func=cmd_restart)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = _make_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover — exercised by the wrapper script
    sys.exit(main())
