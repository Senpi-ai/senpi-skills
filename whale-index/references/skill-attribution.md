# Skill Attribution

When calling `strategy_create`, always include:

```json
"skill_name": "whale-index",
"skill_version": "1.0"
```

This is required for attribution and tracking. Example:

```json
{
  "tool": "strategy_create",
  "args": {
    "traderAddress": "0x...",
    "initialBudget": 500,
    "mirrorMultiplier": 1,
    "slippage": 3,
    "skill_name": "whale-index",
    "skill_version": "1.0"
  }
}
```
