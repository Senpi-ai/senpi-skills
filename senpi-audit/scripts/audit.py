#!/usr/bin/env python3
"""senpi-audit engine — recent activity / strategy history / failure investigation (hidden).

The agent (LLM) runs this via the OpenClaw `exec` tool, reads the JSON on stdout, and NARRATES a
"what happened" answer (see SKILL.md). The script pulls the audit trail and normalizes it; the LLM
turns it into a readable activity summary.

  python3 audit.py                       # recent activity across the account
  python3 audit.py --strategy <id>       # full mutation history for one strategy
  python3 audit.py --failures            # only failed operations (debugging)
  python3 audit.py --tool <name>         # filter to one tool
  python3 audit.py --fixture f.json      # offline (tests)   |   --dry  (raw dump)

⚠ USER-scoped SENPI_AUTH_TOKEN (defaults to the authenticated user's audit log).
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _ok(resp):
    if isinstance(resp, dict):
        if resp.get("success") is False and "data" not in resp:
            return None
        return resp.get("data", resp)
    return resp


def _field(d, *names, default=None):
    if isinstance(d, dict):
        for n in names:
            if n in d and d[n] is not None:
                return d[n]
    return default


def _rows(data, *keys):
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for k in keys + ("entries", "actions", "logs", "data", "results"):
            v = data.get(k)
            if isinstance(v, list):
                return v
    return []


def _get_client():
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    from _mcp import MCPClient
    return MCPClient()


class _FixtureClient:
    def __init__(self, recorded):
        self._r = recorded

    def mcp_call(self, tool, timeout=12, **kw):
        for disc in (kw.get("strategy_id"), kw.get("tool_name"),
                     ("failures" if kw.get("success") is False else None)):
            if disc:
                k = f"{tool}::{str(disc).lower()}"
                if k in self._r:
                    return self._r[k]
        return self._r.get(tool)


def _entry(e):
    return {
        "time": _field(e, "timestamp", "createdAt", "created_at", "time"),
        "action_type": _field(e, "actionType", "action_type", "action"),
        "tool": _field(e, "toolName", "tool_name", "tool"),
        "success": _field(e, "success", default=None),
        "resource": _field(e, "resourceType", "resource_type") or _field(e, "resourceId", "resource_id"),
        "reason": _field(e, "aiReasoning", "ai_reasoning", "reason", "reasoning"),
    }


def fetch(client, meta, mode, strategy_id=None, tool_name=None):
    try:
        if mode == "strategy":
            resp = client.mcp_call("audit_get_strategy_history", strategy_id=strategy_id, limit=100, timeout=20)
        elif mode == "failures":
            resp = client.mcp_call("audit_query", success=False, limit=100, timeout=20)
        elif mode == "tool":
            resp = client.mcp_call("audit_query", tool_name=tool_name, limit=100, timeout=20)
        else:
            resp = client.mcp_call("audit_get_recent_actions", limit=100, timeout=20)
    except Exception as e:  # noqa
        meta.setdefault("warnings", []).append(f"{mode} audit fetch failed: {e}")
        return []
    return [_entry(e) for e in _rows(_ok(resp)) if isinstance(e, dict)]


def summarize(entries):
    by_action, failures = {}, []
    for e in entries:
        a = e.get("action_type") or "unknown"
        by_action[a] = by_action.get(a, 0) + 1
        if e.get("success") is False:
            failures.append(e)
    return {"total": len(entries), "by_action": by_action, "failures": failures}


def run(client, mode, strategy_id=None, tool_name=None):
    meta = {"warnings": []}
    entries = fetch(client, meta, mode, strategy_id, tool_name)
    if not entries and mode == "default":
        meta["degraded"] = "no audit entries — check the token is USER-scoped"
    return {"as_of": "live", "mode": mode, "entries": entries,
            "summary": summarize(entries), "meta": meta}


def _dry(client):
    try:
        return {"audit_get_recent_actions": client.mcp_call("audit_get_recent_actions", limit=3, timeout=15)}
    except Exception as e:  # noqa
        return {"error": str(e)}


def main(argv=None):
    ap = argparse.ArgumentParser(description="senpi audit engine (activity / history / failures)")
    ap.add_argument("--strategy", help="strategy id — full mutation history for that strategy")
    ap.add_argument("--failures", action="store_true", help="only failed operations")
    ap.add_argument("--tool", help="filter to one tool name")
    ap.add_argument("--fixture")
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args(argv)

    if a.fixture:
        try:
            with open(a.fixture) as f:
                client = _FixtureClient(json.load(f))
        except Exception as e:  # noqa
            print(json.dumps({"entries": [], "meta": {"error": f"fixture load failed: {e}"}}))
            return 1
    else:
        try:
            client = _get_client()
        except Exception as e:  # noqa
            print(json.dumps({"entries": [], "meta": {"error": f"mcp init failed: {e}"}}))
            return 1

    if a.dry:
        print(json.dumps(_dry(client), ensure_ascii=False, indent=2, default=str))
        return 0

    mode = "strategy" if a.strategy else ("failures" if a.failures else ("tool" if a.tool else "default"))
    try:
        result = run(client, mode, strategy_id=a.strategy, tool_name=a.tool)
    except Exception as e:  # noqa
        print(json.dumps({"entries": [], "meta": {"error": f"engine failure: {e}"}}))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
