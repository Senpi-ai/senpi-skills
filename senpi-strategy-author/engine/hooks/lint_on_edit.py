#!/usr/bin/env python3
"""PostToolUse hook: re-lint the package after every edit, so errors surface at the edit that made them.

Runs validate_strategy.py (seconds, no credentials) once the package has both a strategy.yaml and a
runtime.yaml. Errors come back to the model as a blocking message (exit 2); advisory warnings come
back as context to relay. Before the package is complete this stays silent — half a package is not
a finding.
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import json
import os
import subprocess
import sys
from pathlib import Path


def main():
    try:
        event = json.load(sys.stdin)
    except ValueError:
        return 0
    pkg = os.environ.get("SENPI_AUTHOR_PKG_DIR")
    skill = os.environ.get("SENPI_AUTHOR_SKILL_DIR")
    if not pkg or not skill:
        return 0
    pkg = Path(pkg).resolve()
    edited = (event.get("tool_input") or {}).get("file_path") or ""
    try:
        edited = (Path(event.get("cwd") or pkg) / edited).resolve()
    except (OSError, ValueError):
        return 0
    if pkg not in edited.parents:
        return 0
    if not (pkg / "strategy.yaml").is_file() or not any(pkg.rglob("runtime.yaml")):
        return 0
    try:
        proc = subprocess.run([sys.executable or "python3", str(Path(skill) / "scripts" / "validate_strategy.py"), str(pkg)],
                              capture_output=True, text=True, timeout=45, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return 0
    out = (proc.stdout + proc.stderr).strip()
    if proc.returncode != 0:
        print(f"validate_strategy.py found errors after this edit — fix them before moving on:\n{out[-3000:]}",
              file=sys.stderr)
        return 2
    if "WARN" in out.upper():
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": "validate_strategy.py passed with advisory warnings. Relay each one in the "
                                 f"result's `warnings`:\n{out[-2000:]}",
        }}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
