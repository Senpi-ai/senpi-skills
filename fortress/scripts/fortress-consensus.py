import json
from datetime import datetime, timezone
from typing import Any, Dict, List

from fortress_common import maybe_debug, print_error, print_heartbeat
from fortress_config import load_config, state_path


def read_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def load_votes(enabled: List[str]) -> Dict[str, Dict[str, Any]]:
    mapped = {}
    for pillar in enabled:
        mapped[pillar] = read_json(state_path(f"{pillar}-vote"))
    return mapped


def main() -> None:
    try:
        cfg = load_config()
        consensus_cfg = cfg.get("consensus", {})
        market_cfg = cfg.get("market", {})
        enabled = consensus_cfg.get("enabledPillars", ["oracle", "ta", "vol", "risk"])
        min_avg = float(consensus_cfg.get("minAverageConviction", 3.0))
        unanimous = bool(consensus_cfg.get("requiredUnanimous", True))

        votes = load_votes(enabled)
        vote_values = [v.get("vote", "NO_GO") for v in votes.values()]
        convictions = [float(v.get("conviction", 1)) for v in votes.values()]
        avg_conviction = sum(convictions) / max(len(convictions), 1)

        all_go = all(v == "GO" for v in vote_values)
        consensus_met = all_go if unanimous else vote_values.count("GO") >= max(1, len(vote_values) - 1)
        consensus_met = consensus_met and avg_conviction >= min_avg

        if not consensus_met:
            print_heartbeat()
            return

        result = {
            "success": True,
            "signals": [{
                "asset": market_cfg.get("asset", "HYPE"),
                "direction": market_cfg.get("direction", "SHORT"),
                "consensus": "GO"
            }],
            "actions": [{"type": "emit_trade_plan", "mode": "manual_confirm"}],
            "summary": "fortress_consensus_go",
            "updatedAt": datetime.now(timezone.utc).isoformat()
        }
        print(json.dumps(maybe_debug(result, {"votes": votes, "avgConviction": avg_conviction})))
    except FileNotFoundError:
        print_heartbeat()
    except Exception as exc:
        print_error(f"consensus_failed: {exc}")


if __name__ == "__main__":
    main()