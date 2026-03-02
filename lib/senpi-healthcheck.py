#!/usr/bin/env python3
"""
senpi-healthcheck.py — Generic self-healing health check for any Senpi skill.

Works with any skill that provides a get_healthcheck_adapter() (or extends
get_lifecycle_adapter()) in its config module.

Modes:
  Default (DSL-only):
    - Every on-chain position has an active, correctly-directed DSL
    - No orphan DSLs (active DSL with no matching position)
    - DSL size/entry/leverage match on-chain values
    - DSLs are being checked recently (not stale)

  --reconcile-state (State Doctor):
    All of the above, PLUS:
    - activePositions reconciled against on-chain data
    - Slot count verification
    - Margin utilization monitoring with configurable auto-downsize
    - Liquidation proximity checks

Usage:
  python3 senpi-healthcheck.py --skill tiger --config-dir tiger/scripts
  python3 senpi-healthcheck.py --skill tiger --config-dir tiger/scripts \\
      --reconcile-state --margin-warn 70 --margin-critical 85
  python3 senpi-healthcheck.py --skill wolf --config-dir wolf-strategy/scripts \\
      --strategy wolf-abc123 --reconcile-state --no-auto-downsize
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
from senpi_state.state_doctor import MarginConfig, reconcile_state


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
    """Run DSL-only health check for one instance."""
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


def _run_state_doctor(adapter, stale_minutes, margin_config):
    """Run full state reconciliation + margin safety for one instance."""
    return reconcile_state(
        wallet=adapter["wallet"],
        instance_key=adapter["instance_key"],
        load_state=adapter["load_state"],
        save_state=adapter["save_state"],
        max_slots=adapter.get("max_slots", 3),
        dsl_glob_pattern=adapter["dsl_glob"],
        dsl_state_path_fn=adapter["dsl_state_path"],
        create_dsl_fn=adapter.get("create_dsl"),
        tiers=adapter.get("tiers"),
        stale_minutes=stale_minutes,
        margin_config=margin_config,
    )


def main():
    parser = argparse.ArgumentParser(
        description="Senpi generic health check + state doctor")
    parser.add_argument("--skill", required=True,
                        help="Skill name (tiger, wolf, lion, viper)")
    parser.add_argument("--config-dir", required=True,
                        help="Path to directory containing {skill}_config.py")
    parser.add_argument("--strategy", default=None,
                        help="Strategy key (for multi-strategy skills like wolf)")
    parser.add_argument("--stale-minutes", type=float, default=10,
                        help="Alert if DSL not checked in N minutes (default 10)")

    parser.add_argument("--reconcile-state", action="store_true",
                        help="Enable full state reconciliation + margin safety")
    parser.add_argument("--margin-warn", type=float, default=70,
                        help="Margin utilization warning %% (default 70)")
    parser.add_argument("--margin-critical", type=float, default=85,
                        help="Margin utilization auto-downsize trigger %% (default 85)")
    parser.add_argument("--margin-target", type=float, default=60,
                        help="Target utilization after downsize %% (default 60)")
    parser.add_argument("--liq-warn", type=float, default=30,
                        help="Liquidation distance warning %% (default 30)")
    parser.add_argument("--liq-critical", type=float, default=15,
                        help="Liquidation distance auto-downsize trigger %% (default 15)")
    parser.add_argument("--no-auto-downsize", action="store_true",
                        help="Disable auto-downsizing (alert-only mode)")
    parser.add_argument("--downsize-pct", type=float, default=25,
                        help="Fallback position reduction %% (default 25)")
    args = parser.parse_args()

    config_dir = os.path.abspath(args.config_dir)
    mod, err = _import_config(args.skill, config_dir)
    if err:
        print(json.dumps({"status": "error", "error": err}))
        return

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    if args.reconcile_state:
        adapter_fn = getattr(mod, "get_lifecycle_adapter", None)
        if adapter_fn is None:
            print(json.dumps({
                "status": "error",
                "error": f"{args.skill}_config has no get_lifecycle_adapter() "
                         f"(required for --reconcile-state)",
            }))
            return
    else:
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

    list_fn = getattr(mod, "list_instances", None)

    adapters = []
    if args.strategy:
        adapters.append(adapter_fn(strategy_key=args.strategy))
    elif list_fn:
        for key in list_fn():
            adapters.append(adapter_fn(strategy_key=key))
    else:
        adapters.append(adapter_fn())

    margin_config = None
    if args.reconcile_state:
        margin_config = MarginConfig(
            warn_utilization_pct=args.margin_warn,
            critical_utilization_pct=args.margin_critical,
            target_utilization_pct=args.margin_target,
            warn_liq_distance_pct=args.liq_warn,
            critical_liq_distance_pct=args.liq_critical,
            auto_downsize=not args.no_auto_downsize,
            downsize_reduce_pct=args.downsize_pct,
        )

    all_issues = []
    instance_results = {}

    for adapter in adapters:
        if args.reconcile_state:
            result = _run_state_doctor(adapter, args.stale_minutes, margin_config)
            instance_results[result["instance_key"]] = result
            all_issues.extend(result.get("all_issues", []))
        else:
            result = _run_single(adapter, args.stale_minutes)
            instance_results[result["instance_key"]] = result
            all_issues.extend(result["issues"])

    actions = sum(1 for i in all_issues
                  if i.get("action") not in ("alert_only", "skipped_fetch_error"))
    downsizes = sum(r.get("downsizes_executed", 0)
                    for r in instance_results.values())

    output = {
        "status": "ok" if not any(
            i["level"] == "CRITICAL" for i in all_issues) else "critical",
        "time": now,
        "skill": args.skill,
        "mode": "state_doctor" if args.reconcile_state else "dsl_only",
        "instances": instance_results,
        "issues": all_issues,
        "issue_count": len(all_issues),
        "critical_count": sum(
            1 for i in all_issues if i["level"] == "CRITICAL"),
        "actions_taken": actions,
        "downsizes_executed": downsizes,
    }
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
