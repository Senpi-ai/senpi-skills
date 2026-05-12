"""Log-parsing aggregator for `senpi-helpers stats`.

The daemon emits structured `[senpi_helpers] {json}` events to stderr; the
operator redirects stderr to a file (`nohup python3 -u … 2>&1 > /tmp/foo.log`).
This module parses those events and aggregates them into wall-clock UTC
hourly buckets so operators can answer:

- "How many MCP calls did this daemon make in the last 24h?"
- "Are signals being rejected by the runtime?"
- "Are there error spikes? Which error codes?"
- "Is the cache helping?"

Design choices:

- **Logs are the source of truth.** No separate stats file: the daemon
  already logs every event with all the data the stats command needs. A
  shadow stats file would drift from reality during partial writes or
  crashes; reading from logs means the stats output is always coherent
  with what an operator would see by grepping.
- **Wall-clock UTC hour boundaries** (not rolling windows). Aligns with
  how humans read graphs. The most recent bucket is partial — clearly
  labeled in the human-facing renderer.
- **Streaming line-by-line read.** Log files for a 3-day window on a
  5-min-tick producer are ~20 MB — fast to scan. No mmap, no reverse
  reader; Python's iterator is fast enough.
- **Tolerant parser.** Skip lines that don't start with the helper
  prefix. Skip lines whose JSON doesn't parse. Skip events whose `iso`
  is missing or unparseable. Aggregation must never abort on one bad
  line — operator wants partial data, not failure.

Events recognized (must match what daemon.py / client.py / cache.py emit):

| event                  | bucket field counted                     |
|------------------------|------------------------------------------|
| mcp_call               | mcp_calls_ok / mcp_calls_failed + code   |
| cache_hit              | cache_hits                               |
| signal_post            | signals_posted_ok / signals_posted_failed|
| daemon_tick_finished   | ticks_by_status + ticks_errors_by_code   |
"""
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
# Source: https://github.com/Senpi-ai/senpi-skills

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

_LOG_PREFIX = "[senpi_helpers] "


# ─── Bucket construction ────────────────────────────────────────────────────


def _empty_bucket(hour_start_iso: str) -> Dict[str, Any]:
    """Initial counters for one hour-bucket. Stable schema for the JSON envelope."""
    return {
        "hour_start_iso": hour_start_iso,
        "mcp_calls_ok": 0,
        "mcp_calls_failed": 0,
        "mcp_errors_by_code": {},
        "cache_hits": 0,
        "signals_posted_ok": 0,
        "signals_posted_failed": 0,
        "signals_errors_by_code": {},
        "ticks_by_status": {"ok": 0, "error": 0, "timeout": 0, "skipped_locked": 0},
        "ticks_errors_by_code": {},
    }


def _hour_bucket_iso(dt: datetime) -> str:
    """Floor `dt` to the UTC hour boundary and return its ISO-Z string."""
    floored = dt.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
    return floored.strftime("%Y-%m-%dT%H:00:00Z")


def _parse_event_ts(iso: Optional[str]) -> Optional[datetime]:
    """Parse the daemon's millisecond-precision ISO. Tolerant of legacy
    seconds-only form. Returns tz-aware UTC datetime or None on any failure."""
    if not isinstance(iso, str) or not iso:
        return None
    payload = iso[:-1] if iso.endswith("Z") else iso
    try:
        dt = datetime.fromisoformat(payload)
    except (ValueError, TypeError):
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def _now_utc() -> datetime:
    """Wrapped so tests can monkeypatch a fixed clock."""
    return datetime.now(timezone.utc)


# ─── Per-event accumulation ─────────────────────────────────────────────────


def _bump(d: Dict[str, int], key: str) -> None:
    """Increment by 1 — null/empty keys collapse to UNKNOWN to keep the
    histogram non-leaky. The CLI surfaces UNKNOWN distinctly so operators
    notice the event source needs a `code` field added."""
    k = key if isinstance(key, str) and key else "UNKNOWN"
    d[k] = d.get(k, 0) + 1


def _accumulate_mcp_call(bucket: Dict[str, Any], event: Dict[str, Any]) -> None:
    if event.get("status") == "ok":
        bucket["mcp_calls_ok"] += 1
        return
    bucket["mcp_calls_failed"] += 1
    # Prefer the `code` field if present. `client.py` emits HTTP status as
    # an int (e.g. 503), so accept both int and str — coerce to str for the
    # histogram key. Falls back to the status string when no code is set
    # (`network_error` / `server_error` / `exception`).
    code = event.get("code")
    if isinstance(code, (int, str)) and code != "" and code is not None:
        code_str = str(code)
    else:
        code_str = str(event.get("status") or "UNKNOWN").upper()
    _bump(bucket["mcp_errors_by_code"], code_str)


def _accumulate_signal_post(bucket: Dict[str, Any], event: Dict[str, Any]) -> None:
    if event.get("status") == "ok":
        bucket["signals_posted_ok"] += int(event.get("batch_size", 1) or 1)
        return
    # Failed path: client.py emits `failed_by_code` on per-item rejection,
    # `envelope_code` on HTTPError envelopes, and only `status` on network
    # errors. Add ONE per envelope to keep batch and envelope failures
    # commensurate — operators care about "how many post attempts failed",
    # not "how many items inside a single failed batch".
    bucket["signals_posted_failed"] += 1
    by_code = event.get("failed_by_code")
    envelope_code = event.get("envelope_code")
    status = str(event.get("status") or "UNKNOWN").upper()
    if isinstance(by_code, dict) and by_code:
        # Per-item histogram from runtime. Sum each into the bucket so a
        # batch with N rejections of code X still shows N in the breakdown.
        for code, n in by_code.items():
            try:
                bucket["signals_errors_by_code"][str(code)] = (
                    bucket["signals_errors_by_code"].get(str(code), 0) + int(n)
                )
            except (TypeError, ValueError):
                _bump(bucket["signals_errors_by_code"], str(code))
    elif isinstance(envelope_code, str) and envelope_code:
        _bump(bucket["signals_errors_by_code"], envelope_code)
    else:
        _bump(bucket["signals_errors_by_code"], status)


def _accumulate_tick(bucket: Dict[str, Any], event: Dict[str, Any]) -> None:
    status = event.get("status") or "ok"
    if status not in bucket["ticks_by_status"]:
        bucket["ticks_by_status"][status] = 0
    bucket["ticks_by_status"][status] += 1
    # `skipped_locked` is normal overlap (prior tick still running OR
    # multi-daemon contention) — not an error. Counting it here would
    # surface false `LOCK_BUSY` entries in `errors_by_code` and confuse
    # the operator into thinking the daemon is failing.
    if status not in ("ok", "skipped_locked"):
        code = event.get("code") or event.get("status") or "UNKNOWN"
        _bump(bucket["ticks_errors_by_code"], str(code))


# Dispatch table: event name → accumulator function. New event types add
# one line here. Lookup miss is a silent drop — old logs with retired event
# names shouldn't crash the parser.
_DISPATCH = {
    "mcp_call": _accumulate_mcp_call,
    "cache_hit": lambda b, _e: b.__setitem__("cache_hits", b["cache_hits"] + 1),
    "signal_post": _accumulate_signal_post,
    "daemon_tick_finished": _accumulate_tick,
}


# ─── Public API ─────────────────────────────────────────────────────────────


def parse_log_line(line: str) -> Optional[Dict[str, Any]]:
    """Extract a `[senpi_helpers]` JSON event from one log line.

    Returns None for non-helper lines, malformed JSON, or non-object payloads.
    Public for testability — most callers want `aggregate_log_file`.
    """
    idx = line.find(_LOG_PREFIX)
    if idx < 0:
        return None
    # Allow timestamp prefixes (some loggers prepend their own preamble) by
    # finding `[senpi_helpers] ` anywhere on the line, not just at index 0.
    try:
        parsed = json.loads(line[idx + len(_LOG_PREFIX):])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def aggregate_events(
    events: Iterable[Dict[str, Any]],
    *,
    window_hours: int,
    now: Optional[datetime] = None,
) -> Tuple[List[Dict[str, Any]], int, Optional[datetime]]:
    """Aggregate already-parsed events into hourly UTC buckets.

    Returns (buckets, total_counted, earliest_event_ts).

    Buckets are returned oldest-first and include every hour in the window —
    empty hours appear as zero-filled buckets so the time axis is contiguous.
    """
    now = now or _now_utc()
    cutoff = now - timedelta(hours=window_hours)
    cutoff_hour = cutoff.replace(minute=0, second=0, microsecond=0)
    current_hour = now.replace(minute=0, second=0, microsecond=0)

    # Pre-populate every hour in the window so empty hours show as zeros.
    buckets_by_iso: Dict[str, Dict[str, Any]] = {}
    h = cutoff_hour
    while h <= current_hour:
        buckets_by_iso[_hour_bucket_iso(h)] = _empty_bucket(_hour_bucket_iso(h))
        h += timedelta(hours=1)

    total_counted = 0
    earliest: Optional[datetime] = None
    for event in events:
        event_name = event.get("event")
        if event_name not in _DISPATCH:
            continue
        ts = _parse_event_ts(event.get("iso"))
        if ts is None or ts < cutoff:
            continue
        bucket_key = _hour_bucket_iso(ts)
        bucket = buckets_by_iso.get(bucket_key)
        if bucket is None:
            # Future event (clock skew, multi-host log shipping) — skip
            # rather than fabricate buckets past `now`.
            continue
        _DISPATCH[event_name](bucket, event)
        total_counted += 1
        if earliest is None or ts < earliest:
            earliest = ts

    buckets = [buckets_by_iso[k] for k in sorted(buckets_by_iso.keys())]
    return buckets, total_counted, earliest


def aggregate_log_file(
    log_path: str,
    *,
    window_hours: int,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Aggregate a log file into an envelope ready for JSON serialization.

    Raises FileNotFoundError / OSError if the file can't be read — the CLI
    catches and translates into a user-readable error.
    """
    def _line_iter() -> Iterable[Dict[str, Any]]:
        # Latin-1 decode never fails — and we only care about ASCII anyway
        # for JSON-prefixed lines. UTF-8 errors on noisy log lines were
        # silently truncating real events upstream.
        with open(log_path, "r", encoding="latin-1") as f:
            for line in f:
                event = parse_log_line(line)
                if event is not None:
                    yield event

    buckets, total, earliest = aggregate_events(
        _line_iter(), window_hours=window_hours, now=now,
    )

    # Roll-ups across all buckets — cheaper than re-iterating the file.
    totals: Dict[str, Any] = {
        "mcp_calls_ok": 0,
        "mcp_calls_failed": 0,
        "mcp_errors_by_code": {},
        "cache_hits": 0,
        "signals_posted_ok": 0,
        "signals_posted_failed": 0,
        "signals_errors_by_code": {},
        "ticks_by_status": {"ok": 0, "error": 0, "timeout": 0, "skipped_locked": 0},
        "ticks_errors_by_code": {},
    }
    for b in buckets:
        totals["mcp_calls_ok"] += b["mcp_calls_ok"]
        totals["mcp_calls_failed"] += b["mcp_calls_failed"]
        totals["cache_hits"] += b["cache_hits"]
        totals["signals_posted_ok"] += b["signals_posted_ok"]
        totals["signals_posted_failed"] += b["signals_posted_failed"]
        for code, n in b["mcp_errors_by_code"].items():
            totals["mcp_errors_by_code"][code] = totals["mcp_errors_by_code"].get(code, 0) + n
        for code, n in b["signals_errors_by_code"].items():
            totals["signals_errors_by_code"][code] = totals["signals_errors_by_code"].get(code, 0) + n
        for status, n in b["ticks_by_status"].items():
            totals["ticks_by_status"][status] = totals["ticks_by_status"].get(status, 0) + n
        for code, n in b["ticks_errors_by_code"].items():
            totals["ticks_errors_by_code"][code] = totals["ticks_errors_by_code"].get(code, 0) + n

    return {
        "log_path": log_path,
        "window_hours": window_hours,
        "total_events_counted": total,
        "earliest_event_iso": (
            earliest.strftime("%Y-%m-%dT%H:%M:%S.") + f"{int(earliest.microsecond / 1000):03d}Z"
            if earliest else None
        ),
        "log_size_bytes": _safe_filesize(log_path),
        "totals": totals,
        "buckets": buckets,
    }


def _safe_filesize(path: str) -> Optional[int]:
    try:
        return os.path.getsize(path)
    except OSError:
        return None
