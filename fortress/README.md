# Fortress Skill

Fortress is a multi-agent consensus trading skill for Hyperliquid via Senpi MCP.

## Included

- `SKILL.md` — full operating spec
- `scripts/` — pillar wrappers + consensus orchestrator
- `references/cron-templates.md` — cron mandates and model tiers
- `references/state-schema.md` — state contract and examples
- `registry-entry.json` — candidate marketplace registry object

## Local Run

```bash
python3 scripts/fortress-oracle.py
python3 scripts/fortress-ta.py
python3 scripts/fortress-vol.py
python3 scripts/fortress-risk.py
python3 scripts/fortress-consensus.py
```

When nothing actionable exists, output is:

```json
{"success": true, "heartbeat": "HEARTBEAT_OK"}
```

## Notes

- MCP-only calls via `mcporter`
- Atomic state writes with `os.replace()`
- Percentage convention: whole numbers (`5` = `5%`)