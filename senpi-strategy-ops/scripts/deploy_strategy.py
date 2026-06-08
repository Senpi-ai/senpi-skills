#!/usr/bin/env python3
"""Deploy PLANNER for a strategy package.

Reads a package's `strategy.yaml` and emits the deterministic, per-instance deploy
plan — wallet step, runtime-create command (with the env it needs), and the
scanner-daemon launch command with its exact env.

SAFE BY DESIGN: planner/renderer only. It does NOT create wallets (that's an agent
MCP step), does NOT run any command, and does NOT touch the network. It prints what
to run. The single-command `senpi-helpers install` does the same deterministically.

Usage:
  python3 deploy_strategy.py <package-dir> [--budget 1000]
                             [--wallet 0x..|name=ADDR ...] [--decision-model <bare-model>] [--json]
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import argparse
import json
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("PyYAML required: pip install pyyaml")


class BadPackage(Exception):
    pass


def _req(d, key, ctx):
    if not isinstance(d, dict) or key not in d or d[key] in (None, ""):
        raise BadPackage(f"{ctx}: missing required field '{key}'")
    return d[key]


def build_plan(pkg: Path, budget, wallets: dict, model: str) -> dict:
    try:
        man = yaml.safe_load((pkg / "strategy.yaml").read_text()) or {}
    except yaml.YAMLError as e:
        raise BadPackage(f"strategy.yaml is not valid YAML: {e}")
    if not isinstance(man, dict):
        raise BadPackage("strategy.yaml did not parse to a mapping")
    sid = _req(man, "id", "strategy.yaml")
    ver = _req(man, "version", "strategy.yaml")
    instances = man.get("instances")
    if not isinstance(instances, list) or not instances:
        raise BadPackage("strategy.yaml: 'instances' must be a non-empty list")

    defaults = man.get("defaults") or {}
    auth_env = defaults.get("auth_token_env", "SENPI_AUTH_TOKEN")
    model_env = defaults.get("decision_model_env", "DECISION_MODEL")
    tg_env = defaults.get("telegram_chat_id_env", "TELEGRAM_CHAT_ID")

    steps = []
    for idx, inst in enumerate(instances):
        ctx = f"instances[{idx}]"
        name = _req(inst, "name", ctx)
        runtime = _req(inst, "runtime", ctx)
        wenv = _req(inst, "wallet_env", ctx)
        scn = inst.get("scanner") or {}
        scanner_name = _req(scn, "name", f"{ctx}.scanner")
        entrypoint = scn.get("entrypoint", "scanner.py")

        share = inst.get("funding_share", 1.0 / len(instances))
        sub_budget = round(budget * share, 2) if budget else None
        wallet = wallets.get(name, wallets.get("*", "new"))
        addr = wallet if wallet != "new" else "<NEW_WALLET_ADDR>"
        model_val = model or "<bare-model-name>"

        # env the runtime engine needs (it resolves ${...} from env at create time)
        runtime_env = {wenv: addr, model_env: model_val, tg_env: "<telegram-chat-id>"}
        # env the scanner daemon needs
        daemon_env = {wenv: addr, auth_env: "<SENPI_AUTH_TOKEN>", model_env: model_val}
        for k, v in (inst.get("env") or {}).items():
            daemon_env[k] = str(v)

        budget_note = f"initialBudget={sub_budget}" if sub_budget is not None else "initialBudget=<min $100>"
        steps.append({
            "instance": name,
            "budget": sub_budget,
            "wallet": wallet,
            "wallet_env": wenv,
            "runtime": runtime,
            "scanner_name": scanner_name,
            "entrypoint": entrypoint,
            "signal_type": scn.get("signal_type"),
            "tick_seconds": inst.get("tick_seconds"),
            "wallet_action": (
                f"AGENT (MCP): create NEW — strategy_create_custom_strategy({budget_note}, positions=[], "
                f"skillName=\"{sid}\", skillVersion=\"{ver}\") -> wait status ACTIVE "
                f"(poll strategy_list by strategyId; read strategyWalletAddress). "
                f"OR use an EXISTING strategy wallet (with user consent; find via strategy_list). "
                f"CONFIRM with user first; min $100 per new wallet"
            ) if wallet == "new" else f"use wallet {wallet}",
            "runtime_cmd": (
                "  ".join(f"{k}={v}" for k, v in runtime_env.items())
                + f"  openclaw senpi runtime create --path {pkg.name}/{runtime}"
            ),
            "daemon_cmd": (
                "  ".join(f"{k}={v}" for k, v in daemon_env.items())
                + f"  nohup python3 -u {pkg.name}/scripts/{entrypoint}"
                + f" > /tmp/{sid}-{name}-scanner.log 2>&1 & disown"
            ),
        })
    return {"id": sid, "version": ver, "budget": budget, "instances": steps}


def render(plan: dict) -> str:
    L = [f"# Deploy plan: {plan['id']} v{plan['version']}  "
         f"({len(plan['instances'])} instance(s))", ""]
    for s in plan["instances"]:
        b = f"${s['budget']:g}" if s["budget"] is not None else "$?"
        tick = s["tick_seconds"] if s["tick_seconds"] is not None else "?"
        L += [
            f"## instance: {s['instance']}  ({b}, {tick}s)",
            f"  1. wallet : {s['wallet_action']}",
            f"  2. runtime: {s['runtime_cmd']}",
            f"             (binds ${{{s['wallet_env']}}}; scanner '{s['scanner_name']}' → {s['signal_type']})",
            f"  3. daemon : {s['daemon_cmd']}",
            f"  4. verify : senpi-helpers health <daemon>  +  runtime ticking ({s['scanner_name']})",
            "",
        ]
    L += ["# Teardown (reverse): senpi-helpers stop <daemon> → verify flat → "
          "openclaw senpi runtime delete --id <runtime.yaml name> "
          "(senpi-helpers uninstall <pkg> does this)"]
    return "\n".join(L)


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("package")
    ap.add_argument("--budget", type=float, default=None,
                    help="Optional total USDC, for the budget-split display only.")
    ap.add_argument("--wallet", action="append", default=[],
                    help="'new', an address, or name=ADDR (repeatable for multi-instance)")
    ap.add_argument("--decision-model", default=None)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv[1:])

    wallets = {}
    for w in (a.wallet or ["new"]):
        if "=" in w:
            k, v = w.split("=", 1)
            wallets[k] = v
        else:
            wallets["*"] = w

    pkg = Path(a.package).resolve()
    if not (pkg / "strategy.yaml").is_file():
        sys.exit(f"error: no strategy.yaml in {pkg} (pass a strategy PACKAGE directory)")
    try:
        plan = build_plan(pkg, a.budget, wallets, a.decision_model)
    except BadPackage as e:
        sys.exit(f"error: {e}")
    print(json.dumps(plan, indent=2) if a.json else render(plan))


if __name__ == "__main__":
    main(sys.argv)
