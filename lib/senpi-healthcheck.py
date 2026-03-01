#!/usr/bin/env python3
"""
senpi-healthcheck.py — Generic self-healing DSL health check for any Senpi skill.

Works with any skill that provides a get_healthcheck_adapter() (or extends
get_lifecycle_adapter()) in its config module.

Checks per instance / strategy:
  - Every on-chain position has an active, correctly-directed DSL
  - No orphan DSLs (active DSL with no matching position)
  - DSL size/entry/leverage match on-chain values
  - DSLs are being checked recently (not stale)

Auto-heals where safe, alerts where not.

Usage:
  python3 senpi-healthcheck.py --skill wolf --config-dir wolf-strategy/scripts
  python3 senpi-healthcheck.py --skill tiger --config-dir tiger/scripts
  python3 senpi-healthcheck.py --skill wolf --config-dir wolf-strategy/scripts \
      --strategy wolf-abc123
"""

import argparse
import importlib.util
import json
import os
import sys
from datetime import datetime, timezone

LIB_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, LIB_DIR)

from senpi_state.healthcheck import check_instance


def _import_config(skill, config_dir):
    """Dynamically import {skill}_config from config_dir."""
    module_name = f"{skill}_config"
    module_path = os.path.join(config_dir, f"{module_name}.py")

    if not os.path.isfile(module_path):
        return None, f"{module_name}.py not found in {config_dir}"

    spec = importlib.util.spec_from_file_location(module_name, module_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod

    if config_dir not in sys.path:
        sys.path.insert(0, config_dir)

    spec.loader.exec_module(mod)
    return mod, None


def _run_single(adapter, stale_minutes):
    """Run health check for one instance and return its result dict."""
    issues, positions, active_dsl = check_instance(
        wallet=adapter["wallet"],
        instance_key=adapter["instance_key"],
        dsl_glob_pattern=adapter["dsl_glob"],
        dsl_state_path_fn=adapter["dsl_state_path"],
        create_dsl_fn=adapter.get("create_dsl"),
        tiers=adapter.get("tiers"),
        stale_minutes=stale_minutes,
    )
    return {
        "instance_key": adapter["instance_key"],
        "positions": positions,
        "active_dsl": active_dsl,
        "issues": issues,
        "issue_count": len(issues),
        "critical_count": sum(1 for i in issues if i["level"] == "CRITICAL"),
    }


def main():
    parser = argparse.ArgumentParser(
        description="Senpi generic DSL health check")
    parser.add_argument("--skill", required=True,
                        help="Skill name (tiger, wolf, lion, viper)")
    parser.add_argument("--config-dir", required=True,
                        help="Path to directory containing {skill}_config.py")
    parser.add_argument("--strategy", default=None,
                        help="Strategy key (for multi-strategy skills like wolf). "
                             "If omitted, checks all strategies/instances.")
    parser.add_argument("--stale-minutes", type=float, default=10,
                        help="Alert if DSL not checked in N minutes (default 10)")
    args = parser.parse_args()

    config_dir = os.path.abspath(args.config_dir)
    mod, err = _import_config(args.skill, config_dir)
    if err:
        print(json.dumps({"status": "error", "error": err}))
        return

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Prefer get_healthcheck_adapter if the skill provides it; fall back to
    # get_lifecycle_adapter with healthcheck-specific keys.
    adapter_fn = getattr(mod, "get_healthcheck_adapter", None)
    if adapter_fn is None:
        adapter_fn = getattr(mod, "get_lifecycle_adapter", None)
    if adapter_fn is None:
        print(json.dumps({
            "status": "error",
            "error": f"{args.skill}_config has neither "
                     f"get_healthcheck_adapter() nor get_lifecycle_adapter()",
        }))
        return

    # Multi-instance: if the config module exposes list_instances(), iterate
    # all of them.  Otherwise run for the single adapter returned.
    list_fn = getattr(mod, "list_instances", None)

    adapters = []
    if args.strategy:
        adapters.append(adapter_fn(strategy_key=args.strategy))
    elif list_fn:
        for key in list_fn():
            adapters.append(adapter_fn(strategy_key=key))
    else:
        adapters.append(adapter_fn())

    all_issues = []
    instance_results = {}

    for adapter in adapters:
        result = _run_single(adapter, args.stale_minutes)
        instance_results[result["instance_key"]] = result
        all_issues.extend(result["issues"])

    output = {
        "status": "ok" if not any(
            i["level"] == "CRITICAL" for i in all_issues) else "critical",
        "time": now,
        "skill": args.skill,
        "instances": instance_results,
        "issues": all_issues,
        "issue_count": len(all_issues),
        "critical_count": sum(
            1 for i in all_issues if i["level"] == "CRITICAL"),
    }
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
