"""Install ledger — the host-side record that makes deploy idempotent & resumable.

Maps `(strategy_id, instance) -> {wallet, phase, runtime_id, daemon, budget}`.
See senpi-strategy-ops/references/install-ledger.md for the contract.
"""
# Copyright 2026 Senpi (https://senpi.ai) — MIT
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

DEFAULT_PATH = "/data/.openclaw/senpi-install-ledger.json"

# deploy phases, in order
PHASES = ("wallet_ready", "runtime_created", "daemon_launched", "verified")


def ledger_path() -> Path:
    return Path(os.environ.get("SENPI_INSTALL_LEDGER") or DEFAULT_PATH)


def read() -> Dict[str, Any]:
    """Return the whole ledger ({} if missing/unreadable)."""
    p = ledger_path()
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text())
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _write(data: Dict[str, Any]) -> None:
    p = ledger_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, str(p))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def get(strategy_id: str, instance: str) -> Optional[Dict[str, Any]]:
    """Return the ledger entry for (strategy_id, instance), or None."""
    return read().get(strategy_id, {}).get(instance)


def write_entry(strategy_id: str, instance: str, **fields: Any) -> Dict[str, Any]:
    """Merge `fields` into the (strategy_id, instance) entry and persist.
    BEST-EFFORT: the ledger is install-time-only; a write failure must NEVER break
    the caller (nothing depends on the ledger persisting)."""
    try:
        data = read()
        entry = data.setdefault(strategy_id, {}).setdefault(instance, {})
        entry.update({k: v for k, v in fields.items() if v is not None})
        _write(data)
        return entry
    except Exception:
        return {}


def remove_entry(strategy_id: str, instance: Optional[str] = None) -> None:
    """Remove one instance entry, or the whole strategy if instance is None.
    BEST-EFFORT — never raises."""
    try:
        data = read()
        if strategy_id not in data:
            return
        if instance is None:
            data.pop(strategy_id, None)
        else:
            data[strategy_id].pop(instance, None)
            if not data[strategy_id]:
                data.pop(strategy_id, None)
        _write(data)
    except Exception:
        pass
