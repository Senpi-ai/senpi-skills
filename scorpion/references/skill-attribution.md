# Skill Attribution

When calling `strategy_create` or `strategy_create_custom_strategy`, always include:

```json
"skill_name": "scorpion",
"skill_version": "5.0.0"
```

This is required for attribution and tracking. Example:

```json
{
  "tool": "strategy_create_custom_strategy",
  "args": {
    "initialBudget": 1000,
    "positions": [],
    "skill_name": "scorpion",
    "skill_version": "5.0.0"
  }
}
```
