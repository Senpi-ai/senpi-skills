"""senpi-helpers CLI — operator interface for senpi_runtime_helpers daemons.

Invocation paths (both work the same):
    /data/workspace/skills/_helpers/senpi-helpers <subcommand> [args]
    python3 -m senpi_runtime_helpers.cli <subcommand> [args]

The CLI is a thin reader + signaler over the self-describing state files
written by `producer_daemon` (`pid.json`, `boot.json`, `heartbeat.json`
under `${SENPI_HELPERS_STATE_DIR}/<name>/`). No coupling to the openclaw
gateway: the helper was built to bypass it, the CLI follows suit.

Subcommands ship incrementally — this module is structured so each new
subcommand is one self-contained function + one `subparsers.add_parser`
entry. See `references/cli-reference.md` for full operator documentation.
"""
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
# Source: https://github.com/Senpi-ai/senpi-skills

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from . import state as _state


# ─── Helpers shared across subcommands ──────────────────────────────────────


def _is_pid_alive(pid: Optional[int]) -> bool:
    """Cheap liveness check via signal(0). Mirrors `lock.py._process_alive`.

    Treats EPERM as "alive" — the process exists, we just can't signal it.
    Returns False on None / 0 / negative / type-mismatch.
    """
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


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


def _resolve_name(args: argparse.Namespace) -> Optional[str]:
    """Resolve `<name>` for subcommands that take one daemon.

    Rules:
      - Explicit `<name>` arg always wins.
      - If no daemons registered → return None (caller surfaces "not found").
      - If exactly one daemon registered → use it (single-daemon hosts
        shouldn't pay the cost of always typing the name).
      - If multiple daemons registered → return None and print a list.
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


def _collect_daemon_row(name: str, state_dir: Optional[str]) -> Dict[str, Any]:
    """Build a single row for `list` — combines pid.json + heartbeat.json."""
    pid_data = _state.read_pid(name, state_dir=state_dir) or {}
    hb_data = _state.read_heartbeat(name, state_dir=state_dir) or {}
    pid = pid_data.get("pid")
    return {
        "name": name,
        "pid": pid if isinstance(pid, int) else None,
        "running": _is_pid_alive(pid) if isinstance(pid, int) else False,
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
        print("state files on boot. See: senpi_runtime_helpers/SKILL.md.")
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
    if last_tick_status != "ok":
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
    running = _is_pid_alive(pid) if pid is not None else False
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
    sub.required = True  # py3.10 compat — required=True on add_subparsers isn't honored everywhere

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

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = _make_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover — exercised by the wrapper script
    sys.exit(main())
