"""
Crash-safe JSON read/write primitives.

atomic_write() is the canonical implementation — all skills should use this
instead of rolling their own.  Write to a temp file, then os.replace() for
a POSIX-atomic swap.  Crash mid-write leaves the original intact.
"""

import json
import os


def atomic_write(path, data, indent=None):
    """Write JSON atomically via tmp + os.replace().

    Args:
        path: Target file path.
        data: JSON-serializable object.
        indent: JSON indent level (None = compact, 2 = pretty).
    """
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=indent)
    os.replace(tmp, path)


def load_json(path, default=None):
    """Load JSON from disk with safe fallback.

    Returns ``default`` (or empty dict) if the file is missing or corrupt.
    """
    if default is None:
        default = {}
    if not os.path.exists(path):
        return default
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return default


def deep_merge(base, override):
    """Recursively merge *override* into *base*.  Returns a new dict."""
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result
