# Skill Attribution

When calling `strategy_create` or `strategy_create_custom_strategy`, always include:

```json
"skill_name": "dsl-dynamic-stop-loss",
"skill_version": "5.3.1"
```

This is required for attribution and tracking. Example:

```json
{
  "tool": "strategy_create_custom_strategy",
  "args": {
    "initialBudget": 500,
    "positions": [],
    "skill_name": "dsl-dynamic-stop-loss",
    "skill_version": "5.3.1"
  }
}
```
