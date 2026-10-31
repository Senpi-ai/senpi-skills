"""The deploy request journal (K2): one JSON entry per package under the durable strategies root,
`<strategies_root()>/.deploy-requests/<package id>.json`, holding the request id the last
`senpi.deploy.start` carried and whether that START was accounted for (`resolved`).

`deploy.py` writes it (mint, reuse, resolve on a reply naming a job, `deploy.py status` seeing the
job); `close.py <id>` resolves it when it ends with nothing of the package live
(a close it triggered, or every strategy already closed). Nothing deletes an entry."""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _pkg  # noqa: E402

REQUEST_DIR = ".deploy-requests"
# A package id names a file under the journal dir: only a plain name is ever used as one.
_PLAIN_ID = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._-]*")


def utc_stamp(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def path_for(pkg_id):
    return _pkg.strategies_root() / REQUEST_DIR / f"{pkg_id}.json"


def read(pkg_id):
    if not isinstance(pkg_id, str) or not _PLAIN_ID.fullmatch(pkg_id):
        return None
    try:
        entry = json.loads(path_for(pkg_id).read_text())
    except (OSError, ValueError):
        return None
    return entry if isinstance(entry, dict) else None


def write(pkg_id, entry):
    path = path_for(pkg_id)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(entry, indent=2) + "\n")
        os.replace(tmp, path)
        return True
    except OSError as e:
        print(f"warning: could not write the deploy request journal {path} ({e}). The request id "
              f"still goes on this START, but a later create will not find it.", file=sys.stderr)
        return False


def resolve(pkg_id, rid, outcome):
    """Close `pkg_id`'s entry if it is still open, and (with `rid`) only if it holds that id.
    Returns True when an entry was closed."""
    entry = read(pkg_id)
    if not entry or entry.get("resolved") is not None:
        return False
    if rid is not None and entry.get("requestId") != rid:
        return False
    entry.update(resolved=outcome, resolvedAt=utc_stamp(time.time()))
    return write(pkg_id, entry)
