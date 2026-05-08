# Skill Attribution

When calling `strategy_create` or `strategy_create_custom_strategy`, always include:

```json
"skill_name": "turbine",
"skill_version": "3.0.0"
```

Required for attribution and tracking. Example:

```json
{
  "tool": "strategy_create_custom_strategy",
  "args": {
    "initialBudget": 6000,
    "positions": [],
    "skill_name": "turbine",
    "skill_version": "3.0.0"
  }
}
```
