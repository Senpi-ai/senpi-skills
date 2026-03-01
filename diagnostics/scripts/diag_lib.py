#!/usr/bin/env python3
"""Senpi Diagnostics — discovery engine, test registry, and helpers.

Provides:
- Test registry with phase grouping and critical/non-critical classification
- Cron config parser and Senpi-related job discovery
- Suite/animal resolver using runtime config
- mcporter call helper (mirrors tiger_config pattern)
- Output formatter (JSON + human-readable)
"""

import glob
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


# ─── Defaults ───────────────────────────────────────────────────

DEFAULT_WORKSPACE = "/home/arnold/.openclaw/workspace"
DEFAULT_PY = "/home/arnold/openclaw/venv/bin/python"
DEFAULT_CRON_FILE = os.path.expanduser("~/.openclaw/cron/jobs.json")

SENPI_NAME_PATTERNS = [
    "TIGER", "WOLF", "LION", "VIPER",
    "DSL", "Scanner", "Prescreener", "OI Tracker",
    "Goal Engine", "Risk Guardian", "Exit Checker",
    "Portfolio", "Emerging Movers", "SM Flip",
    "Health Check", "Senpi",
]


# ─── Test Registry ──────────────────────────────────────────────

class TestResult:
    __slots__ = ("name", "phase", "passed", "critical", "detail",
                 "duration_ms", "remediation")

    def __init__(self, name, phase, passed, critical=True,
                 detail="", duration_ms=0, remediation=""):
        self.name = name
        self.phase = phase
        self.passed = passed
        self.critical = critical
        self.detail = detail
        self.duration_ms = duration_ms
        self.remediation = remediation

    def to_dict(self):
        d = {
            "test": self.name,
            "phase": self.phase,
            "passed": self.passed,
            "critical": self.critical,
            "detail": self.detail,
            "duration_ms": self.duration_ms,
        }
        if self.remediation:
            d["remediation"] = self.remediation
        return d


class DiagRunner:
    """Accumulates test results across phases."""

    def __init__(self):
        self.results: list[TestResult] = []
        self.start_time = time.time()

    def record(self, name, phase, passed, critical=True,
               detail="", duration_ms=0, remediation=""):
        self.results.append(TestResult(
            name=name, phase=phase, passed=passed, critical=critical,
            detail=detail, duration_ms=duration_ms, remediation=remediation,
        ))

    @property
    def critical_failures(self):
        return sum(1 for r in self.results if not r.passed and r.critical)

    @property
    def safe_to_trade(self):
        return self.critical_failures == 0

    def phase_summary(self):
        phases = {}
        for r in self.results:
            p = phases.setdefault(r.phase, {"name": r.phase, "passed": 0,
                                            "failed": 0, "skipped": 0, "tests": []})
            if r.passed:
                p["passed"] += 1
            else:
                p["failed"] += 1
            p["tests"].append(r.to_dict())
        return list(phases.values())

    def to_json(self, suite="", animal=""):
        total = len(self.results)
        passed = sum(1 for r in self.results if r.passed)
        failed = total - passed
        blockers = [r.detail or r.name for r in self.results
                    if not r.passed and r.critical]
        return json.dumps({
            "suite": suite,
            "animal": animal,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "safe_to_trade": self.safe_to_trade,
            "phases": self.phase_summary(),
            "summary": {
                "total": total,
                "passed": passed,
                "failed": failed,
                "critical_failures": self.critical_failures,
                "duration_ms": int((time.time() - self.start_time) * 1000),
                "blockers": blockers,
            },
        }, indent=2)

    def to_human(self, suite="", animal=""):
        lines = []
        w = 60
        lines.append("=" * w)
        lines.append("  SENPI DIAGNOSTICS SUITE")
        if suite:
            lines.append(f"  Suite: {suite}  Animal: {animal or 'default'}")
        lines.append("=" * w)
        lines.append("")

        current_phase = None
        for r in self.results:
            if r.phase != current_phase:
                current_phase = r.phase
                lines.append(f"--- {current_phase} ---")
                lines.append("")

            icon = "PASS" if r.passed else ("FAIL [CRITICAL]" if r.critical else "WARN")
            lines.append(f"  [{icon}] {r.name}")
            if r.detail:
                lines.append(f"         {r.detail}")
            if r.remediation and not r.passed:
                lines.append(f"         FIX: {r.remediation}")
            if r.duration_ms > 0:
                lines.append(f"         ({r.duration_ms}ms)")
            lines.append("")

        total = len(self.results)
        passed = sum(1 for r in self.results if r.passed)
        failed = total - passed
        dur = int((time.time() - self.start_time) * 1000)

        lines.append("=" * w)
        if self.critical_failures > 0:
            lines.append(f"  UNSAFE TO TRADE -- {self.critical_failures} critical failure(s)")
            for r in self.results:
                if not r.passed and r.critical:
                    lines.append(f"    - {r.name}: {r.detail}")
        else:
            lines.append(f"  SAFE TO TRADE -- {passed}/{total} passed")
        lines.append(f"  Total: {dur}ms")
        lines.append("=" * w)
        return "\n".join(lines)


# ─── Discovery ──────────────────────────────────────────────────

def resolve_workspace(workspace=None):
    return workspace or os.environ.get("WORKSPACE", DEFAULT_WORKSPACE)


def resolve_suite_config(workspace):
    """Load suite-branches.json and animals.json from runtime config."""
    runtime = os.path.join(workspace, "skills/senpi-trading/runtime/config")
    suites_file = os.path.join(runtime, "suite-branches.json")
    animals_file = os.path.join(runtime, "animals.json")

    suites = {}
    animals = {}
    try:
        with open(suites_file) as f:
            suites = json.load(f)
    except Exception:
        pass
    try:
        with open(animals_file) as f:
            animals = json.load(f)
    except Exception:
        pass
    return suites, animals


def resolve_suite_paths(workspace, suite_name):
    """Return key paths for a suite."""
    suites_cfg, _ = resolve_suite_config(workspace)
    suite = suites_cfg.get("suites", {}).get(suite_name, {})
    submodule_root = suites_cfg.get("submoduleRoot",
                                     "skills/senpi-trading/senpi-skills")
    suite_path = suite.get("suitePath", suite_name)
    full_suite = os.path.join(workspace, submodule_root, suite_path)
    scripts_dir = os.path.join(full_suite, "scripts")
    return {
        "submodule_root": os.path.join(workspace, submodule_root),
        "suite_dir": full_suite,
        "scripts_dir": scripts_dir,
        "branch": suite.get("branch", ""),
        "dsl_adapter": suite.get("adapters", {}).get("dsl", ""),
    }


def resolve_animal(workspace, animal_name=None):
    """Return config for a specific animal or the default."""
    _, animals_cfg = resolve_suite_config(workspace)
    if not animal_name:
        animal_name = animals_cfg.get("defaultAnimal", "")
    animal = animals_cfg.get("animals", {}).get(animal_name, {})
    return animal_name, animal


def discover_senpi_crons(cron_file=None):
    """Load cron jobs and filter to Senpi-related ones."""
    path = cron_file or DEFAULT_CRON_FILE
    try:
        with open(path) as f:
            data = json.load(f)
    except Exception:
        return [], path

    jobs = data.get("jobs", [])
    senpi_jobs = []
    for j in jobs:
        name = j.get("name", "")
        if any(pat.lower() in name.lower() for pat in SENPI_NAME_PATTERNS):
            senpi_jobs.append(j)
    return senpi_jobs, path


def load_cron_model_profiles(workspace):
    """Load the expected model profiles for cron jobs."""
    path = os.path.join(workspace,
                        "skills/senpi-trading/runtime/config/cron-model-profiles.json")
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {}


def find_suite_scripts(scripts_dir):
    """Return all .py files in a suite's scripts/ directory."""
    if not os.path.isdir(scripts_dir):
        return []
    return sorted(glob.glob(os.path.join(scripts_dir, "*.py")))


def find_dsl_state_files(workspace, animal):
    """Find all DSL state files for an animal."""
    strategy_id = animal.get("strategyId", "")
    if not strategy_id:
        return []
    state_dir = os.path.join(workspace, "state", strategy_id)
    if not os.path.isdir(state_dir):
        return []
    return sorted(glob.glob(os.path.join(state_dir, "dsl-*.json")))


# ─── Command Execution ─────────────────────────────────────────

def run_cmd(cmd, timeout=30, env=None):
    """Run a command, return (returncode, stdout, stderr)."""
    run_env = {**os.environ, "NPM_CONFIG_CACHE": "/tmp/npm-cache"}
    if env:
        run_env.update(env)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=timeout, env=run_env)
        return r.returncode, r.stdout, r.stderr
    except subprocess.TimeoutExpired:
        return -1, "", "TIMEOUT"
    except Exception as e:
        return -1, "", str(e)


def gate_call(workspace, tool, timeout=30, **kwargs):
    """Call a Senpi tool through the gate adapter handler.py."""
    py = os.environ.get("DIAG_PYTHON", DEFAULT_PY)
    gate = os.path.join(workspace, "skills/mcporter-gate/handler.py")
    cmd = [py, gate, "call", f"senpi.{tool}"]
    for k, v in kwargs.items():
        if isinstance(v, (list, dict)):
            cmd.append(f"{k}={json.dumps(v)}")
        elif isinstance(v, bool):
            cmd.append(f"{'true' if v else 'false'}")
        else:
            cmd.append(f"{k}={v}")
    return run_cmd(cmd, timeout=timeout)


def mcporter_call(workspace, tool, timeout=30, **kwargs):
    """Call a Senpi tool via MCPORTER_CMD (or PATH mcporter)."""
    mcporter_bin = os.environ.get("MCPORTER_CMD", "mcporter")
    cmd = [mcporter_bin, "call", f"senpi.{tool}"]
    for k, v in kwargs.items():
        if isinstance(v, (list, dict)):
            cmd.append(f"{k}={json.dumps(v)}")
        elif isinstance(v, bool):
            cmd.append(f"{'true' if v else 'false'}")
        else:
            cmd.append(f"{k}={v}")
    return run_cmd(cmd, timeout=timeout)


# ─── Source Code Audit Helpers ──────────────────────────────────

BARE_MCPORTER_PATTERN = re.compile(
    r"""subprocess\.\w+\(\s*\[?\s*["']mcporter["']""",
    re.MULTILINE,
)
MCPORTER_CMD_PATTERN = re.compile(
    r"""os\.environ\.get\(\s*["']MCPORTER_CMD["']""",
)
BARE_CURL_HL_PATTERN = re.compile(
    r"""["']https?://api\.hyperliquid\.xyz""",
)


def audit_script_source(filepath):
    """Check a script for bare mcporter calls and missing MCPORTER_CMD.

    Returns list of issue dicts: {kind, line, snippet}.
    """
    issues = []
    try:
        with open(filepath) as f:
            content = f.read()
            lines = content.splitlines()
    except Exception as e:
        return [{"kind": "read_error", "line": 0, "snippet": str(e)}]

    has_mcporter_cmd = bool(MCPORTER_CMD_PATTERN.search(content))

    for i, line in enumerate(lines, 1):
        if BARE_MCPORTER_PATTERN.search(line) and not has_mcporter_cmd:
            issues.append({
                "kind": "bare_mcporter",
                "line": i,
                "snippet": line.strip()[:120],
            })
        if BARE_CURL_HL_PATTERN.search(line):
            issues.append({
                "kind": "bare_curl_hyperliquid",
                "line": i,
                "snippet": line.strip()[:120],
            })

    return issues


# ─── State File Validation ──────────────────────────────────────

DSL_REQUIRED_FIELDS = [
    "active", "asset", "direction", "entryPrice", "leverage", "phase",
]
DSL_CLOSE_CRITICAL_FIELDS = ["wallet"]
DSL_TRACKING_FIELDS = ["highWaterPrice", "phase1"]


def validate_dsl_state(filepath, expected_wallet=""):
    """Validate a DSL state file and return list of issues."""
    issues = []
    try:
        with open(filepath) as f:
            state = json.load(f)
    except json.JSONDecodeError as e:
        return [f"corrupt JSON: {e}"]
    except Exception as e:
        return [f"read error: {e}"]

    if not state.get("active", False):
        return []  # inactive files don't need validation

    name = state.get("asset", os.path.basename(filepath))

    missing = [f for f in DSL_REQUIRED_FIELDS if f not in state]
    if missing:
        issues.append(f"{name}: missing required fields {missing}")

    wallet = state.get("wallet", state.get("strategyWallet", ""))
    if not wallet:
        issues.append(f"{name}: NO WALLET -- DSL cannot close this position")
    elif expected_wallet and wallet.lower() != expected_wallet.lower():
        issues.append(f"{name}: wallet mismatch "
                      f"(state={wallet[:10]}... vs config={expected_wallet[:10]}...)")

    if "highWaterPrice" not in state:
        issues.append(f"{name}: missing highWaterPrice -- DSL will KeyError")

    phase = state.get("phase", 1)
    if phase == 1 and "phase1" not in state:
        issues.append(f"{name}: phase=1 but no phase1 config block")
    if phase == 2 and "phase2" not in state:
        issues.append(f"{name}: phase=2 but no phase2 config block")

    tiers = state.get("tiers", [])
    for idx, tier in enumerate(tiers):
        trigger = tier.get("triggerPct", tier.get("triggerROE", 0))
        if trigger == 0:
            issues.append(f"{name}: tier[{idx}] has triggerPct=0 -- fires immediately")

    return issues
