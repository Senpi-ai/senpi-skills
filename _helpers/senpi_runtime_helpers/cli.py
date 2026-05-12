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

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = _make_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover — exercised by the wrapper script
    sys.exit(main())
