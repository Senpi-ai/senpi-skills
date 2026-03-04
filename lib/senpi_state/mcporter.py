"""
Unified mcporter CLI wrapper with retry.

Every skill currently reimplements mcporter_call().  This is the canonical
version — three-attempt retry, configurable timeout, MCPORTER_CMD env var
support, and automatic JSON envelope stripping.
"""

import json
import os
import subprocess
import time


def mcporter_call(tool, retries=3, timeout=30, **kwargs):
    """Call a Senpi MCP tool via mcporter with retry.

    Args:
        tool: MCP tool name (without ``senpi.`` prefix — added automatically).
        retries: Number of attempts before giving up.
        timeout: Per-attempt timeout in seconds.
        **kwargs: Tool arguments (key=value).

    Returns:
        Parsed JSON response (envelope stripped — returns ``data`` if present).

    Raises:
        RuntimeError: All attempts failed.
    """
    mcporter_bin = os.environ.get("MCPORTER_CMD", "mcporter")
    cmd = [mcporter_bin, "call", f"senpi.{tool}"]
    for k, v in kwargs.items():
        if isinstance(v, (list, dict)):
            cmd.append(f"{k}={json.dumps(v)}")
        elif isinstance(v, bool):
            cmd.append(f"{'true' if v else 'false'}")
        else:
            cmd.append(f"{k}={v}")

    last_error = None
    for attempt in range(retries):
        try:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                stdout, stderr = proc.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
                raise RuntimeError("timeout")
            if proc.returncode != 0:
                raise RuntimeError(stderr.strip() or f"exit code {proc.returncode}")
            data = json.loads(stdout)
            if isinstance(data, dict) and data.get("success") is False:
                raise ValueError(data.get("error", "unknown"))
            if isinstance(data, dict) and "data" in data:
                return data["data"]
            return data
        except Exception as e:
            last_error = e
            if attempt < retries - 1:
                time.sleep(3)

    raise RuntimeError(f"mcporter {tool} failed after {retries} attempts: {last_error}")


def mcporter_call_safe(tool, retries=3, timeout=30, **kwargs):
    """Like mcporter_call but returns None on failure instead of raising."""
    try:
        return mcporter_call(tool, retries=retries, timeout=timeout, **kwargs)
    except Exception:
        return None
