"""Catalog guard: an external scanner's `timeout_seconds` must be BELOW its `interval_seconds`.

WHY THIS EXISTS (2026-09-08). When a tick reaches `timeout_seconds` the scaffold raises SIGALRM
(`_TickTimeout`, a BaseException) on the main thread. Any MCP request in flight lives on a background
daemon loop and is never cancelled, and the client's `_connected` flag is a one-way latch — so from
that tick on **every** `call_tool` raises `ClosedResourceError` for the life of the process. The tick
still reports `status: "ok"` with `duration_ms` ≈ 0, so no error-based health check can see it. Only a
fresh process clears it.

That makes `timeout_seconds` a live hazard, not a safety net: the closer a scanner's real tick duration
sits to its timeout, the sooner it wedges. `timeout >= interval` is the worst case — a scanner given a
budget it is expected to consume, with no room for one slow read.

MEASURED, 24 h to 2026-09-08 (fleet telemetry, `max(duration_ms)` per run vs the configured timeout):

    barracuda pump_signals      60/90    worst 95,046 ms = 105.6% of budget   <- exceeded, 2 users
    barracuda close_all_scanner 30/30    worst 30,000 ms = 100.0% of budget   <- wedged M417101, 4 users
    jackal    jackal_main_signals 60/120 worst 120,000 ms = 100.0% of budget  <- 4 users
    vulture   60/120  30.8%   swift 60/120  26.0%   orca 90/120  16.9%   roach 90/180  5.7%

M417101's `close_all_scanner` ran 3,231 clean ticks over 17.4 h, then one tick hit **exactly 30,000 ms**
and the next 9.5 h produced 2,284 `ClosedResourceError` with no recovery. The user closed the $180
strategy 27 minutes after a restart had already fixed it.

Run:
  python3 -m pytest strategies/tests/test_scanner_timeout_headroom.py -q
"""
import pathlib
import re

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]

# Scanners that still carry `timeout >= interval` and are NOT retuned here, each with the reason.
# Adding a package to this list requires the measured evidence that justifies it. New packages must
# not appear here at all — the point of the guard is that the smell cannot be introduced silently.
KNOWN_UNTUNED = {
    # avg tick 1.5 s but max pinned at exactly 120,000 ms across 4 users: that is a HUNG MCP read,
    # not a slow scan. Raising or lowering the timeout does not fix a call that never returns — the
    # upstream client fix (cancel the abandoned future, reset the latch) is the remedy. Left alone
    # deliberately rather than retuned on a number that is not the cause.
    ("jackal", "main", "jackal_main_signals"),
    # 5–31% of budget in 24 h of telemetry: real duration headroom, so the timeout is not the live
    # hazard for these. They violate the ordering rule only, and are queued for a cadence review.
    ("vulture", "main", "vulture_main_signals"),
    ("swift", "main", "swift_scanner"),
    ("orca", "main", "orca_main_signals"),
    ("roach", "main", "roach_main_signals"),
    # no telemetry in the 24 h window (no live deployment to measure), so there is no evidence to
    # size a new cadence from. Reviewed when a user deploys one.
    ("ant", "main", "ant_scanner"),
    ("mantis", "main", "mantis_main_signals"),
    ("oryx", "main", "oryx_scanner"),
}


def _external_scanner_cadences():
    """(package, instance, scanner, interval_seconds, timeout_seconds) for every external scanner."""
    out = []
    for f in sorted(_ROOT.glob("*/*/runtime.yaml")):
        pkg, inst = f.parts[-3], f.parts[-2]
        cur = ivl = tmo = None
        is_external = False

        def flush():
            if cur and is_external and ivl is not None and tmo is not None:
                out.append((pkg, inst, cur, ivl, tmo))

        for line in f.read_text(encoding="utf-8").splitlines():
            m = re.match(r"\s*-\s*name:\s*(\S+)", line)
            if m:
                flush()
                cur, ivl, tmo, is_external = m.group(1), None, None, False
            if re.match(r"\s*type:\s*external_scanner", line):
                is_external = True
            mi = re.match(r"\s*interval_seconds:\s*(\d+)", line)
            if mi:
                ivl = int(mi.group(1))
            mt = re.match(r"\s*timeout_seconds:\s*(\d+)", line)
            if mt:
                tmo = int(mt.group(1))
        flush()
    return out


_CADENCES = _external_scanner_cadences()


def test_the_scan_found_the_expected_number_of_external_scanners():
    """Guard the guard: a parser that silently matches nothing would make every test below vacuous."""
    assert len(_CADENCES) > 100, f"only parsed {len(_CADENCES)} external scanners — parser broken?"


@pytest.mark.parametrize("pkg,inst,scanner,ivl,tmo", _CADENCES)
def test_timeout_is_below_interval(pkg, inst, scanner, ivl, tmo):
    """A tick must be cut off before its next one is due, with room for one slow read.

    Reaching `timeout_seconds` poisons the MCP session for the life of the process, so a budget the
    scanner is expected to consume is a scheduled outage.
    """
    if (pkg, inst, scanner) in KNOWN_UNTUNED:
        pytest.skip(f"{pkg}/{inst}/{scanner} is a documented untuned cadence — see KNOWN_UNTUNED")
    assert tmo < ivl, (
        f"{pkg}/{inst}/{scanner}: timeout_seconds={tmo} >= interval_seconds={ivl}. "
        "A tick that reaches its timeout wedges the MCP session permanently; give it a budget it is "
        "not expected to consume, below the interval."
    )


def test_known_untuned_list_has_no_stale_entries():
    """An entry that has been fixed must leave the list, or the guard rots into an allowlist."""
    actual = {(p, i, s) for p, i, s, ivl, tmo in _CADENCES if tmo >= ivl}
    stale = sorted(KNOWN_UNTUNED - actual)
    assert not stale, f"these now comply and must be removed from KNOWN_UNTUNED: {stale}"


def test_barracuda_is_retuned_and_not_exempt():
    """The two cadences this change fixes, pinned so a future edit cannot quietly regress them."""
    got = {s: (ivl, tmo) for p, i, s, ivl, tmo in _CADENCES if p == "barracuda"}
    assert "pump_signals" in got and "close_all_scanner" in got, got
    for scanner in ("pump_signals", "close_all_scanner"):
        assert ("barracuda", "main", scanner) not in KNOWN_UNTUNED
        ivl, tmo = got[scanner]
        assert tmo < ivl, f"barracuda {scanner}: timeout {tmo} must stay below interval {ivl}"
    # pump_signals was killed at 90 s while genuinely needing up to 95 s; its budget must exceed that.
    assert got["pump_signals"][1] >= 120, got["pump_signals"]
    # close_all's p90 worst tick was 25.1 s; 30 s left no room at all.
    assert got["close_all_scanner"][1] >= 45, got["close_all_scanner"]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
