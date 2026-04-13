# Arena Week Cycle

- **Cycle:** Thursday 00:00 UTC → Wednesday 23:59:59 UTC
- **Week 1 anchor:** 2026-03-26T00:00:00Z

## Computing Week Boundaries

Given the anchor date and cycle length (7 days), any week's boundaries can be
derived as:

  start = anchor + (n * 7 days)
  end   = start + 6 days 23h 59m 59s

Where n = number of complete weeks since anchor.

The `compute_week_boundaries(week_offset)` helper in
`scripts/analyze_user_trades_config.py` implements this. Pass `week_offset=0`
for the current week, `-1` for last week, etc.

## Example Boundaries

| Week | Start (UTC)              | End (UTC)                |
|------|--------------------------|--------------------------|
| 1    | 2026-03-26T00:00:00Z     | 2026-04-01T23:59:59Z     |
| 2    | 2026-04-02T00:00:00Z     | 2026-04-08T23:59:59Z     |
| 3    | 2026-04-09T00:00:00Z     | 2026-04-15T23:59:59Z     |
