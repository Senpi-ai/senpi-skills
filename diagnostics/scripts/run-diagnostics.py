#!/usr/bin/env python3
"""Senpi Diagnostics Suite — comprehensive E2E preflight checks.

Run before enabling any trading strategy to verify that all Senpi calls,
state files, cron configs, and the mcporter gate pipeline work end-to-end.

Usage:
  python3 run-diagnostics.py --workspace /path --suite tiger
  python3 run-diagnostics.py --workspace /path --suite tiger --json
  python3 run-diagnostics.py --workspace /path --suite tiger --quick
  python3 run-diagnostics.py --workspace /path --suite tiger --phase close-path
  python3 run-diagnostics.py --workspace /path --suite tiger --cron-file /path/to/jobs.json
"""

import argparse
import glob
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))
from diag_lib import (
    DiagRunner, audit_script_source, discover_senpi_crons,
    find_dsl_state_files, find_suite_scripts, gate_call, load_cron_model_profiles,
    mcporter_call, resolve_animal, resolve_suite_config, resolve_suite_paths,
    resolve_workspace, run_cmd, validate_dsl_state,
    DEFAULT_PY, DEFAULT_WORKSPACE,
)

PHASE_ENV = "1-Environment"
PHASE_CRON = "2-Cron-Config"
PHASE_TOOLS = "3-Tool-Connectivity"
PHASE_AUDIT = "4-Source-Audit"
PHASE_SCRIPTS = "5-Script-Execution"
PHASE_CLOSE = "6-Close-Path"
PHASE_STATE = "7-State-Lifecycle"

ALL_PHASES = [PHASE_ENV, PHASE_CRON, PHASE_TOOLS, PHASE_AUDIT,
              PHASE_SCRIPTS, PHASE_CLOSE, PHASE_STATE]

PHASE_MAP = {
    "env": PHASE_ENV,
    "environment": PHASE_ENV,
    "cron": PHASE_CRON,
    "cron-config": PHASE_CRON,
    "tools": PHASE_TOOLS,
    "tool-connectivity": PHASE_TOOLS,
    "audit": PHASE_AUDIT,
    "source-audit": PHASE_AUDIT,
    "scripts": PHASE_SCRIPTS,
    "script-execution": PHASE_SCRIPTS,
    "close": PHASE_CLOSE,
    "close-path": PHASE_CLOSE,
    "state": PHASE_STATE,
    "state-lifecycle": PHASE_STATE,
}


# ═══════════════════════════════════════════════════════════════════
# PHASE 1: Environment and Infrastructure
# ═══════════════════════════════════════════════════════════════════

def phase_environment(diag, workspace, suite_paths):
    phase = PHASE_ENV

    # 1a. Python venv
    t = time.time()
    py = os.environ.get("DIAG_PYTHON", DEFAULT_PY)
    if os.path.isfile(py) and os.access(py, os.X_OK):
        rc, out, _ = run_cmd([py, "--version"], timeout=5)
        diag.record("python_venv", phase, rc == 0,
                     detail=out.strip() if rc == 0 else f"rc={rc}",
                     duration_ms=_ms(t))
    else:
        diag.record("python_venv", phase, False,
                     detail=f"Not found or not executable: {py}",
                     remediation="Ensure venv exists at the configured path",
                     duration_ms=_ms(t))

    # 1b. Gate adapter handler
    t = time.time()
    gate = os.path.join(workspace, "skills/mcporter-gate/handler.py")
    if os.path.isfile(gate):
        rc, out, err = run_cmd([py, gate, "list"], timeout=15)
        ok = rc == 0 and "senpi" in out.lower()
        diag.record("gate_adapter", phase, ok,
                     detail="Gate lists senpi server" if ok
                            else f"rc={rc} err={err[:200]}",
                     remediation="Check handler.py and mcporter-gate config.json",
                     duration_ms=_ms(t))
    else:
        diag.record("gate_adapter", phase, False,
                     detail=f"Not found: {gate}",
                     remediation="Deploy mcporter-gate to workspace",
                     duration_ms=_ms(t))

    # 1c. /usr/local/bin/mcporter symlink chain
    t = time.time()
    mcporter_path = "/usr/local/bin/mcporter"
    if os.path.exists(mcporter_path):
        real = os.path.realpath(mcporter_path)
        ok = "mcporter" in real.lower()
        diag.record("mcporter_symlink", phase, ok,
                     detail=f"{mcporter_path} -> {real}",
                     remediation="Run: sudo ln -sf ~/.openclaw/workspace/scripts/mcporter /usr/local/bin/mcporter",
                     duration_ms=_ms(t))
    else:
        diag.record("mcporter_symlink", phase, False, critical=False,
                     detail="Not found (may be running locally, not on VM)",
                     remediation="Deploy to VM and verify symlink",
                     duration_ms=_ms(t))

    # 1d. MCPORTER_CMD env var
    t = time.time()
    mcporter_cmd = os.environ.get("MCPORTER_CMD", "")
    if mcporter_cmd:
        exists = os.path.isfile(mcporter_cmd) and os.access(mcporter_cmd, os.X_OK)
        diag.record("mcporter_cmd_env", phase, exists,
                     detail=f"MCPORTER_CMD={mcporter_cmd} (exists={exists})",
                     remediation="Set MCPORTER_CMD to the wrapper path",
                     duration_ms=_ms(t))
    else:
        diag.record("mcporter_cmd_env", phase, True, critical=False,
                     detail="MCPORTER_CMD not set; relying on PATH resolution",
                     duration_ms=_ms(t))

    # 1e. Runtime wrapper exists and has gate routing
    t = time.time()
    wrapper = os.path.join(workspace,
                           "skills/senpi-trading/runtime/bin/mcporter-senpi-wrapper.sh")
    if os.path.isfile(wrapper):
        try:
            with open(wrapper) as f:
                content = f.read()
            has_gate = "mcporter-gate" in content or "GATE" in content or "handler.py" in content
            has_npm = "NPM_CONFIG_CACHE" in content
            issues = []
            if not has_gate:
                issues.append("no gate routing")
            if not has_npm:
                issues.append("no NPM_CONFIG_CACHE")
            ok = has_gate
            diag.record("runtime_wrapper", phase, ok,
                         detail=f"Gate routing: {has_gate}, NPM cache: {has_npm}",
                         remediation="; ".join(issues) if issues else "",
                         duration_ms=_ms(t))
        except Exception as e:
            diag.record("runtime_wrapper", phase, False,
                         detail=str(e), duration_ms=_ms(t))
    else:
        diag.record("runtime_wrapper", phase, False,
                     detail=f"Not found: {wrapper}",
                     remediation="Deploy runtime wrapper",
                     duration_ms=_ms(t))

    # 1f. Suite scripts directory exists
    t = time.time()
    scripts_dir = suite_paths.get("scripts_dir", "")
    exists = os.path.isdir(scripts_dir)
    diag.record("suite_scripts_dir", phase, exists,
                 detail=scripts_dir if exists else f"Not found: {scripts_dir}",
                 remediation="Sync submodule: make deploy-workspace",
                 duration_ms=_ms(t))


# ═══════════════════════════════════════════════════════════════════
# PHASE 2: Cron Config Discovery and Validation
# ═══════════════════════════════════════════════════════════════════

def phase_cron_config(diag, workspace, suite_name, cron_file=None):
    phase = PHASE_CRON

    # 2a. Load crons
    t = time.time()
    jobs, path = discover_senpi_crons(cron_file)
    if not jobs:
        diag.record("cron_discovery", phase, False, critical=False,
                     detail=f"No Senpi crons found in {path}",
                     remediation="Provide --cron-file or ensure jobs.json is accessible",
                     duration_ms=_ms(t))
        return
    diag.record("cron_discovery", phase, True,
                 detail=f"Found {len(jobs)} Senpi-related crons in {path}",
                 duration_ms=_ms(t))

    # 2b. Load model profiles
    profiles = load_cron_model_profiles(workspace)
    suite_profile = profiles.get("profiles", {}).get(suite_name, {})
    model_rules = suite_profile.get("rules", [])

    enabled_jobs = [j for j in jobs if j.get("enabled", False)]
    diag.record("cron_enabled_count", phase, len(enabled_jobs) > 0,
                 critical=False,
                 detail=f"{len(enabled_jobs)} enabled, {len(jobs) - len(enabled_jobs)} disabled")

    # 2c. Validate each enabled cron
    for job in enabled_jobs:
        name = job.get("name", "unnamed")
        safe_name = re.sub(r'[^a-zA-Z0-9_]', '_', name.lower())[:40]
        payload = job.get("payload", {})
        message = payload.get("message", "")
        model = payload.get("model", "")

        issues = []

        # Script path validation: extract python script paths from message
        script_refs = re.findall(
            r'/[^\s"\']+\.py', message
        )
        for ref in script_refs:
            if not os.path.isfile(ref):
                issues.append(f"script not found: {ref}")

        # MCPORTER_CMD or PATH shim in payload
        has_mcporter_cmd = "MCPORTER_CMD" in message
        has_path_shim = "PATH=" in message and "/bin:" in message
        if not has_mcporter_cmd and not has_path_shim:
            if any(kw in message.lower() for kw in ["mcporter", "senpi", "dsl", "scanner"]):
                issues.append("no MCPORTER_CMD or PATH shim in payload")

        # Bare mcporter in payload
        if "mcporter call senpi" in message and "MCPORTER_CMD" not in message:
            if "PATH=" not in message:
                issues.append("bare 'mcporter call senpi' without shim")

        # Model tier check
        for rule in model_rules:
            if rule.get("matchContains", "").lower() in name.lower():
                expected = rule["expectedModel"]
                if model and model != expected:
                    issues.append(f"model mismatch: got {model}, expected {expected}")
                break

        ok = len(issues) == 0
        diag.record(f"cron_{safe_name}", phase, ok,
                     critical=not ok and any("script not found" in i or
                                             "bare" in i for i in issues),
                     detail=f"{name}: {'; '.join(issues)}" if issues
                            else f"{name}: OK",
                     remediation="; ".join(issues) if issues else "")


# ═══════════════════════════════════════════════════════════════════
# PHASE 3: Senpi MCP Tool Connectivity
# ═══════════════════════════════════════════════════════════════════

CONNECTIVITY_TOOLS = [
    ("market_list_instruments", {}, True),
    ("market_get_prices", {}, True),
    ("leaderboard_get_markets", {}, True),
    ("leaderboard_get_top", {}, False),
]


def phase_tool_connectivity(diag, workspace, animal):
    phase = PHASE_TOOLS

    # 3a. Read tools
    for tool, args, critical in CONNECTIVITY_TOOLS:
        t = time.time()
        rc, out, err = gate_call(workspace, tool, timeout=30, **args)
        dur = _ms(t)
        try:
            d = json.loads(out)
            ok = d.get("success") is True or "data" in d or isinstance(d, list)
            diag.record(f"tool_{tool}", phase, ok, critical=critical,
                         detail=f"OK ({dur}ms)" if ok
                                else f"API error: {_trunc(str(d), 200)}",
                         duration_ms=dur)
        except (json.JSONDecodeError, Exception) as e:
            diag.record(f"tool_{tool}", phase, False, critical=critical,
                         detail=f"Parse error: {_trunc(str(e), 100)} stderr={_trunc(err, 100)}",
                         remediation="Check gate adapter and Senpi API connectivity",
                         duration_ms=dur)

    # 3b. market_get_asset_data with BTC
    t = time.time()
    rc, out, err = gate_call(workspace, "market_get_asset_data",
                             timeout=30, coin="BTC")
    dur = _ms(t)
    try:
        d = json.loads(out)
        ok = d.get("success") is True or "data" in d
        diag.record("tool_market_get_asset_data", phase, ok,
                     detail=f"BTC data OK ({dur}ms)" if ok
                            else f"Error: {_trunc(str(d), 200)}",
                     duration_ms=dur)
    except Exception as e:
        diag.record("tool_market_get_asset_data", phase, False,
                     detail=f"{_trunc(str(e), 200)} stderr={_trunc(err, 100)}",
                     duration_ms=dur)

    # 3c. strategy_get_clearinghouse_state with configured wallet
    wallet = animal.get("wallet", "")
    if wallet:
        t = time.time()
        rc, out, err = gate_call(workspace,
                                 "strategy_get_clearinghouse_state",
                                 timeout=30,
                                 strategy_wallet=wallet)
        dur = _ms(t)
        try:
            d = json.loads(out)
            main = d.get("data", {})
            if isinstance(main, dict) and "main" in main:
                main = main["main"]
            margin = main.get("crossMarginSummary",
                              main.get("marginSummary", {}))
            val = margin.get("accountValue", "0")
            ok = float(val) > 0
            diag.record("tool_clearinghouse", phase, ok,
                         detail=f"Account: ${float(val):,.2f} ({dur}ms)",
                         duration_ms=dur)
        except Exception as e:
            diag.record("tool_clearinghouse", phase, False,
                         detail=f"Failed: {_trunc(str(e), 200)}",
                         remediation="Check wallet address in animals.json",
                         duration_ms=dur)
    else:
        diag.record("tool_clearinghouse", phase, False,
                     detail="No wallet configured for animal",
                     remediation="Set wallet in runtime/config/animals.json")


# ═══════════════════════════════════════════════════════════════════
# PHASE 4: Script Source Code Audit
# ═══════════════════════════════════════════════════════════════════

def phase_source_audit(diag, workspace, suite_paths, suite_name):
    phase = PHASE_AUDIT
    submodule = suite_paths["submodule_root"]

    # Collect all script files to audit
    scripts_to_audit = []

    # Suite scripts
    suite_scripts = find_suite_scripts(suite_paths["scripts_dir"])
    scripts_to_audit.extend(suite_scripts)

    # DSL v4 (shared across suites)
    dsl_v4 = os.path.join(submodule, "dsl-dynamic-stop-loss/scripts/dsl-v4.py")
    if os.path.isfile(dsl_v4):
        scripts_to_audit.append(dsl_v4)

    # Emerging movers (standalone)
    em = os.path.join(submodule, "emerging-movers/scripts/emerging-movers.py")
    if os.path.isfile(em):
        scripts_to_audit.append(em)

    if not scripts_to_audit:
        diag.record("source_audit", phase, False, critical=False,
                     detail="No scripts found to audit")
        return

    total_issues = 0
    bare_mcporter_files = []

    for script in scripts_to_audit:
        basename = os.path.basename(script)
        issues = audit_script_source(script)

        bare = [i for i in issues if i["kind"] == "bare_mcporter"]
        curl = [i for i in issues if i["kind"] == "bare_curl_hyperliquid"]

        if bare:
            bare_mcporter_files.append(basename)
            total_issues += len(bare)
            diag.record(f"audit_{_safe(basename)}_mcporter", phase, False,
                         detail=f"{basename}: {len(bare)} bare mcporter call(s) "
                                f"without MCPORTER_CMD at line(s) "
                                f"{', '.join(str(i['line']) for i in bare)}",
                         remediation=f"Add os.environ.get('MCPORTER_CMD', 'mcporter') to {basename}")

        if curl:
            total_issues += len(curl)
            diag.record(f"audit_{_safe(basename)}_curl", phase, False,
                         critical=False,
                         detail=f"{basename}: {len(curl)} direct curl to Hyperliquid API "
                                f"at line(s) {', '.join(str(i['line']) for i in curl)}",
                         remediation="Consider using market_get_prices via mcporter instead")

        if not bare and not curl:
            diag.record(f"audit_{_safe(basename)}", phase, True,
                         critical=False,
                         detail=f"{basename}: clean")

    # Summary
    if bare_mcporter_files:
        diag.record("audit_summary", phase, False,
                     detail=f"{len(bare_mcporter_files)} file(s) with bare mcporter: "
                            f"{', '.join(bare_mcporter_files)}",
                     remediation="All mcporter calls must use MCPORTER_CMD env var")
    else:
        diag.record("audit_summary", phase, True,
                     detail=f"All {len(scripts_to_audit)} scripts pass source audit")


# ═══════════════════════════════════════════════════════════════════
# PHASE 5: Script Execution Tests (dry-run)
# ═══════════════════════════════════════════════════════════════════

TIGER_SCRIPT_EXPECTATIONS = {
    "prescreener.py": {"keys": ["actionable", "prescreened", "top", "scores"],
                       "any_key": True},
    "compression-scanner.py": {"keys": ["signals", "actionable"],
                                "any_key": True},
    "correlation-scanner.py": {"keys": ["signals", "actionable"],
                                "any_key": True},
    "momentum-scanner.py": {"keys": ["signals", "actionable"],
                             "any_key": True},
    "reversion-scanner.py": {"keys": ["signals", "actionable"],
                              "any_key": True},
    "funding-scanner.py": {"keys": ["signals", "actionable"],
                            "any_key": True},
    "oi-tracker.py": {"keys": ["status", "tracked", "assets"],
                       "any_key": True},
    "goal-engine.py": {"keys": ["aggression", "balance", "status"],
                        "any_key": True},
    "risk-guardian.py": {"keys": ["status", "actions", "risk"],
                          "any_key": True},
    "tiger-exit.py": {"keys": ["status", "exits", "signals"],
                       "any_key": True},
}


def phase_script_execution(diag, workspace, suite_paths, suite_name, animal):
    phase = PHASE_SCRIPTS
    py = os.environ.get("DIAG_PYTHON", DEFAULT_PY)
    scripts_dir = suite_paths["scripts_dir"]

    if not os.path.isdir(scripts_dir):
        diag.record("script_exec", phase, False,
                     detail=f"Scripts dir not found: {scripts_dir}")
        return

    expectations = TIGER_SCRIPT_EXPECTATIONS if suite_name == "tiger" else {}
    wallet = animal.get("wallet", "")
    strategy_id = animal.get("strategyId", "")

    env_extra = {
        "MCPORTER_CMD": os.environ.get("MCPORTER_CMD",
                                        os.path.join(workspace,
                                                     "scripts/mcporter-senpi-wrapper.sh")),
        "TIGER_WORKSPACE": workspace,
        "WOLF_WORKSPACE": workspace,
        "OPENCLAW_WORKSPACE": workspace,
        "PYTHONUNBUFFERED": "1",
        "NPM_CONFIG_CACHE": "/tmp/npm-cache",
    }
    if wallet:
        env_extra["TIGER_WALLET"] = wallet
    if strategy_id:
        env_extra["TIGER_STRATEGY_ID"] = strategy_id

    # Config file
    config_file = os.path.join(workspace, f"{suite_name}-config.json")
    if os.path.isfile(config_file):
        env_extra["TIGER_CONFIG"] = config_file

    for script_name, expected in expectations.items():
        script_path = os.path.join(scripts_dir, script_name)
        if not os.path.isfile(script_path):
            diag.record(f"exec_{_safe(script_name)}", phase, False,
                         critical=False,
                         detail=f"Script not found: {script_name}")
            continue

        t = time.time()
        run_env = {**os.environ, **env_extra}
        try:
            r = subprocess.run(
                [py, script_path],
                capture_output=True, text=True, timeout=45,
                env=run_env, cwd=scripts_dir,
            )
            dur = _ms(t)

            if r.returncode != 0 and not r.stdout.strip():
                diag.record(f"exec_{_safe(script_name)}", phase, False,
                             detail=f"Exit {r.returncode}: {_trunc(r.stderr, 200)}",
                             remediation=f"Run manually: {py} {script_path}",
                             duration_ms=dur)
                continue

            try:
                d = json.loads(r.stdout)
                if expected.get("any_key"):
                    found = any(k in d for k in expected["keys"])
                else:
                    found = all(k in d for k in expected["keys"])

                if found or (isinstance(d, dict) and
                             d.get("status") not in ("error", "crash")):
                    diag.record(f"exec_{_safe(script_name)}", phase, True,
                                 critical=False,
                                 detail=f"OK: keys={list(d.keys())[:5]} ({dur}ms)",
                                 duration_ms=dur)
                else:
                    diag.record(f"exec_{_safe(script_name)}", phase, False,
                                 critical=False,
                                 detail=f"Unexpected output: {_trunc(str(d), 200)}",
                                 duration_ms=dur)
            except json.JSONDecodeError:
                stderr_hint = _trunc(r.stderr, 100)
                stdout_hint = _trunc(r.stdout, 200)
                if r.returncode == 0:
                    diag.record(f"exec_{_safe(script_name)}", phase, True,
                                 critical=False,
                                 detail=f"Ran OK (non-JSON output): {stdout_hint}",
                                 duration_ms=dur)
                else:
                    diag.record(f"exec_{_safe(script_name)}", phase, False,
                                 critical=False,
                                 detail=f"Bad output (rc={r.returncode}): {stdout_hint} | {stderr_hint}",
                                 duration_ms=dur)

        except subprocess.TimeoutExpired:
            diag.record(f"exec_{_safe(script_name)}", phase, False,
                         critical=False,
                         detail=f"TIMEOUT after 45s",
                         remediation="Script may be hanging on mcporter call",
                         duration_ms=_ms(t))
        except Exception as e:
            diag.record(f"exec_{_safe(script_name)}", phase, False,
                         critical=False,
                         detail=f"Error: {_trunc(str(e), 200)}",
                         duration_ms=_ms(t))

    # DSL v4 dry-run with synthetic state
    _test_dsl_dry_run(diag, workspace, suite_paths, py)


def _test_dsl_dry_run(diag, workspace, suite_paths, py):
    """Run DSL v4 against a synthetic state file."""
    phase = PHASE_SCRIPTS
    submodule = suite_paths["submodule_root"]
    dsl_v4 = os.path.join(submodule, "dsl-dynamic-stop-loss/scripts/dsl-v4.py")

    if not os.path.isfile(dsl_v4):
        diag.record("exec_dsl_v4_dryrun", phase, False,
                     detail=f"DSL v4 not found: {dsl_v4}")
        return

    synthetic = {
        "active": True,
        "asset": "BTC",
        "direction": "LONG",
        "entryPrice": 65000.0,
        "size": 0.01,
        "leverage": 7,
        "wallet": "",
        "phase": 1,
        "currentBreachCount": 0,
        "currentTierIndex": -1,
        "tierFloorPrice": 0,
        "highWaterPrice": 65500.0,
        "phase1": {
            "retraceThreshold": 0.02,
            "consecutiveBreachesRequired": 3,
            "absoluteFloor": 63700.0,
        },
        "phase2": {
            "retraceThreshold": 0.015,
            "consecutiveBreachesRequired": 2,
        },
        "tiers": [
            {"triggerPct": 5, "lockPct": 0.2, "retrace": 0.015, "maxBreaches": 2},
            {"triggerPct": 10, "lockPct": 0.5, "retrace": 0.012, "maxBreaches": 2},
        ],
        "createdAt": datetime.now(timezone.utc).isoformat(),
    }

    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json",
                                      delete=False, dir="/tmp")
    json.dump(synthetic, tmp, indent=2)
    tmp.close()

    t = time.time()
    try:
        env = {**os.environ, "DSL_STATE_FILE": tmp.name,
               "NPM_CONFIG_CACHE": "/tmp/npm-cache",
               "MCPORTER_CMD": os.environ.get("MCPORTER_CMD",
                   os.path.join(workspace, "scripts/mcporter-senpi-wrapper.sh"))}
        r = subprocess.run([py, dsl_v4], capture_output=True, text=True,
                           timeout=30, env=env)
        dur = _ms(t)

        try:
            d = json.loads(r.stdout)
            if d.get("status") == "active" and d.get("asset") == "BTC":
                diag.record("exec_dsl_v4_dryrun", phase, True,
                             detail=f"DSL engine OK: price={d.get('price')}, "
                                    f"phase={d.get('phase')} ({dur}ms)",
                             duration_ms=dur)
            elif d.get("status") == "error":
                diag.record("exec_dsl_v4_dryrun", phase, False,
                             detail=f"DSL error: {_trunc(str(d), 200)}",
                             duration_ms=dur)
            else:
                diag.record("exec_dsl_v4_dryrun", phase, True, critical=False,
                             detail=f"DSL ran (status={d.get('status')})",
                             duration_ms=dur)
        except json.JSONDecodeError:
            diag.record("exec_dsl_v4_dryrun", phase, False,
                         detail=f"Bad output: {_trunc(r.stdout, 200)} | {_trunc(r.stderr, 200)}",
                         duration_ms=dur)
    except subprocess.TimeoutExpired:
        diag.record("exec_dsl_v4_dryrun", phase, False,
                     detail="TIMEOUT", duration_ms=_ms(t))
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass


# ═══════════════════════════════════════════════════════════════════
# PHASE 6: Close Position Path Verification
# ═══════════════════════════════════════════════════════════════════

def phase_close_path(diag, workspace, animal):
    phase = PHASE_CLOSE
    wallet = animal.get("wallet", "")

    if not wallet:
        diag.record("close_path_gate", phase, False,
                     detail="No wallet configured -- cannot test close path",
                     remediation="Set wallet in animals.json")
        return

    # 6a. Call close_position via gate with a non-existent coin.
    # We expect a Senpi API error (not auth error), proving the full chain works.
    t = time.time()
    rc, out, err = gate_call(workspace, "close_position", timeout=30,
                             strategy_wallet=wallet,
                             coin="DIAGNOSISTEST",
                             reason="senpi-diagnostics-preflight")
    dur = _ms(t)

    auth_failure_signals = [
        "unauthorized", "auth", "403", "401",
        "token", "permission", "SENPI_AUTH_TOKEN",
    ]
    empty_body = not out.strip() and not err.strip()

    if empty_body:
        diag.record("close_path_gate", phase, False,
                     detail="Empty response -- gate/token chain likely broken",
                     remediation="Check 1Password token, gate adapter, and mcporter-senpi-wrapper",
                     duration_ms=dur)
    else:
        combined = (out + err).lower()
        is_auth_fail = any(sig in combined for sig in auth_failure_signals)

        if is_auth_fail:
            diag.record("close_path_gate", phase, False,
                         detail=f"AUTH FAILURE: {_trunc(out + err, 300)}",
                         remediation="Gate adapter cannot authenticate with Senpi. "
                                     "Check SENPI_AUTH_TOKEN in 1Password and gate config.",
                         duration_ms=dur)
        else:
            # Any other error (e.g., "invalid coin", "no position") means
            # the full chain works: shim -> gate -> 1Password -> token -> Senpi
            diag.record("close_path_gate", phase, True,
                         detail=f"Gate close_position chain works "
                                f"(got expected API rejection): {_trunc(out, 150)}",
                         duration_ms=dur)

    # 6b. Verify MCPORTER_CMD routes through gate (not bare)
    t = time.time()
    mcporter_cmd = os.environ.get("MCPORTER_CMD", "")
    if mcporter_cmd and os.path.isfile(mcporter_cmd):
        try:
            with open(mcporter_cmd) as f:
                content = f.read()
            routes_gate = ("mcporter-gate" in content or "handler.py" in content
                           or "mcporter-senpi-wrapper" in content)
            diag.record("close_path_mcporter_cmd", phase, routes_gate,
                         detail=f"MCPORTER_CMD ({os.path.basename(mcporter_cmd)}) "
                                f"{'routes through gate' if routes_gate else 'does NOT route through gate'}",
                         remediation="MCPORTER_CMD must point to wrapper that routes through gate",
                         duration_ms=_ms(t))
        except Exception as e:
            diag.record("close_path_mcporter_cmd", phase, False,
                         detail=str(e), duration_ms=_ms(t))
    else:
        # Try to resolve what PATH gives us
        rc, out, _ = run_cmd(["which", "mcporter"], timeout=5)
        if rc == 0 and out.strip():
            real = os.path.realpath(out.strip())
            diag.record("close_path_mcporter_cmd", phase, True, critical=False,
                         detail=f"PATH mcporter resolves to: {real}",
                         duration_ms=_ms(t))
        else:
            diag.record("close_path_mcporter_cmd", phase, False, critical=False,
                         detail="mcporter not found in PATH and MCPORTER_CMD not set",
                         remediation="Set MCPORTER_CMD or ensure mcporter is in PATH",
                         duration_ms=_ms(t))

    # 6c. Verify bare mcporter (without gate) would fail
    t = time.time()
    bare_mcporter = "/usr/bin/mcporter"
    if os.path.isfile(bare_mcporter):
        rc, out, err = run_cmd(
            [bare_mcporter, "call", "senpi.close_position",
             "--args", json.dumps({"strategy_wallet": wallet,
                                    "coin": "DIAGNOSISTEST",
                                    "reason": "diag-bare-test"})],
            timeout=15)
        combined = (out + err).lower()
        bare_empty = not out.strip() and not err.strip()
        bare_fails = rc != 0 or any(s in combined for s in auth_failure_signals) or bare_empty
        diag.record("close_path_bare_fails", phase, bare_fails, critical=False,
                     detail=f"Bare mcporter {'correctly fails' if bare_fails else 'UNEXPECTEDLY SUCCEEDS'} "
                            f"without gate",
                     duration_ms=_ms(t))
    else:
        diag.record("close_path_bare_fails", phase, True, critical=False,
                     detail=f"Bare mcporter not found at {bare_mcporter} (expected on VM)",
                     duration_ms=_ms(t))


# ═══════════════════════════════════════════════════════════════════
# PHASE 7: State File Lifecycle Simulation
# ═══════════════════════════════════════════════════════════════════

def phase_state_lifecycle(diag, workspace, suite_paths, animal):
    phase = PHASE_STATE
    py = os.environ.get("DIAG_PYTHON", DEFAULT_PY)
    wallet = animal.get("wallet", "")
    submodule = suite_paths["submodule_root"]
    dsl_v4 = os.path.join(submodule, "dsl-dynamic-stop-loss/scripts/dsl-v4.py")

    # 7a. Validate all existing active DSL state files
    t = time.time()
    state_files = find_dsl_state_files(workspace, animal)
    if state_files:
        all_issues = []
        active_count = 0
        for sf in state_files:
            issues = validate_dsl_state(sf, expected_wallet=wallet)
            if issues:
                all_issues.extend(issues)
            try:
                with open(sf) as f:
                    s = json.load(f)
                if s.get("active", False):
                    active_count += 1
            except Exception:
                pass

        if all_issues:
            diag.record("state_active_validation", phase, False,
                         detail=f"{active_count} active, issues: {'; '.join(all_issues[:5])}",
                         remediation="Fix state files: add missing wallet, phase2, highWaterPrice",
                         duration_ms=_ms(t))
        else:
            diag.record("state_active_validation", phase, True,
                         detail=f"{active_count} active positions, all valid "
                                f"({len(state_files)} total files)",
                         duration_ms=_ms(t))
    else:
        diag.record("state_active_validation", phase, True, critical=False,
                     detail="No DSL state files found (no active positions)",
                     duration_ms=_ms(t))

    if not os.path.isfile(dsl_v4):
        diag.record("state_lifecycle_sim", phase, False,
                     detail="DSL v4 not found, cannot run lifecycle simulation")
        return

    # 7b. Full lifecycle: create state -> DSL run -> verify update
    t = time.time()
    good_state = _make_synthetic_state(wallet)
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json",
                                      delete=False, dir="/tmp",
                                      prefix="diag-lifecycle-")
    json.dump(good_state, tmp, indent=2)
    tmp.close()

    try:
        env = {**os.environ, "DSL_STATE_FILE": tmp.name,
               "NPM_CONFIG_CACHE": "/tmp/npm-cache",
               "MCPORTER_CMD": os.environ.get("MCPORTER_CMD",
                   os.path.join(workspace, "scripts/mcporter-senpi-wrapper.sh"))}
        r = subprocess.run([py, dsl_v4], capture_output=True, text=True,
                           timeout=30, env=env)

        with open(tmp.name) as f:
            updated = json.load(f)

        has_last_check = "lastCheck" in updated or "lastCheckAt" in updated
        has_last_price = "lastPrice" in updated
        ok = has_last_check or has_last_price or r.returncode == 0

        diag.record("state_lifecycle_create_run", phase, ok,
                     detail=f"DSL updated state: lastCheck={has_last_check}, "
                            f"lastPrice={has_last_price}",
                     duration_ms=_ms(t))
    except Exception as e:
        diag.record("state_lifecycle_create_run", phase, False,
                     detail=f"Error: {_trunc(str(e), 200)}",
                     duration_ms=_ms(t))
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass

    # 7c. Missing wallet test
    t = time.time()
    no_wallet_state = _make_synthetic_state("")
    tmp2 = tempfile.NamedTemporaryFile(mode="w", suffix=".json",
                                       delete=False, dir="/tmp",
                                       prefix="diag-nowallet-")
    json.dump(no_wallet_state, tmp2, indent=2)
    tmp2.close()

    try:
        # Set breach conditions so DSL tries to close
        no_wallet_state["currentBreachCount"] = 2
        no_wallet_state["phase1"]["consecutiveBreachesRequired"] = 3
        no_wallet_state["highWaterPrice"] = 60000.0  # well below entry for LONG
        with open(tmp2.name, "w") as f:
            json.dump(no_wallet_state, f, indent=2)

        env = {**os.environ, "DSL_STATE_FILE": tmp2.name,
               "NPM_CONFIG_CACHE": "/tmp/npm-cache",
               "MCPORTER_CMD": os.environ.get("MCPORTER_CMD",
                   os.path.join(workspace, "scripts/mcporter-senpi-wrapper.sh"))}
        r = subprocess.run([py, dsl_v4], capture_output=True, text=True,
                           timeout=30, env=env)

        with open(tmp2.name) as f:
            result = json.load(f)

        # With no wallet, DSL should either set pendingClose or report the issue
        pending = result.get("pendingClose", False)
        has_error = "error" in r.stdout.lower() or "wallet" in r.stdout.lower()
        ok = pending or has_error or r.returncode != 0
        diag.record("state_missing_wallet", phase, ok, critical=False,
                     detail=f"Missing wallet handled: pendingClose={pending}, "
                            f"error_reported={has_error}",
                     remediation="DSL should set pendingClose when wallet is missing",
                     duration_ms=_ms(t))
    except Exception as e:
        diag.record("state_missing_wallet", phase, False, critical=False,
                     detail=f"Error: {_trunc(str(e), 200)}",
                     duration_ms=_ms(t))
    finally:
        try:
            os.unlink(tmp2.name)
        except OSError:
            pass

    # 7d. Missing phase2 test
    t = time.time()
    no_phase2_state = _make_synthetic_state(wallet)
    del no_phase2_state["phase2"]
    tmp3 = tempfile.NamedTemporaryFile(mode="w", suffix=".json",
                                       delete=False, dir="/tmp",
                                       prefix="diag-nophase2-")
    json.dump(no_phase2_state, tmp3, indent=2)
    tmp3.close()

    try:
        env = {**os.environ, "DSL_STATE_FILE": tmp3.name,
               "NPM_CONFIG_CACHE": "/tmp/npm-cache",
               "MCPORTER_CMD": os.environ.get("MCPORTER_CMD",
                   os.path.join(workspace, "scripts/mcporter-senpi-wrapper.sh"))}
        r = subprocess.run([py, dsl_v4], capture_output=True, text=True,
                           timeout=30, env=env)
        dur = _ms(t)

        crashed = r.returncode != 0 and "KeyError" in r.stderr
        if crashed:
            diag.record("state_missing_phase2", phase, False,
                         detail=f"DSL CRASHES on missing phase2: {_trunc(r.stderr, 200)}",
                         remediation="DSL v4 should handle missing phase2 gracefully",
                         duration_ms=dur)
        else:
            diag.record("state_missing_phase2", phase, True, critical=False,
                         detail=f"DSL handles missing phase2 (rc={r.returncode})",
                         duration_ms=dur)
    except Exception as e:
        diag.record("state_missing_phase2", phase, False, critical=False,
                     detail=str(e), duration_ms=_ms(t))
    finally:
        try:
            os.unlink(tmp3.name)
        except OSError:
            pass

    # 7e. Missing highWaterPrice test
    t = time.time()
    no_hwp_state = _make_synthetic_state(wallet)
    del no_hwp_state["highWaterPrice"]
    tmp4 = tempfile.NamedTemporaryFile(mode="w", suffix=".json",
                                       delete=False, dir="/tmp",
                                       prefix="diag-nohwp-")
    json.dump(no_hwp_state, tmp4, indent=2)
    tmp4.close()

    try:
        env = {**os.environ, "DSL_STATE_FILE": tmp4.name,
               "NPM_CONFIG_CACHE": "/tmp/npm-cache",
               "MCPORTER_CMD": os.environ.get("MCPORTER_CMD",
                   os.path.join(workspace, "scripts/mcporter-senpi-wrapper.sh"))}
        r = subprocess.run([py, dsl_v4], capture_output=True, text=True,
                           timeout=30, env=env)
        dur = _ms(t)

        crashed = r.returncode != 0 and ("KeyError" in r.stderr or
                                          "highWaterPrice" in r.stderr)
        if crashed:
            diag.record("state_missing_highwater", phase, False,
                         detail=f"DSL CRASHES on missing highWaterPrice: "
                                f"{_trunc(r.stderr, 200)}",
                         remediation="DSL v4 should default highWaterPrice to entryPrice",
                         duration_ms=dur)
        else:
            diag.record("state_missing_highwater", phase, True, critical=False,
                         detail=f"DSL handles missing highWaterPrice (rc={r.returncode})",
                         duration_ms=dur)
    except Exception as e:
        diag.record("state_missing_highwater", phase, False, critical=False,
                     detail=str(e), duration_ms=_ms(t))
    finally:
        try:
            os.unlink(tmp4.name)
        except OSError:
            pass


# ═══════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════

def _ms(start):
    return int((time.time() - start) * 1000)


def _trunc(s, n=200):
    s = str(s).strip()
    return s[:n] + "..." if len(s) > n else s


def _safe(name):
    return re.sub(r'[^a-zA-Z0-9_]', '_', name.replace(".py", ""))


def _make_synthetic_state(wallet):
    return {
        "active": True,
        "asset": "BTC",
        "direction": "LONG",
        "entryPrice": 65000.0,
        "size": 0.01,
        "leverage": 7,
        "wallet": wallet,
        "phase": 1,
        "currentBreachCount": 0,
        "currentTierIndex": -1,
        "tierFloorPrice": 0,
        "highWaterPrice": 65500.0,
        "phase1": {
            "retraceThreshold": 0.02,
            "consecutiveBreachesRequired": 3,
            "absoluteFloor": 63700.0,
        },
        "phase2": {
            "retraceThreshold": 0.015,
            "consecutiveBreachesRequired": 2,
        },
        "tiers": [
            {"triggerPct": 5, "lockPct": 0.2, "retrace": 0.015, "maxBreaches": 2},
            {"triggerPct": 10, "lockPct": 0.5, "retrace": 0.012, "maxBreaches": 2},
        ],
        "createdAt": datetime.now(timezone.utc).isoformat(),
    }


# ═══════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Senpi Diagnostics Suite — E2E preflight checks")
    parser.add_argument("--workspace", "-w", default=None,
                        help=f"Workspace path (default: {DEFAULT_WORKSPACE})")
    parser.add_argument("--suite", "-s", default="tiger",
                        help="Suite name (default: tiger)")
    parser.add_argument("--animal", "-a", default=None,
                        help="Animal name (default: suite default)")
    parser.add_argument("--json", action="store_true",
                        help="Output JSON instead of human-readable")
    parser.add_argument("--quick", action="store_true",
                        help="Skip slow tests (script execution, state lifecycle)")
    parser.add_argument("--phase", default=None,
                        help="Run only a specific phase (e.g., close-path, env, cron)")
    parser.add_argument("--cron-file", default=None,
                        help="Path to cron jobs.json file")
    args = parser.parse_args()

    workspace = resolve_workspace(args.workspace)
    suite_name = args.suite
    suite_paths = resolve_suite_paths(workspace, suite_name)
    animal_name, animal = resolve_animal(workspace, args.animal)

    # Determine which phases to run
    if args.phase:
        target = PHASE_MAP.get(args.phase.lower(), args.phase)
        phases = [target]
    elif args.quick:
        phases = [PHASE_ENV, PHASE_CRON, PHASE_TOOLS, PHASE_AUDIT, PHASE_CLOSE]
    else:
        phases = ALL_PHASES

    diag = DiagRunner()

    if PHASE_ENV in phases:
        phase_environment(diag, workspace, suite_paths)

    if PHASE_CRON in phases:
        phase_cron_config(diag, workspace, suite_name, args.cron_file)

    if PHASE_TOOLS in phases:
        phase_tool_connectivity(diag, workspace, animal)

    if PHASE_AUDIT in phases:
        phase_source_audit(diag, workspace, suite_paths, suite_name)

    if PHASE_SCRIPTS in phases:
        phase_script_execution(diag, workspace, suite_paths,
                               suite_name, animal)

    if PHASE_CLOSE in phases:
        phase_close_path(diag, workspace, animal)

    if PHASE_STATE in phases:
        phase_state_lifecycle(diag, workspace, suite_paths, animal)

    # Output
    if args.json:
        print(diag.to_json(suite=suite_name, animal=animal_name))
    else:
        print(diag.to_human(suite=suite_name, animal=animal_name))

    sys.exit(1 if diag.critical_failures > 0 else 0)


if __name__ == "__main__":
    main()
