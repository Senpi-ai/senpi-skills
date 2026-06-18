---
name: analyze-user-trades
description: >-
  Extracts actionable trading intelligence from a Senpi user's activity —
  what worked, what didn't, and what to replicate or avoid.
license: Apache-2.0
compatibility: Python 3.10+, mcporter
metadata:
  author: senpi
  version: "1.0.0"
  platform: senpi
  exchange: hyperliquid
---

## Invocation

| Argument        | When to use                           |
|-----------------|---------------------------------------|
| `--username`    | User provided a @username             |
| `--user-id`     | You already have the internal user ID |
| `--top-n`       | User asked for top N arena agents     |
| `--start-time`  | Custom time range start (ISO 8601)    |
| `--end-time`    | Custom time range end (ISO 8601)      |

`--username`, `--user-id`, and `--top-n` are mutually exclusive — pass exactly one.
Time range defaults to the current arena week if omitted.

```
python3 scripts/analyze_user_trades.py --username alice
python3 scripts/analyze_user_trades.py --top-n 10
```

## Processing Order

1. Determine arguments from the user's request
2. Run `python3 scripts/analyze_user_trades.py`
3. If `success=false`: surface the error to the user, stop
4. If `success=true`: apply Intelligence Extraction below, then present findings

## Intelligence Extraction

For each user in `results`, reason over `strategies`, `orders`, and `audit_log` to answer:

### What drove profit?
- Which coins, directions (LONG/SHORT), and leverage levels produced positive `realizedPnl`?
- Were winning trades confirmed by `ai_reasoning` in audit logs?
- Were entries well-timed (short hold time, tight entry-to-exit spread)?

### What caused losses?
- Which coins, directions, or leverage levels produced negative `realizedPnl`?
- Did `ai_reasoning` on losing trades show overconfidence or ignored risk signals?
- Did fees (`totalFees`) eat into marginal winners and turn them net negative?

### Patterns to replicate
2–4 concrete takeaways — e.g. "LONG BTC at 5–10x on confirmed SM momentum — 3/3 profitable"

### Patterns to avoid
2–4 concrete warnings — e.g. "High-leverage SHORT on altcoins during uptrend — 100% loss rate"

### Cross-user patterns (top-N only)
- Which coins/directions dominate top performers' winning trades?
- What do bottom performers share in their losing trades?

## Output Format

1. **Per-user summary** — 3–5 bullets: what worked, what didn't, standout trades
2. **Replicate** — numbered list of actionable patterns to copy
3. **Avoid** — numbered list of patterns that consistently lost
4. For top-N: **Cross-user edge** — shared winning patterns across the cohort

Do not recite the raw JSON. Lead with insight.
