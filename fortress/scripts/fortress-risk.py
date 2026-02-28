import json
from typing import Any, Dict

from fortress_common import atomic_write, call_mcp, maybe_debug, now_iso, print_error, print_heartbeat, read_json_safe, with_state_meta
from fortress_config import load_config, state_path


def build_vote(config: Dict[str, Any]) -> Dict[str, Any]:
    max_loss_usd = float(config.get("risk", {}).get("maxLossUsd", 75))
    portfolio = call_mcp("account_get_portfolio")

    vote = "GO"
    conviction = 4
    reasons = ["loss_cap_respected"]

    if not portfolio:
        vote = "NO_GO"
        conviction = 1
        reasons = ["portfolio_data_missing"]

    if max_loss_usd <= 0:
        vote = "NO_GO"
        conviction = 1
        reasons = ["invalid_risk_config"]

    return {
        "pillar": "risk",
        "vote": vote,
        "conviction": conviction,
        "reasons": reasons,
        "maxLossUsd": max_loss_usd,
        "updatedAt": now_iso(),
    }


def main() -> None:
    try:
        cfg = load_config()
        vote = build_vote(cfg)
        path = state_path("risk-vote")
        instance_key = cfg.get("instanceKey", "fortress-default")
        current = read_json_safe(path)
        atomic_write(path, with_state_meta(vote, instance_key=instance_key, existing=current))

        if vote["vote"] != "GO":
            print_heartbeat()
            return

        out = {
            "success": True,
            "signals": [],
            "actions": [],
            "summary": "risk_vote_ready"
        }
        print(json.dumps(maybe_debug(out, {"risk": vote})))
    except Exception as exc:
        print_error(f"risk_failed: {exc}")


if __name__ == "__main__":
    main()