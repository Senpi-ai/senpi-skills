"""install_strategy / uninstall_strategy — host-side deploy orchestrator.

Deploys a strategy PACKAGE (scanner.py + runtime.yaml(s) + strategy.yaml) using
WALLET ADDRESSES THE AGENT HAS ALREADY CREATED. This CLI never creates or funds
wallets — strategy-wallet creation is an async, funded, multi-step MCP lifecycle
(`strategy_create_custom_strategy` → CREATE_WALLET → FUND_WALLET → ACTIVE) that the
conversational agent performs (with user confirmation, polling `strategy_list` until
ACTIVE) before calling this. Here we only:

  render runtime.yaml   -> ${VAR} substitution
  runtime create        -> subprocess to the runtime engine CLI
  launch scanner daemon -> manage.relaunch_daemon (scanner self-registers pid/boot/heartbeat)
  verify liveness       -> SenpiClient.is_runtime_registered / is_scanner_registered
  record                -> ledger (idempotency / teardown)

Reuses the (tested) senpi_runtime_helpers primitives; the only cross-layer call is
`openclaw senpi runtime create/delete`.
"""
# Copyright 2026 Senpi (https://senpi.ai) — MIT
import os
import re
import shlex
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import ledger as _ledger
from . import manage as _manage
from . import state as _state
from .client import SenpiClient
from .strategy import load_manifest

_VAR = re.compile(r"\$\{([A-Z0-9_]+)\}")


class WalletRequired(RuntimeError):
    """Raised when an instance has no ready wallet address. The agent must create
    the wallet via MCP `strategy_create_custom_strategy` and pass the address."""


class DecisionModelRequired(RuntimeError):
    """Raised when no decision_model is resolvable for an instance — rendering an
    empty model into runtime.yaml would fail `runtime create` (REQUIRED, bare name)."""


# ─── runtime.yaml rendering ──────────────────────────────────────────────────

def render_runtime(text: str, subs: Dict[str, str]) -> Tuple[str, Optional[str]]:
    """Substitute ${VAR} from `subs` (unknown vars left as-is) and extract the
    runtime's top-level `name:` as its runtime_id."""
    rendered = _VAR.sub(lambda m: str(subs.get(m.group(1), m.group(0))), text)
    runtime_id = None
    for line in text.splitlines():
        m = re.match(r'^name:\s*["\']?([^"\'#]+?)["\']?\s*(#.*)?$', line)
        if m:
            runtime_id = m.group(1).strip()
            break
    return rendered, runtime_id


# ─── runtime engine CLI (the one place we shell out to openclaw) ─────────────

def _runtime_cli() -> List[str]:
    return shlex.split(os.environ.get("SENPI_RUNTIME_CLI", "openclaw senpi"))


def runtime_create(rendered_path: str, timeout: float = 120.0) -> Tuple[bool, str]:
    cmd = _runtime_cli() + ["runtime", "create", "--path", str(rendered_path)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode == 0, (r.stdout or r.stderr or "").strip()
    except (OSError, subprocess.SubprocessError) as e:
        return False, f"{type(e).__name__}: {e}"


def runtime_delete(runtime_id: str, timeout: float = 60.0) -> Tuple[bool, str]:
    cmd = _runtime_cli() + ["runtime", "delete", "--id", runtime_id]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode == 0, (r.stdout or r.stderr or "").strip()
    except (OSError, subprocess.SubprocessError) as e:
        return False, f"{type(e).__name__}: {e}"


# ─── wallet resolution (NO creation — addresses come from the agent) ─────────

def resolve_wallet(wallets: Dict[str, str], instance_name: str, sid: str) -> str:
    addr = wallets.get(instance_name, wallets.get("*"))
    if not addr or addr == "new":
        raise WalletRequired(
            f"no wallet address for instance '{instance_name}'. Create it first via "
            f"MCP strategy_create_custom_strategy(initialBudget>=100, positions=[], "
            f"skillName='{sid}', skillVersion=<version>), wait until status ACTIVE "
            f"(poll strategy_list by strategyId, read strategyWalletAddress), then pass "
            f"--wallet {instance_name}=0x..."
        )
    return addr


# ─── daemon launch ───────────────────────────────────────────────────────────

def _stop_daemons_for_scanner(scanner_name: str, log) -> List[Tuple[str, str]]:
    """Stop any running daemon registered under `scanner_name` (live state). Used on
    reinstall so the new scanner can acquire the scanner_lock the old one holds."""
    stopped: List[Tuple[str, str]] = []
    for dname in _state.list_daemons():
        pid = _state.read_pid(dname)
        if pid and pid.get("scanner") == scanner_name and pid.get("pid"):
            out = _manage.stop_pid(int(pid["pid"]))
            stopped.append((dname, out.get("outcome", "?")))
    return stopped


def launch_scanner(pkg: Path, instance: Dict[str, Any], env: Dict[str, str],
                   log, timeout: float = 25.0) -> Tuple[Optional[str], Optional[str]]:
    """Spawn the scanner; it self-registers pid.json via producer_daemon. Find the
    daemon by (wallet, scanner) match, or — robustly — a single newly-appeared one."""
    scn = instance["scanner"]
    scanner_path = pkg / "scripts" / scn.get("entrypoint", "scanner.py")
    argv = [sys.executable, "-u", str(scanner_path)]
    log_path = f"/tmp/{pkg.name}-{instance['name']}-scanner.log"
    full_env = dict(os.environ)
    full_env.update(env)

    before = set(_state.list_daemons())
    res = _manage.relaunch_daemon(
        argv=argv, cwd=str(pkg), log_path=log_path,
        env=full_env, script_path=str(scanner_path),
    )
    if res.get("outcome") != _manage.RELAUNCH_OK:
        return None, res.get("error") or res.get("outcome")

    wallet = env.get(instance["wallet_env"])
    scanner_name = scn.get("name")
    deadline = time.time() + timeout
    while time.time() < deadline:
        new_alive = []
        for name in _state.list_daemons():
            pid = _state.read_pid(name)
            if not pid:
                continue
            if pid.get("wallet") == wallet and pid.get("scanner") == scanner_name:
                return name, None  # exact match — best
            if name not in before:
                new_alive.append(name)
        if len(new_alive) == 1:
            return new_alive[0], None  # exactly one daemon appeared — it's ours
        time.sleep(1.0)
    return None, "scanner did not register pid.json within timeout"


def verify_instance(client: SenpiClient, wallet: str, scanner_name: str) -> str:
    """Post-launch REGISTRATION check — runtime + scanner are registered for the
    wallet. This does NOT confirm the scanner has ticked (runCount > 0); the agent
    must run the monitor/liveness workflow to declare the strategy truly live.
    Returns 'registered' or 'degraded'."""
    try:
        if client.is_runtime_registered(wallet) and client.is_scanner_registered(wallet, scanner_name):
            return "registered"
    except Exception:
        pass
    return "degraded"


# ─── orchestration ───────────────────────────────────────────────────────────

def _resolve_wallets(wallets) -> Dict[str, str]:
    """Normalize wallet arg(s) into {instance_name|'*': value}."""
    out: Dict[str, str] = {}
    if wallets is None:
        return {}
    if isinstance(wallets, str):
        return {"*": wallets}
    for w in wallets:
        if "=" in w:
            k, v = w.split("=", 1)
            out[k] = v
        else:
            out["*"] = w
    return out


def plan(package_dir: str, wallets=None, budget: Optional[float] = None) -> Dict[str, Any]:
    """Dry-run: the per-instance intended deploy actions, no side effects."""
    pkg = Path(package_dir).resolve()
    man = load_manifest(str(pkg / "strategy.yaml"))
    w = _resolve_wallets(wallets)
    insts = man["instances"]
    out = {"id": man["id"], "version": man["version"], "dry_run": True,
           "budget": budget, "instances": []}
    for inst in insts:
        share = inst.get("funding_share", 1.0 / max(1, len(insts)))
        addr = w.get(inst["name"], w.get("*", "<provision via MCP>"))
        out["instances"].append({
            "name": inst["name"],
            "budget": round(budget * share, 2) if budget else None,
            "wallet": addr,
            "wallet_env": inst["wallet_env"],
            "runtime": inst["runtime"],
            "scanner": inst["scanner"]["name"],
            "env": {**{inst["wallet_env"]: addr},
                    **{k: str(v) for k, v in (inst.get("env") or {}).items()}},
        })
    return out


def install_strategy(package_dir: str, wallets=None,
                     decision_model: Optional[str] = None,
                     telegram_chat_id: Optional[str] = None,
                     reinstall: bool = False, dry_run: bool = False,
                     budget: Optional[float] = None, log=print) -> Dict[str, Any]:
    pkg = Path(package_dir).resolve()
    if dry_run:
        return plan(package_dir, wallets, budget)

    man = load_manifest(str(pkg / "strategy.yaml"))
    sid, ver = man["id"], man["version"]
    instances = man["instances"]
    defaults = man.get("defaults", {})
    w = _resolve_wallets(wallets)
    model_env = defaults.get("decision_model_env", "DECISION_MODEL")
    tg_env = defaults.get("telegram_chat_id_env", "TELEGRAM_CHAT_ID")
    auth_env = defaults.get("auth_token_env", "SENPI_AUTH_TOKEN")

    # B4: the spawned scanner daemon needs Senpi auth (it inherits our env);
    # fail fast with a clear message instead of a silent daemon death in its log.
    # The SDK currently reads SENPI_AUTH_TOKEN, but strategy.yaml may name an
    # auth_token_env for operator docs/launch rendering. If a custom name is set,
    # mirror it into SENPI_AUTH_TOKEN for the child process.
    auth_value = (os.environ.get(auth_env, "") or os.environ.get("SENPI_AUTH_TOKEN", "")).strip()
    if not auth_value:
        return {"strategy": sid, "version": ver, "status": "failed",
                "error": f"{auth_env} is not set in the install environment — the scanner "
                         "daemon (and liveness checks) need Senpi auth. Export it before "
                         "running install.",
                "instances": [],
                "attribution": {"skillName": sid, "skillVersion": ver}}

    if len(instances) > 1:
        names = [i["name"] for i in instances]
        if "*" in w:
            return {"strategy": sid, "version": ver, "status": "failed",
                    "error": "multi-instance strategies require one named wallet per instance "
                             f"({', '.join(names)}). Pass --wallet name=0x... for each instance; "
                             "do not reuse one wildcard wallet.",
                    "instances": [],
                    "attribution": {"skillName": sid, "skillVersion": ver}}
        missing = [n for n in names if n not in w]
        if missing:
            return {"strategy": sid, "version": ver, "status": "failed",
                    "error": "missing wallet address for instance(s): "
                             f"{', '.join(missing)}. Pass --wallet name=0x... for each instance.",
                    "instances": [],
                    "attribution": {"skillName": sid, "skillVersion": ver}}
        lowered = [w[n].lower() for n in names]
        if len(set(lowered)) != len(lowered):
            return {"strategy": sid, "version": ver, "status": "failed",
                    "error": "multi-instance strategies require distinct wallet addresses per instance.",
                    "instances": [],
                    "attribution": {"skillName": sid, "skillVersion": ver}}

    client = SenpiClient(auth_token=auth_value)
    report = {"strategy": sid, "version": ver, "status": "registered", "instances": [],
              "attribution": {"skillName": sid, "skillVersion": ver}}

    for inst in instances:
        name = inst["name"]
        ir: Dict[str, Any] = {"name": name, "phase": None, "status": "failed"}
        try:
            # a. wallet (provided by the agent — never created here)
            wallet = resolve_wallet(w, name, sid)
            ir["wallet"] = wallet
            ir["phase"] = "wallet_ready"
            _ledger.write_entry(sid, name, wallet=wallet, phase="wallet_ready")

            # B8: idempotency from LIVE state (not the ledger — it's ephemeral). If a
            # runtime already exists for this wallet and we're not reinstalling, skip.
            if not reinstall:
                try:
                    already = client.is_runtime_registered(wallet)
                except Exception:
                    already = False  # can't reach /state → proceed and let runtime create decide
                if already:
                    ir["phase"] = "runtime_created"
                    ir["status"] = "already_installed"
                    log(f"[install] {sid}/{name}: runtime already exists for {wallet} — "
                        f"skipping (use --reinstall to redeploy)")
                    report["instances"].append(ir)
                    continue

            # B2: decision_model required — don't render an empty model into runtime.yaml.
            resolved_model = (decision_model or os.environ.get(model_env, "")).strip()
            if not resolved_model:
                raise DecisionModelRequired(
                    f"decision_model required for instance '{name}': pass "
                    f"--decision-model <bare-model> (no provider prefix) or set {model_env}")

            # b. runtime render + create
            subs = {
                inst["wallet_env"]: wallet,
                model_env: resolved_model,
                tg_env: telegram_chat_id or os.environ.get(tg_env, ""),
            }
            rendered, rid = render_runtime((pkg / inst["runtime"]).read_text(), subs)
            fd, tmp = tempfile.mkstemp(prefix=f"{sid}-{name}-", suffix=".yaml")
            with os.fdopen(fd, "w") as f:
                f.write(rendered)
            try:
                if reinstall:
                    # stop the old daemon FIRST (so the new scanner can take the lock),
                    # then delete the old runtime, then recreate.
                    for dn, oc in _stop_daemons_for_scanner(inst["scanner"]["name"], log):
                        log(f"[install] {sid}/{name}: stopped old daemon {dn} ({oc})")
                    if rid:
                        runtime_delete(rid)
                ok, msg = runtime_create(tmp)
            finally:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
            if not ok:
                raise RuntimeError(f"runtime create failed: {msg}")
            ir["runtime_id"] = rid
            ir["phase"] = "runtime_created"
            _ledger.write_entry(sid, name, runtime_id=rid, phase="runtime_created")

            # c. daemon
            env = {inst["wallet_env"]: wallet, auth_env: auth_value, "SENPI_AUTH_TOKEN": auth_value}
            env.update({k: str(v) for k, v in (inst.get("env") or {}).items()})
            if decision_model:
                env[model_env] = decision_model
            daemon, derr = launch_scanner(pkg, inst, env, log)
            if not daemon:
                raise RuntimeError(f"daemon launch failed: {derr}")
            ir["daemon"] = daemon
            ir["phase"] = "daemon_launched"
            _ledger.write_entry(sid, name, daemon=daemon, phase="daemon_launched")

            # d. verify (REGISTRATION only — not a tick check; agent runs monitor next)
            status = verify_instance(client, wallet, inst["scanner"]["name"])
            ir["status"] = status
            if status == "registered":
                ir["phase"] = "verified"
                _ledger.write_entry(sid, name, phase="verified")
        except WalletRequired as e:
            ir["error"] = str(e)
            ir["status"] = "wallet_required"
            log(f"[install] {sid}/{name}: {e}")
        except DecisionModelRequired as e:
            ir["error"] = str(e)
            ir["status"] = "decision_model_required"
            log(f"[install] {sid}/{name}: {e}")
        except Exception as e:  # noqa: BLE001
            ir["error"] = str(e)
            ir["status"] = "failed"
            log(f"[install] {sid}/{name} FAILED at {ir['phase']}: {e}")
        report["instances"].append(ir)

    statuses = [i["status"] for i in report["instances"]]
    _OK = {"registered", "already_installed"}
    _BAD = {"failed", "wallet_required", "decision_model_required"}
    report["status"] = ("registered" if all(s in _OK for s in statuses)
                        else "failed" if all(s in _BAD for s in statuses)
                        else "degraded")
    return report


def uninstall_strategy(package_dir: str, instance: Optional[str] = None,
                       log=print) -> Dict[str, Any]:
    """Tear down a deployed strategy from the PACKAGE + LIVE STATE — ledger-free
    (the ledger is install-time-only and may be gone). For each instance:
      - runtime_id = the runtime.yaml top-level `name:` (static) -> runtime delete
      - daemon = the live daemon whose pid.json.scanner == the instance scanner name
        -> stop_pid (wallet read from that pid.json)
    """
    p = Path(package_dir).resolve()
    man_path = p / "strategy.yaml"
    if not man_path.is_file():
        return {"strategy": str(package_dir), "status": "not_found",
                "error": "pass the strategy PACKAGE directory (containing strategy.yaml); "
                         "uninstall derives teardown from the package + live daemons, not a ledger.",
                "instances": []}
    man = load_manifest(str(man_path))
    sid = man["id"]
    instances = [i for i in man["instances"] if instance is None or i["name"] == instance]

    # index live daemons by the scanner name they registered in pid.json
    by_scanner: Dict[str, List[Tuple[str, Dict[str, Any]]]] = {}
    for dname in _state.list_daemons():
        pid = _state.read_pid(dname)
        if pid and pid.get("scanner"):
            by_scanner.setdefault(pid["scanner"], []).append((dname, pid))

    report = {"strategy": sid, "status": "ok", "instances": []}
    for inst in instances:
        name = inst["name"]
        r: Dict[str, Any] = {"name": name, "status": "ok"}
        scanner_name = inst["scanner"]["name"]

        # 1. stop the scanner daemon(s) for this instance
        stopped = []
        for dname, pid in by_scanner.get(scanner_name, []):
            r.setdefault("wallet", pid.get("wallet"))
            if pid.get("pid"):
                out = _manage.stop_pid(int(pid["pid"]))
                stopped.append({"daemon": dname, "outcome": out.get("outcome")})
        r["daemons_stopped"] = stopped or "none running"

        # 2. delete the runtime (runtime_id = the runtime.yaml top-level name, static)
        try:
            _, rid = render_runtime((p / inst["runtime"]).read_text(), {})
        except Exception:
            rid = None
        r["runtime_id"] = rid
        if rid:
            ok, msg = runtime_delete(rid)
            if not ok:
                r["status"] = "failed"
                r["error"] = msg
        else:
            r["status"] = "failed"
            r["error"] = "could not read runtime_id (name:) from runtime.yaml"

        # 3. best-effort ledger cleanup (ephemeral; ignore if absent)
        try:
            _ledger.remove_entry(sid, name)
        except Exception:
            pass
        log(f"[uninstall] {sid}/{name}: {r['status']}")
        report["instances"].append(r)

    if any(i["status"] != "ok" for i in report["instances"]):
        report["status"] = "degraded"
    return report
