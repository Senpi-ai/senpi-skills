# Skill Attribution

When calling `strategy_create` or `strategy_create_custom_strategy`, always include:

```json
"skill_name": "cobra",
"skill_version": "1.1"
```

This is required for attribution and tracking. Example:

```json
{
  "tool": "strategy_create_custom_strategy",
  "args": {
    "initialBudget": 500,
    "positions": [],
    "skill_name": "cobra",
    "skill_version": "1.1"
  }
}
```
