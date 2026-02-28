import json
import os
import subprocess
import time
from datetime import datetime, timezone
from typing import Any, Dict


VERBOSE = os.environ.get("FORTRESS_VERBOSE") == "1"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_write(path: str, data: Dict[str, Any]) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(data, handle)
    os.replace(tmp, path)


def read_json_safe(path: str) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
            if isinstance(data, dict):
                return data
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return {}


def with_state_meta(payload: Dict[str, Any], instance_key: str, existing: Dict[str, Any] | None = None) -> Dict[str, Any]:
    previous = existing or {}
    created_at = previous.get("createdAt", now_iso())
    output = {
        "version": int(previous.get("version", 1)),
        "active": bool(previous.get("active", True)),
        "instanceKey": instance_key,
        "createdAt": created_at,
        "updatedAt": now_iso(),
    }
    output.update(payload)
    return output


def call_mcp(tool: str, timeout: int = 15, **kwargs: Any) -> Any:
    cmd = ["mcporter", "call", f"senpi.{tool}"]
    for key, value in kwargs.items():
        if isinstance(value, (list, dict, bool)):
            cmd.append(f"{key}={json.dumps(value)}")
        else:
            cmd.append(f"{key}={value}")

    last_error = None
    for attempt in range(3):
        try:
            run = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
            if run.returncode != 0:
                raise RuntimeError(run.stderr.strip() or "mcporter_call_failed")
            data = json.loads(run.stdout)
            if isinstance(data, dict) and data.get("success") is False:
                raise ValueError(data.get("error", "mcp_error"))
            return data
        except Exception as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(3)
    raise RuntimeError(f"mcp_call_failed: {last_error}")


def print_heartbeat() -> None:
    print(json.dumps({"success": True, "heartbeat": "HEARTBEAT_OK"}))


def print_error(error: str) -> None:
    print(json.dumps({"success": False, "error": error, "actionable": False}))


def maybe_debug(output: Dict[str, Any], debug: Dict[str, Any]) -> Dict[str, Any]:
    if VERBOSE:
        output["debug"] = debug
    return output