#!/usr/bin/env python3
"""
Return canonical order spec (orderType + feeOptimizedLimitOptions) for a given context.
Used by scanners/agents to stay aligned with fee-optimizer skill.
Usage: python3 get_order_spec.py <context> [--timeout SECONDS]
Context: entry | entry_resting | exit_tp | exit_sl | exit_emergency
Output: JSON to stdout.
"""
import argparse
import json
import sys

CONTEXTS = ("entry", "entry_resting", "exit_tp", "exit_sl", "exit_emergency")


def get_order_spec(context: str, execution_timeout_seconds: int | None = 45) -> dict:
    if context in ("entry", "exit_tp"):
        spec = {
            "orderType": "FEE_OPTIMIZED_LIMIT",
            "feeOptimizedLimitOptions": {"ensureExecutionAsTaker": True},
        }
        if execution_timeout_seconds is not None:
            spec["feeOptimizedLimitOptions"]["executionTimeoutSeconds"] = execution_timeout_seconds
        return spec
    if context == "entry_resting":
        return {"orderType": "FEE_OPTIMIZED_LIMIT"}
    if context in ("exit_sl", "exit_emergency"):
        return {"orderType": "MARKET"}
    raise ValueError(f"Unknown context: {context}. Use one of: {CONTEXTS}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Return order spec JSON for fee-optimizer contexts.")
    parser.add_argument("context", choices=CONTEXTS, help="Order context")
    parser.add_argument("--timeout", type=int, default=45, metavar="SECONDS", help="Execution timeout for entry/exit_tp (1-300)")
    args = parser.parse_args()
    timeout = args.timeout if 1 <= args.timeout <= 300 else 45
    timeout_for_spec = timeout if args.context in ("entry", "exit_tp") else None
    try:
        spec = get_order_spec(args.context, timeout_for_spec)
        print(json.dumps(spec))
        return 0
    except Exception as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
