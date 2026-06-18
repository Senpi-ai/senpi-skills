# Output Schema

## Top-level

| Field      | Type   | Notes                                      |
|------------|--------|--------------------------------------------|
| success    | bool   | Always present                             |
| startTime  | string | ISO 8601                                   |
| endTime    | string | ISO 8601                                   |
| results    | array  | One entry per analyzed user                |
| error      | string | Present only on failure                    |
| actionable | bool   | Present only on failure                    |
| debug      | object | Present only when ANALYZE_USER_TRADES_VERBOSE=1 |

## Per-user result

| Field         | Type         | Notes                                              |
|---------------|--------------|----------------------------------------------------|
| senpiUserName | string\|null | null when looked up by user ID directly            |
| senpiUserId   | string       |                                                    |
| rank          | int\|null    | null when not resolved via arena_leaderboard       |
| roePct        | string\|null | null when not resolved via arena_leaderboard       |
| totalPnl      | string\|null | null when not resolved via arena_leaderboard       |
| strategies    | array        | See strategy schema below                          |

## Per-strategy

| Field       | Type         | Notes                                              |
|-------------|--------------|---------------------------------------------------|
| strategyId  | string       |                                                    |
| address     | string       | Wallet address                                     |
| status      | string       | ACTIVE \| CLOSED                                   |
| skillName   | string\|null | null if not created by a skill                     |
| createdAt   | string       | ISO 8601                                           |
| orders      | array        | Closed positions filtered to time range            |
| audit_log   | array        | Audit entries filtered to time range               |

## Per-order (closed position)

| Field       | Type         | Notes                          |
|-------------|--------------|--------------------------------|
| coin        | string       |                                |
| entryPx     | string       |                                |
| exitPx      | string       |                                |
| leverage    | string       |                                |
| openTime    | string       | ISO 8601                       |
| closeTime   | string       | ISO 8601                       |
| szi         | string       | Signed size                    |
| realizedPnl | string       |                                |
| totalFees   | string       |                                |

## Per-audit-log entry

| Field        | Type         | Notes                          |
|--------------|--------------|--------------------------------|
| tool         | string       | MCP tool name called           |
| ai_reasoning | string\|null | AI reasoning string if present |
| timestamp    | string       | ISO 8601                       |

## Error response

```json
{
  "success": false,
  "error": "No Senpi user found with username 'unknownuser'",
  "actionable": false
}
```
