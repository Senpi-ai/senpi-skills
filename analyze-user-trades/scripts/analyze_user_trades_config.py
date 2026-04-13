"""analyze-user-trades — shared config and mcporter helper.

All scripts import from here. No script invokes mcporter directly.
"""
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under Apache-2.0

import json
import os
import shlex
import subprocess
import tempfile
import time
from datetime import datetime, timezone, timedelta

SKILL_NAME = "analyze-user-trades"
SKILL_VERSION = "1.0.0"
VERBOSE = os.environ.get("ANALYZE_USER_TRADES_VERBOSE") == "1"


def mcporter_call(tool, retries=3, timeout=30, **kwargs):
    """Call a Senpi MCP tool via mcporter. Returns the `data` portion of the response.

    Args:
        tool:    Tool name (e.g. "strategy_list", "arena_leaderboard").
        retries: Number of attempts before giving up (default 3).
        timeout: Subprocess timeout in seconds (default 30).
        **kwargs: Tool arguments passed as a single --args JSON blob.

    Returns:
        The `data` dict from the MCP response envelope (envelope already stripped).

    Raises:
        RuntimeError: If all retries fail or the tool returns success=false.
    """
    kwargs.setdefault("skill_name", SKILL_NAME)
    kwargs.setdefault("skill_version", SKILL_VERSION)
    filtered = {k: v for k, v in kwargs.items() if v is not None}

    mcporter_bin = os.environ.get("MCPORTER_CMD", "mcporter")
    cmd = [mcporter_bin, "call", f"senpi.{tool}"]
    if filtered:
        cmd.extend(["--args", json.dumps(filtered)])
    cmd_str = " ".join(shlex.quote(c) for c in cmd)
    last_error = None

    for attempt in range(retries):
        fd, tmp = None, None
        try:
            fd, tmp = tempfile.mkstemp(suffix=".json")
            os.close(fd)
            subprocess.run(
                f"{cmd_str} > {tmp} 2>/dev/null",
                shell=True, timeout=timeout,
            )
            with open(tmp) as f:
                d = json.load(f)
            if d.get("success"):
                return d.get("data", {})
            last_error = d.get("error", d)
        except (json.JSONDecodeError, subprocess.TimeoutExpired, OSError) as e:
            last_error = str(e)
        finally:
            if tmp and os.path.exists(tmp):
                os.unlink(tmp)
        if attempt < retries - 1:
            time.sleep(3)

    raise RuntimeError(f"mcporter {tool} failed after {retries} attempts: {last_error}")


def mcporter_call_safe(tool, retries=3, timeout=30, **kwargs):
    """Like mcporter_call but returns None instead of raising on failure."""
    try:
        return mcporter_call(tool, retries=retries, timeout=timeout, **kwargs)
    except RuntimeError:
        return None


def compute_week_boundaries(week_offset=0, _now=None):
    """Compute arena week start/end given an offset from the current week.

    Week 1 anchor: 2026-03-26T00:00:00Z (Thursday)
    Cycle: Thursday 00:00 UTC → Wednesday 23:59:59 UTC

    Args:
        week_offset: 0 = current week, -1 = last week, +1 = next week, etc.
        _now: Override current time (datetime with tzinfo). For testing only.

    Returns:
        (start_iso, end_iso) tuple of ISO 8601 strings.
    """
    anchor = datetime(2026, 3, 26, 0, 0, 0, tzinfo=timezone.utc)
    now = _now or datetime.now(timezone.utc)
    weeks_since_anchor = (now - anchor).days // 7
    target_week = weeks_since_anchor + week_offset

    start = anchor + timedelta(weeks=target_week)
    end = start + timedelta(days=6, hours=23, minutes=59, seconds=59)

    def to_utc_z(dt):
        # Keep lexical comparisons compatible with API timestamps that use "Z".
        return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    return to_utc_z(start), to_utc_z(end)
