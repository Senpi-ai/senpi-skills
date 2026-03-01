"""
Append-only trade event journal — canonical audit trail for all Senpi skills.

Inspired by polyscanner's PositionJournal: every trade lifecycle event is a
timestamped, immutable JSONL line.  Current state is derived by replaying
events, eliminating stale-state bugs entirely.

Event types:
    POSITION_OPENED   — order executed on exchange
    POSITION_CLOSED   — position closed (DSL, exit checker, risk, manual)
    DSL_CREATED       — trailing stop state file created
    DSL_TRIGGERED     — DSL closed a position
    DSL_DEACTIVATED   — DSL state set to inactive
    STATE_RECONCILED  — reconciliation pass corrected state
    ERROR             — something went wrong
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class TradeEvent:
    """Single immutable trade lifecycle event."""
    timestamp: float
    event: str
    skill: str
    instance_key: str
    asset: str
    direction: str = ""
    source: str = ""
    reason: str = ""
    leverage: float = 0
    margin: float = 0
    entry_price: float = 0
    exit_price: float = 0
    size: float = 0
    pnl: float = 0
    pattern: str = ""
    score: float = 0
    order_id: str = ""
    details: dict = field(default_factory=dict)


class TradeJournal:
    """Append-only JSONL trade journal.

    Thread-safe.  Each ``record()`` call appends one JSON line.
    State queries replay the full event stream — always fresh, never stale.
    """

    def __init__(self, path: str | Path):
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def record(self, event: TradeEvent) -> None:
        """Append a single event to the journal."""
        with self._lock:
            try:
                with open(self._path, "a") as fp:
                    fp.write(json.dumps(asdict(event)) + "\n")
            except Exception:
                pass

    def record_entry(self, skill: str, instance_key: str, asset: str,
                     direction: str, leverage: float, margin: float,
                     entry_price: float, size: float, pattern: str,
                     score: float = 0, order_id: str = "",
                     source: str = "") -> None:
        self.record(TradeEvent(
            timestamp=time.time(), event="POSITION_OPENED",
            skill=skill, instance_key=instance_key,
            asset=asset, direction=direction, source=source,
            leverage=leverage, margin=margin, entry_price=entry_price,
            size=size, pattern=pattern, score=score, order_id=order_id,
        ))

    def record_exit(self, skill: str, instance_key: str, asset: str,
                    direction: str, reason: str, pnl: float = 0,
                    entry_price: float = 0, exit_price: float = 0,
                    source: str = "") -> None:
        self.record(TradeEvent(
            timestamp=time.time(), event="POSITION_CLOSED",
            skill=skill, instance_key=instance_key,
            asset=asset, direction=direction, source=source,
            reason=reason, pnl=pnl, entry_price=entry_price,
            exit_price=exit_price,
        ))

    def record_dsl(self, skill: str, instance_key: str, asset: str,
                   event_type: str, source: str = "",
                   details: Optional[dict] = None) -> None:
        self.record(TradeEvent(
            timestamp=time.time(), event=event_type,
            skill=skill, instance_key=instance_key,
            asset=asset, source=source, details=details or {},
        ))

    def record_error(self, skill: str, instance_key: str, asset: str,
                     reason: str, source: str = "") -> None:
        self.record(TradeEvent(
            timestamp=time.time(), event="ERROR",
            skill=skill, instance_key=instance_key,
            asset=asset, reason=reason, source=source,
        ))

    def load(self, since_timestamp: float = 0, skill: str = "",
             asset: str = "") -> list[dict]:
        """Read events with optional filters."""
        if not self._path.exists():
            return []
        events = []
        try:
            with open(self._path) as fp:
                for line in fp:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        ev = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if ev.get("timestamp", 0) < since_timestamp:
                        continue
                    if skill and ev.get("skill") != skill:
                        continue
                    if asset and ev.get("asset") != asset:
                        continue
                    events.append(ev)
        except Exception:
            pass
        return events

    def open_positions(self, skill: str = "") -> list[dict]:
        """Return POSITION_OPENED events with no corresponding POSITION_CLOSED."""
        events = self.load(skill=skill)
        opened: dict[str, dict] = {}
        closed: set[str] = set()
        for ev in events:
            key = f"{ev.get('skill')}:{ev.get('instance_key')}:{ev.get('asset')}"
            if ev.get("event") == "POSITION_OPENED":
                opened[key] = ev
            elif ev.get("event") == "POSITION_CLOSED":
                closed.add(key)
        return [v for k, v in opened.items() if k not in closed]

    def summary(self, skill: str = "") -> dict:
        """Replay events to compute aggregate P&L."""
        events = self.load(skill=skill)
        total_entries = 0
        total_exits = 0
        total_pnl = 0.0
        wins = 0
        losses = 0
        for ev in events:
            evt = ev.get("event", "")
            if evt == "POSITION_OPENED":
                total_entries += 1
            elif evt == "POSITION_CLOSED":
                total_exits += 1
                pnl = ev.get("pnl", 0)
                total_pnl += pnl
                if pnl > 0:
                    wins += 1
                elif pnl < 0:
                    losses += 1
        return {
            "total_entries": total_entries,
            "total_exits": total_exits,
            "open_positions": total_entries - total_exits,
            "total_pnl": round(total_pnl, 2),
            "wins": wins,
            "losses": losses,
            "win_rate": round(wins / max(1, wins + losses), 4),
        }
