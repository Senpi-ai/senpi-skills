import json
from typing import Any, Dict

from fortress_common import atomic_write, call_mcp, maybe_debug, now_iso, print_error, print_heartbeat, read_json_safe, with_state_meta
from fortress_config import load_config, state_path


def build_vote(config: Dict[str, Any]) -> Dict[str, Any]:
    market = config.get("market", {})
    asset = market.get("asset", "HYPE")

    snapshot = call_mcp("leaderboard_get_markets")
    conviction = 3
    vote = "GO"
    reasons = ["leaderflow_stable"]

    if not snapshot:
        vote = "NO_GO"
        conviction = 1
        reasons = ["leaderboard_unavailable"]

    return {
        "pillar": "oracle",
        "asset": asset,
        "vote": vote,
        "conviction": conviction,
        "reasons": reasons,
        "updatedAt": now_iso(),
    }


def main() -> None:
    try:
        cfg = load_config()
        vote = build_vote(cfg)
        path = state_path("oracle-vote")
        instance_key = cfg.get("instanceKey", "fortress-default")
        current = read_json_safe(path)
        atomic_write(path, with_state_meta(vote, instance_key=instance_key, existing=current))

        if vote["vote"] != "GO":
            print_heartbeat()
            return

        out = {
            "success": True,
            "signals": [{"asset": vote["asset"], "direction": cfg.get("market", {}).get("direction", "SHORT")}],
            "actions": [],
            "summary": "oracle_vote_ready"
        }
        print(json.dumps(maybe_debug(out, {"oracle": vote})))
    except Exception as exc:
        print_error(f"oracle_failed: {exc}")


if __name__ == "__main__":
    main()